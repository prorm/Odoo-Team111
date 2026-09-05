import { fetchApi } from './api-client';
import { reachability } from './reachability';
import {
  addConflict, deleteCachedEntity, getConflicts, getCursor, getPendingOutbox,
  makeCacheEntry, putCachedEntity, removeOutboxEntry, markOutboxConflict, removeConflict, setCursor,
  updateOutboxEntry, type OutboxMutation,
} from './offline-db';

/** Entity types this frontend build syncs — mirrors the backend's
 * app/services/sync_entities.py registrations. A new entry here is the only
 * change needed to start syncing another registered entity type. */
// Must match what app/services/sync_entities.py registers on the backend.
// Empty until Phase 8, which registers 'attendance' and 'time_off_request'
// (Architecture §8.3). The engine below is entity-agnostic and needs no other
// change when they are added.
export const SYNCED_ENTITY_TYPES: string[] = [];

interface PushResultItem {
  client_mutation_id: string;
  outcome: 'applied' | 'conflict' | 'rejected';
  entity_type: string;
  entity_id: string | null;
  version: number | null;
  error: { code: string; message: string; details?: Record<string, unknown> } | null;
}

interface PullDelta {
  entity_type: string;
  public_id: string;
  op: 'CREATE' | 'UPDATE' | 'DELETE';
  version: number;
  updated_at: string;
  data: Record<string, unknown> | null;
}

let syncing = false;
type SyncListener = () => void;
const listeners = new Set<SyncListener>();

