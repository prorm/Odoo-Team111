import { useCallback, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  deleteCachedEntity, enqueueOutbox, getCachedEntities, getPendingOutbox, makeCacheEntry,
  putCachedEntity, type OutboxMutation,
} from '@/lib/offline-db';
import { fetchApi } from '@/lib/api-client';
import { reachability } from '@/lib/reachability';
import { onSyncSettled, runSync } from '@/lib/sync-engine';

/**
 * Generic offline-capable CRUD for ANY entity_type registered on both sides
 * (app/services/sync_registry.py on the backend, SYNCED_ENTITY_TYPES in
 * src/lib/sync-engine.ts here) — no entity-specific logic lives in this file.
 * Every write: (1) updates the local IndexedDB cache optimistically so the UI
 * reflects it instantly, (2) enqueues it to the outbox, (3) if currently
 * online, immediately asks the sync engine to flush the outbox — the ONLY
 * difference between "online" and "offline" here is how soon that flush
 * happens, not which code path runs. That is deliberate: it means there is
 * exactly one way a mutation reaches the server (POST /sync/push), online or
 * offline, rather than a fast-path REST call for the online case and a
 * second, parallel offline-queue implementation for the other.
 */
export function useOfflineEntities<T = Record<string, unknown>>(entityType: string) {
  return useQuery({
    queryKey: ['offline', entityType],
    queryFn: async () => {
      const rows = await getCachedEntities(entityType);
      return rows
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
        .map((row) => ({ ...(row.data as T), _publicId: row.public_id, _version: row.version }));
    },
    // IndexedDB is the source of truth for this query; nothing else should
    // silently refetch it over the network.
    staleTime: Infinity,
    // TanStack Query's default networkMode: 'online' pauses ALL queries —
    // including this one — the instant navigator.onLine goes false, on the
    // assumption every queryFn hits the network. This one only reads
    // IndexedDB, so pausing it would make the whole point of "offline reads
    // still work" silently false: the UI would freeze on stale data despite
    // a perfectly good local write sitting in the cache right under it.
    networkMode: 'always',
  });
}

function newClientMutationId(): string {
  return crypto.randomUUID();
}

export function useOfflineMutation(entityType: string) {
  const queryClient = useQueryClient();

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['offline', entityType] });
    // The outbox view too, or a mutation queued with no network would sit
    // there invisibly: `usePendingOfflineMutations` reads IndexedDB with
    // staleTime Infinity, so nothing else would ever make it re-read.
    queryClient.invalidateQueries({ queryKey: ['offline-pending', entityType] });
  }, [queryClient, entityType]);

  const flushIfOnline = useCallback(() => {
    if (reachability.getSnapshot()) void runSync().then(invalidate);
  }, [invalidate]);

  const create = useCallback(
    async (payload: Record<string, unknown>) => {
      const localId = `local:${newClientMutationId()}`;
      const clientMutationId = newClientMutationId();
      await putCachedEntity(makeCacheEntry(entityType, localId, 0, new Date().toISOString(), payload));
      await enqueueOutbox({
        client_mutation_id: clientMutationId,
        entity_type: entityType,
        entity_id: null,
        op: 'CREATE',
        known_version: null,
        payload,
        created_at: new Date().toISOString(),
        status: 'pending',
        local_temp_id: localId,
      });
      invalidate();
      flushIfOnline();
      return localId;
    },
    [entityType, invalidate, flushIfOnline],
  );

  const update = useCallback(
    async (publicId: string, knownVersion: number, payload: Record<string, unknown>) => {
      await putCachedEntity(makeCacheEntry(entityType, publicId, knownVersion, new Date().toISOString(), payload));
      await enqueueOutbox({
        client_mutation_id: newClientMutationId(),
        entity_type: entityType,
        entity_id: publicId,
        op: 'UPDATE',
        known_version: knownVersion,
        payload,
        created_at: new Date().toISOString(),
        status: 'pending',
      });
      invalidate();
      flushIfOnline();
    },
    [entityType, invalidate, flushIfOnline],
  );

  const remove = useCallback(
    async (publicId: string, knownVersion: number) => {
      await deleteCachedEntity(entityType, publicId);
      await enqueueOutbox({
        client_mutation_id: newClientMutationId(),
        entity_type: entityType,
        entity_id: publicId,
        op: 'DELETE',
        known_version: knownVersion,
        payload: null,
        created_at: new Date().toISOString(),
        status: 'pending',
      });
      invalidate();
      flushIfOnline();
    },
    [entityType, invalidate, flushIfOnline],
  );

  return { create, update, remove };
}


/**
 * The mutations for one entity type that are queued and not yet accepted by
 * the server. This is what makes offline work VISIBLE: a check-in recorded
 * with no network has to appear somewhere, or the person taps the button
 * again and the honest answer to "did that save?" is a shrug.
 *
 * Reads the existing outbox store; it adds no state of its own. It re-reads
 * whenever the sync engine settles, so a row disappears from "waiting" at the
 * moment the server accepts it rather than on the next poll.
 */
export function usePendingOfflineMutations(entityType: string) {
  const queryClient = useQueryClient();

  useEffect(() => onSyncSettled(() => {
    queryClient.invalidateQueries({ queryKey: ['offline-pending', entityType] });
    queryClient.invalidateQueries({ queryKey: ['offline', entityType] });
  }), [queryClient, entityType]);

  return useQuery<OutboxMutation[]>({
    queryKey: ['offline-pending', entityType],
    queryFn: async () => (await getPendingOutbox()).filter((m) => m.entity_type === entityType),
    staleTime: Infinity,
    // Same reason as useOfflineEntities: this reads IndexedDB, not the
    // network, so TanStack's default of pausing queries while offline would
    // hide exactly the rows it exists to show.
    networkMode: 'always',
  });
}


/**
 * Reference data a form needs in order to be usable offline, persisted in the
 * SAME IndexedDB cache store the sync engine uses.
 *
 * The leave-request form cannot be filled in without the list of leave types,
 * and that list is a live read (`/time-off-types/lookup`). With no network the
 * dropdown is empty, and "you may submit a request offline" is then false in
 * the only way that matters.
 *
 * This is NOT a third syncable entity. Architecture §8.3 registers exactly two
 * (`attendance`, `time_off_request`) and `SYNCED_ENTITY_TYPES` still names
 * exactly those; `/sync/pull` is never asked for this. It is a read-through
 * cache of reference data under its own key, written whenever the online read
 * succeeds and read back when it fails. Types are configuration, not somebody's
 * records — there is nothing here to conflict, reconcile or push.
 */
export function useOfflineReferenceList<T extends { id: string }>(
  cacheKey: string,
  path: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: ['offline-reference', cacheKey],
    enabled: options.enabled ?? true,
    // Reads the network first and IndexedDB second; pausing it while offline
    // would skip the fallback that is the entire point.
    networkMode: 'always',
    queryFn: async (): Promise<{ items: T[] }> => {
      try {
        const fresh = await fetchApi<{ items: T[] }>(path);
        await Promise.all(
          fresh.items.map((item) =>
            putCachedEntity(
              makeCacheEntry(cacheKey, item.id, 0, new Date().toISOString(),
                item as unknown as Record<string, unknown>),
            ),
          ),
        );
        return fresh;
      } catch (error) {
        const cached = await getCachedEntities(cacheKey);
        // Nothing cached and no network: re-raise, so the form shows the real
        // failure rather than an empty dropdown with no explanation.
        if (cached.length === 0) throw error;
        return { items: cached.map((row) => row.data as unknown as T) };
      }
    },
  });
}
