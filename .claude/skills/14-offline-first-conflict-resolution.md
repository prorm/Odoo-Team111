# Offline-First Conflict Resolution

## Why this matters
Offline capability is explicitly called out as a "nice to have" that judges value — most teams skip it entirely, so even a working, honestly-scoped version is a differentiator. It also signals resilience thinking: a real ERP client (warehouse floor, field technician) can't assume constant connectivity.

## Architecture
```
React app
  ├── IndexedDB cache — mirrors server data for offline reads
  ├── Mutation queue (IndexedDB) — records writes made while offline
  ├── Network status listener — detects online/offline transitions
  └── Sync service
        ├── On reconnect: drains the mutation queue, one at a time, in order
        ├── Detects conflicts (e.g. resource was booked by someone else while offline)
        ├── Applies last-write-wins by default, OR
        └── Surfaces a visible conflict banner with a manual override option
```
The core promise: the user can keep working while offline, sees clearly what's unsynced, and is never silently lied to about whether their action actually went through.

## Step 1 — IndexedDB wrapper (using `idb` for a clean promise-based API)
```bash
npm install idb
```
```js
// src/offline/db.js
import { openDB } from 'idb';

const DB_NAME = 'app-offline-cache';
const DB_VERSION = 1;

export async function getDB() {
  return openDB(DB_NAME, DB_VERSION, {
    upgrade(db) {
      db.createObjectStore('bookings', { keyPath: 'id' });
      db.createObjectStore('mutationQueue', { keyPath: 'queueId', autoIncrement: true });
    },
  });
}
```

## Step 2 — Caching GET responses for offline reads
```js
// src/offline/cachedFetch.js
import { getDB } from './db';
import { api } from '../lib/api';

export async function getCachedBookings() {
  if (navigator.onLine) {
    try {
      const { data } = await api.get('/bookings');
      const db = await getDB();
      const tx = db.transaction('bookings', 'readwrite');
      await Promise.all(data.map(b => tx.store.put(b)));
      await tx.done;
      return { data, fromCache: false };
    } catch {
      // fall through to cache on network error even if navigator.onLine lied (common on flaky wifi)
    }
  }
  const db = await getDB();
  const cached = await db.getAll('bookings');
  return { data: cached, fromCache: true };
}
```

## Step 3 — Queueing mutations made while offline
```js
// src/offline/mutationQueue.js
import { getDB } from './db';

export async function queueMutation({ method, url, body }) {
  const db = await getDB();
  const queueId = await db.add('mutationQueue', {
    method, url, body,
    clientTimestamp: Date.now(),
    localId: crypto.randomUUID(), // lets the UI show an optimistic row before the server assigns a real ID
  });
  return queueId;
}

export async function getQueuedMutations() {
  const db = await getDB();
  return db.getAll('mutationQueue');
}

export async function removeQueuedMutation(queueId) {
  const db = await getDB();
  await db.delete('mutationQueue', queueId);
}
```

## Step 4 — Network status hook
```jsx
// src/hooks/useOnlineStatus.js
import { useState, useEffect } from 'react';

export function useOnlineStatus() {
  const [isOnline, setIsOnline] = useState(navigator.onLine);

  useEffect(() => {
    const goOnline = () => setIsOnline(true);
    const goOffline = () => setIsOnline(false);
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);
    return () => {
      window.removeEventListener('online', goOnline);
      window.removeEventListener('offline', goOffline);
    };
  }, []);

  return isOnline;
}
```

## Step 5 — Sync service: draining the queue on reconnect
```js
// src/offline/syncService.js
import { api } from '../lib/api';
import { getQueuedMutations, removeQueuedMutation } from './mutationQueue';

export async function syncQueuedMutations(onConflict) {
  const queued = await getQueuedMutations();
  const results = [];

  for (const mutation of queued) { // sequential, in original order — not Promise.all
    try {
      const { data } = await api.request({
        method: mutation.method,
        url: mutation.url,
        data: mutation.body,
        headers: { 'Idempotency-Key': mutation.localId }, // reuses the Idempotent API Design skill directly
      });
      await removeQueuedMutation(mutation.queueId);
      results.push({ status: 'synced', localId: mutation.localId, serverData: data });
    } catch (err) {
      if (err.response?.status === 409) {
        // genuine conflict — e.g. slot was booked by someone else while this client was offline
        results.push({ status: 'conflict', localId: mutation.localId, mutation, serverError: err.response.data });
        onConflict?.(mutation, err.response.data); // let the UI decide: last-write-wins or manual resolve
      } else {
        // leave it queued to retry on the next sync attempt (e.g. transient 500)
        results.push({ status: 'retry-later', localId: mutation.localId });
      }
    }
  }
  return results;
}
```
Sequential processing (not parallel) matters here — mutations queued offline often have a real order dependency (e.g. create then update the same record), and syncing them out of order can produce a different final state than the user intended.

