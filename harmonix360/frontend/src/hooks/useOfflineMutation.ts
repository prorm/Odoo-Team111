import { useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  deleteCachedEntity, enqueueOutbox, getCachedEntities, makeCacheEntry, putCachedEntity,
} from '@/lib/offline-db';
import { reachability } from '@/lib/reachability';
import { runSync } from '@/lib/sync-engine';

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
