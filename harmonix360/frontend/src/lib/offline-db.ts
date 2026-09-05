import { openDB, type DBSchema, type IDBPDatabase } from 'idb';

/**
 * Generic offline store for ANY entity_type the backend registers in
 * app/services/sync_registry.py — this file has no entity-specific code,
 * the same separation the backend keeps between app/services/sync.py and the
 * concrete entities it syncs. A new entity type needs zero changes here; it
 * just starts showing up as rows keyed by its own entity_type string.
 */

export interface CachedEntity {
  key: string; // `${entity_type}:${public_id}`
  entity_type: string;
  public_id: string;
  version: number;
  updated_at: string;
  data: Record<string, unknown>;
}

export type OutboxStatus = 'pending' | 'sending' | 'conflict';

export interface OutboxMutation {
  id?: number; // autoincrement
  client_mutation_id: string;
  entity_type: string;
  entity_id: string | null;
  op: 'CREATE' | 'UPDATE' | 'DELETE';
  known_version: number | null;
  payload: Record<string, unknown> | null;
  created_at: string;
  status: OutboxStatus;
  // Set locally once a CREATE is applied and the server hands back a real
  // public_id — lets a later mutation queued offline against the same
  // not-yet-synced row (e.g. edit right after create) resolve the right id.
  local_temp_id?: string;
}

export interface ConflictRecord {
  client_mutation_id: string;
  entity_type: string;
  entity_id: string;
  mine_payload: Record<string, unknown> | null;
  op: 'UPDATE' | 'DELETE';
  known_version: number | null;
  current_version: number;
  current_state: Record<string, unknown>;
  created_at: string;
}

interface Harmonix360OfflineDB extends DBSchema {
  cache: {
    key: string;
    value: CachedEntity;
    indexes: { 'by-entity-type': string };
  };
  outbox: {
    key: number;
    value: OutboxMutation;
    indexes: { 'by-status': string };
  };
  conflicts: {
    key: string; // client_mutation_id
    value: ConflictRecord;
  };
  meta: {
    key: string;
    value: string;
  };
}

const DB_NAME = 'harmonix360-offline';
const DB_VERSION = 1;

let dbPromise: Promise<IDBPDatabase<Harmonix360OfflineDB>> | null = null;

export function getOfflineDB(): Promise<IDBPDatabase<Harmonix360OfflineDB>> {
  if (!dbPromise) {
    dbPromise = openDB<Harmonix360OfflineDB>(DB_NAME, DB_VERSION, {
      upgrade(db) {
        const cache = db.createObjectStore('cache', { keyPath: 'key' });
        cache.createIndex('by-entity-type', 'entity_type');

        const outbox = db.createObjectStore('outbox', { keyPath: 'id', autoIncrement: true });
        outbox.createIndex('by-status', 'status');

        db.createObjectStore('conflicts', { keyPath: 'client_mutation_id' });
        db.createObjectStore('meta');
      },
    });
  }
  return dbPromise;
}

function cacheKey(entityType: string, publicId: string): string {
  return `${entityType}:${publicId}`;
}

export async function getCachedEntities(entityType: string): Promise<CachedEntity[]> {
  const db = await getOfflineDB();
  return db.getAllFromIndex('cache', 'by-entity-type', entityType);
}

export async function putCachedEntity(entry: CachedEntity): Promise<void> {
  const db = await getOfflineDB();
  await db.put('cache', entry);
}

export async function deleteCachedEntity(entityType: string, publicId: string): Promise<void> {
  const db = await getOfflineDB();
  await db.delete('cache', cacheKey(entityType, publicId));
}

export function makeCacheEntry(
  entityType: string,
  publicId: string,
  version: number,
  updatedAt: string,
  data: Record<string, unknown>,
): CachedEntity {
  return { key: cacheKey(entityType, publicId), entity_type: entityType, public_id: publicId, version, updated_at: updatedAt, data };
}

export async function enqueueOutbox(mutation: Omit<OutboxMutation, 'id'>): Promise<number> {
  const db = await getOfflineDB();
  return db.add('outbox', mutation as OutboxMutation);
}

export async function getPendingOutbox(): Promise<OutboxMutation[]> {
  const db = await getOfflineDB();
  return db.getAllFromIndex('outbox', 'by-status', 'pending');
}

export async function removeOutboxEntry(id: number): Promise<void> {
  const db = await getOfflineDB();
  await db.delete('outbox', id);
}

export async function updateOutboxEntry(entry: OutboxMutation): Promise<void> {
  const db = await getOfflineDB();
  await db.put('outbox', entry);
}

export async function markOutboxConflict(id: number): Promise<void> {
  const db = await getOfflineDB();
  const entry = await db.get('outbox', id);
  if (entry) {
    entry.status = 'conflict';
    await db.put('outbox', entry);
  }
}

export async function addConflict(record: ConflictRecord): Promise<void> {
  const db = await getOfflineDB();
  await db.put('conflicts', record);
}

export async function getConflicts(): Promise<ConflictRecord[]> {
  const db = await getOfflineDB();
  return db.getAll('conflicts');
}

export async function removeConflict(clientMutationId: string): Promise<void> {
  const db = await getOfflineDB();
  await db.delete('conflicts', clientMutationId);
  const outbox = await db.getAll('outbox');
  const match = outbox.find((o) => o.client_mutation_id === clientMutationId);
  if (match?.id !== undefined) {
    await db.delete('outbox', match.id);
  }
}

export async function getCursor(): Promise<string | undefined> {
  const db = await getOfflineDB();
  return db.get('meta', 'cursor');
}

export async function setCursor(cursor: string): Promise<void> {
  const db = await getOfflineDB();
  await db.put('meta', cursor, 'cursor');
}