## Step 6 — React integration: offline banner + conflict resolution UI
```jsx
// src/offline/OfflineProvider.jsx
import { createContext, useContext, useEffect, useState } from 'react';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { syncQueuedMutations } from './syncService';
import { queueMutation } from './mutationQueue';

const OfflineContext = createContext(null);

export function OfflineProvider({ children }) {
  const isOnline = useOnlineStatus();
  const [conflicts, setConflicts] = useState([]);
  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    if (!isOnline) return;
    syncQueuedMutations((mutation, serverError) => {
      setConflicts(c => [...c, { mutation, serverError }]);
    }).then(results => {
      setPendingCount(results.filter(r => r.status === 'retry-later').length);
    });
  }, [isOnline]);

  const resolveConflict = async (conflict, strategy) => {
    if (strategy === 'discard') {
      // user's offline action loses to the server state — the common, safe default
      setConflicts(c => c.filter(x => x !== conflict));
    } else if (strategy === 'retry') {
      // last-write-wins: force the client's version through as a fresh, non-conflicting mutation
      await api.request({ method: conflict.mutation.method, url: conflict.mutation.url, data: conflict.mutation.body });
      setConflicts(c => c.filter(x => x !== conflict));
    }
  };

  return (
    <OfflineContext.Provider value={{ isOnline, pendingCount, conflicts, resolveConflict, queueMutation }}>
      {!isOnline && <OfflineBanner />}
      {conflicts.length > 0 && <ConflictBanner conflicts={conflicts} onResolve={resolveConflict} />}
      {children}
    </OfflineContext.Provider>
  );
}

function OfflineBanner() {
  return (
    <div className="bg-amber-500 text-white text-sm text-center py-2">
      You're offline — changes will sync automatically when you reconnect
    </div>
  );
}

function ConflictBanner({ conflicts, onResolve }) {
  return (
    <div className="bg-red-50 border-b border-red-200 p-3 text-sm">
      {conflicts.map((c, i) => (
        <div key={i} className="flex items-center justify-between">
          <span>A change made while offline conflicts with a newer server update.</span>
          <div className="flex gap-2">
            <button onClick={() => onResolve(c, 'discard')} className="text-slate-600 underline">Discard mine</button>
            <button onClick={() => onResolve(c, 'retry')} className="text-blue-600 underline">Keep mine</button>
          </div>
        </div>
      ))}
    </div>
  );
}

export const useOffline = () => useContext(OfflineContext);
```

## Step 7 — Wiring a mutation to go offline-aware
```jsx
// src/hooks/useOfflineAwareMutation.js
import { useOnlineStatus } from './useOnlineStatus';
import { useOffline } from '../offline/OfflineProvider';
import { api } from '../lib/api';

export function useOfflineAwareBookingCreate() {
  const isOnline = useOnlineStatus();
  const { queueMutation } = useOffline();

  return async (bookingData) => {
    if (isOnline) {
      return api.post('/bookings', bookingData);
    }
    await queueMutation({ method: 'POST', url: '/bookings', body: bookingData });
    return { queued: true, localId: crypto.randomUUID() }; // optimistic response, UI shows it as "pending sync"
  };
}
```

## Enterprise best practices
- Never claim a write "succeeded" while offline — the UI must visibly mark queued items as pending (e.g. a small clock icon + "syncing when back online"), not indistinguishable from a confirmed record.
- Reuse the **Idempotent API Design** skill's `Idempotency-Key` header on every synced mutation — a sync retry after a dropped connection mid-sync must not double-create.
- Default conflict strategy should be the **safer** one for your domain — for bookings, "discard mine, respect the server's newer state" is usually safer than blindly overwriting someone else's confirmed booking.

## Checklist before moving on
- [ ] Reads fall back to IndexedDB cache when offline, without throwing
- [ ] Mutations queue locally when offline and are visibly marked as pending in the UI
- [ ] Sync drains the queue sequentially, in original order, on reconnect
- [ ] Conflicts (`409`) are surfaced to the user, never silently dropped or silently overwritten
- [ ] Tested with actual dev-tools "offline" network throttling, not just assumed to work

## Common mistakes to avoid
- Trusting `navigator.onLine` alone — it can report `true` on a technically-connected-but-unusable network; always fall back to cache on an actual fetch failure too, as shown in Step 2.
- Syncing queued mutations with `Promise.all` — loses ordering guarantees for dependent writes.
- Silently discarding conflicts instead of surfacing them — a user who thinks their offline booking succeeded, only to find it silently vanished, is a worse experience than staying online-only.

## Integration with the rest of the stack
- Sync requests reuse the **Idempotent API Design** skill's `Idempotency-Key` mechanism directly.
- Conflicts surfaced here are typically `409`s from the **State Machine + Constraint Generator**'s exclusion constraint — the same conflict-prevention guarantee, now handled gracefully on the client instead of just rejected outright.