export function onSyncSettled(listener: SyncListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify() {
  for (const l of listeners) l();
}

/** Sends one batch and reconciles each result into the outbox/cache/conflicts
 * stores. Returns the local ids of any CREATE that got a real public_id
 * assigned, keyed by the local placeholder id it replaces — `pushOutbox`
 * uses this to unblock sibling mutations that were still pointing at it. */
async function sendBatch(pending: OutboxMutation[]): Promise<Map<string, { entityId: string; version: number }>> {
  const mutations = pending.map((m) => ({
    client_mutation_id: m.client_mutation_id,
    entity_type: m.entity_type,
    entity_id: m.entity_id,
    op: m.op,
    known_version: m.known_version,
    payload: m.payload,
  }));

  const response = await fetchApi<{ results: PushResultItem[] }>('/sync/push', {
    method: 'POST',
    body: JSON.stringify({ mutations }),
  });

  const byClientId = new Map(pending.map((m) => [m.client_mutation_id, m]));
  const resolvedLocalIds = new Map<string, { entityId: string; version: number }>();

  for (const result of response.results) {
    const original = byClientId.get(result.client_mutation_id);
    if (!original || original.id === undefined) continue;

    if (result.outcome === 'applied') {
      // A CREATE queued offline was cached under a local placeholder id
      // (original.local_temp_id) — now that the server has assigned the
      // real public_id, migrate the cache row so reads key off the id every
      // other client will ever see.
      if (original.op === 'CREATE' && original.local_temp_id && result.entity_id) {
        await deleteCachedEntity(original.entity_type, original.local_temp_id);
        resolvedLocalIds.set(original.local_temp_id, { entityId: result.entity_id, version: result.version! });
      }
      if (result.entity_id && result.version !== null && original.op !== 'DELETE') {
        await putCachedEntity(
          makeCacheEntry(original.entity_type, result.entity_id, result.version, new Date().toISOString(), original.payload ?? {}),
        );
      }
      if (original.op === 'DELETE' && result.entity_id) {
        await deleteCachedEntity(original.entity_type, result.entity_id);
      }
      await removeOutboxEntry(original.id);
    } else if (result.outcome === 'conflict') {
      await markOutboxConflict(original.id);
      const details = (result.error?.details ?? {}) as Record<string, unknown>;
      await addConflict({
        client_mutation_id: original.client_mutation_id,
        entity_type: original.entity_type,
        entity_id: original.entity_id ?? (result.entity_id as string),
        mine_payload: original.payload,
        op: original.op as 'UPDATE' | 'DELETE',
        known_version: original.known_version,
        current_version: (details.current_version as number) ?? 0,
        current_state: (details.current_state as Record<string, unknown>) ?? {},
        created_at: new Date().toISOString(),
      });
      // Deliberately NOT overwritten locally (offline-sync requirement):
      // the row stays exactly as this client last wrote it until the user
      // resolves the conflict via the modal (Keep Mine / Overwrite).
    } else {
      // rejected — a genuine validation/constraint failure, not a version
      // race. Nothing local to reconcile it against; drop it rather than
      // retry forever, but keep it visible for a human via console for now.
      console.error('sync/push rejected mutation', original, result.error);
      await removeOutboxEntry(original.id);
    }
  }

  return resolvedLocalIds;
}

async function pushOutbox(): Promise<void> {
  // Chained-dependency resolution: an UPDATE/DELETE queued offline against a
  // row that was ITSELF created offline in the same session still points at
  // that CREATE's local placeholder id (e.g. "local:<uuid>") until the CREATE
  // round-trips and the server assigns a real public_id — the client can't
  // know that id before asking. Push whatever doesn't depend on an unresolved
  // local id first, resolve any siblings waiting on what just got created,
  // and repeat until nothing more can be unblocked locally.
  for (;;) {
    const pending = await getPendingOutbox();
    if (pending.length === 0) return;

    const sendable = pending.filter((m) => !(m.entity_id && m.entity_id.startsWith('local:')));
    if (sendable.length === 0) return; // remaining entries depend on a CREATE that hasn't resolved (and won't, this run)

    const resolved = await sendBatch(sendable);
    if (resolved.size === 0) return; // nothing left to unblock — done for this run

    const stillPending = await getPendingOutbox();
    for (const entry of stillPending) {
      const match = entry.entity_id ? resolved.get(entry.entity_id) : undefined;
      if (match && entry.id !== undefined) {
        entry.entity_id = match.entityId;
        entry.known_version = match.version;
        await updateOutboxEntry(entry);
      }
    }
  }
}

async function pullUpdates(): Promise<void> {
  let cursor = await getCursor();
  let hasMore = true;
  while (hasMore) {
    const qs = new URLSearchParams({ entity_types: SYNCED_ENTITY_TYPES.join(',') });
    if (cursor) qs.set('since', cursor);
    const response = await fetchApi<{ cursor: string; has_more: boolean; entities: PullDelta[] }>(
      `/sync/pull?${qs.toString()}`,
    );

    for (const delta of response.entities) {
      if (delta.op === 'DELETE') {
        await deleteCachedEntity(delta.entity_type, delta.public_id);
      } else if (delta.data) {
        await putCachedEntity(makeCacheEntry(delta.entity_type, delta.public_id, delta.version, delta.updated_at, delta.data));
      }
    }

    cursor = response.cursor;
    await setCursor(cursor);
    hasMore = response.has_more;
  }
}

export async function runSync(): Promise<void> {
  if (syncing) return;
  syncing = true;
  try {
    const online = await reachability.checkNow();
    if (!online) return;
    await pushOutbox();
    await pullUpdates();
  } finally {
    syncing = false;
    notify();
  }
}

export async function resolveConflictKeepMine(clientMutationId: string): Promise<void> {
  const conflicts = await getConflicts();
  const conflict = conflicts.find((c) => c.client_mutation_id === clientMutationId);
  if (!conflict) return;

  const newClientMutationId = `${clientMutationId}-retry-${Date.now()}`;
  await fetchApi('/sync/push', {
    method: 'POST',
    body: JSON.stringify({
      mutations: [{
        client_mutation_id: newClientMutationId,
        entity_type: conflict.entity_type,
        entity_id: conflict.entity_id,
        op: conflict.op,
        known_version: conflict.current_version,
        payload: conflict.mine_payload,
      }],
    }),
  });
  await removeConflict(clientMutationId);
  await runSync();
}

export async function resolveConflictOverwrite(clientMutationId: string): Promise<void> {
  const conflicts = await getConflicts();
  const conflict = conflicts.find((c) => c.client_mutation_id === clientMutationId);
  if (!conflict) return;
  await putCachedEntity(
    makeCacheEntry(conflict.entity_type, conflict.entity_id, conflict.current_version, new Date().toISOString(), conflict.current_state),
  );
  await removeConflict(clientMutationId);
  notify();
}

// Reconnect flow (offline-sync requirement): push the outbox first, then
// pull — never the other way round, or a stale local write could look like
// it "won" against a pull that ran before it was ever sent.
reachability.subscribe((online) => {
  if (online) void runSync();
});
