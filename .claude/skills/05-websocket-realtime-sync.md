# WebSocket Real-Time Sync

## Why this matters
Judges call out real-time state management as a differentiator beyond standard CRUD. An admin approving a request should instantly update every affected dashboard with no refresh. This is a high "wow-per-effort" feature — cheap to build correctly, very visible in a demo.

## Architecture
```
Express + Socket.IO server
  └── auth middleware (same JWT as REST)
  └── rooms: role:<ROLE>, user:<id>, resource:<id>
  └── service layer emits AFTER successful DB write, never from the route handler directly

React client
  └── SocketProvider (single connection, authenticated once)
  └── useSocketEvent(event, handler) hook
  └── optimistic update on send, reconciled/rolled back on ack or error
```
Emitting from the **service layer**, right after a successful transaction commit, guarantees the event only fires on real state changes — never on a request that later fails validation or hits a DB conflict.

## Step 1 — Server setup with authenticated rooms
```js
// src/realtime/socket.js
const { Server } = require('socket.io');
const jwt = require('jsonwebtoken');

let io;

function initSocket(httpServer) {
  io = new Server(httpServer, {
    cors: { origin: process.env.CLIENT_URL, credentials: true },
  });

  io.use((socket, next) => {
    const token = socket.handshake.auth?.token;
    if (!token) return next(new Error('unauthorized'));
    try {
      const payload = jwt.verify(token, process.env.JWT_ACCESS_SECRET);
      socket.user = { id: payload.sub, role: payload.role };
      next();
    } catch {
      next(new Error('unauthorized'));
    }
  });

  io.on('connection', (socket) => {
    socket.join(`role:${socket.user.role}`);
    socket.join(`user:${socket.user.id}`);

    socket.on('resource:subscribe', (resourceId) => {
      socket.join(`resource:${resourceId}`); // e.g. admin watching a live booking board
    });
    socket.on('disconnect', () => {});
  });

  return io;
}

function getIO() {
  if (!io) throw new Error('Socket.IO not initialized — call initSocket first');
  return io;
}

module.exports = { initSocket, getIO };
```
```js
// server.js
const http = require('http');
const app = require('./app');
const { initSocket } = require('./src/realtime/socket');

const server = http.createServer(app);
initSocket(server);
server.listen(process.env.PORT || 4000);
```

## Step 2 — Emitting from the service layer
```js
// src/services/booking.service.js (extends the State Machine skill's service)
const { getIO } = require('../realtime/socket');

async function transitionBooking(bookingId, toStatus, actorId) {
  const updated = await prisma.$transaction(async (tx) => {
    // ...same transition logic as the State Machine skill...
    return tx.booking.findUnique({ where: { id: bookingId }, include: { resource: true } });
  });

  const io = getIO();
  const payload = { bookingId, status: updated.status, resourceId: updated.resourceId };
  io.to('role:ADMIN').emit('booking:updated', payload);
  io.to(`user:${updated.userId}`).emit('booking:updated', payload);
  io.to(`resource:${updated.resourceId}`).emit('booking:updated', payload);

  return updated;
}
```

## Step 3 — Event naming convention
```
<entity>:<action>
booking:created
booking:updated
booking:cancelled
asset:allocated
asset:released
```
Agree on this list as a team before wiring any events — mismatched names between backend and frontend teammates is the #1 websocket time-sink under deadline pressure.

## Step 4 — React client: connection + typed hook
```jsx
// src/realtime/SocketProvider.jsx
import { createContext, useContext, useEffect, useRef } from 'react';
import { io } from 'socket.io-client';
import { useAuth } from '../context/AuthContext';

const SocketContext = createContext(null);

export function SocketProvider({ children }) {
  const { user } = useAuth();
  const socketRef = useRef(null);

  useEffect(() => {
    if (!user) return;
    const socket = io(import.meta.env.VITE_API_URL, {
      auth: { token: localStorage.getItem('accessToken') },
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
    });
    socketRef.current = socket;
    return () => socket.disconnect();
  }, [user]);

  return <SocketContext.Provider value={socketRef}>{children}</SocketContext.Provider>;
}

export function useSocketEvent(event, handler) {
  const socketRef = useContext(SocketContext);
  useEffect(() => {
    const socket = socketRef?.current;
    if (!socket) return;
    socket.on(event, handler);
    return () => socket.off(event, handler);
  }, [event, handler]);
}
```
```jsx
// usage: AdminDashboard.jsx
import { useSocketEvent } from '../realtime/SocketProvider';
import { useToast } from '../components/common/Toast';
import { useQueryClient } from '@tanstack/react-query';

function AdminDashboard() {
  const queryClient = useQueryClient();
  const toast = useToast();

  useSocketEvent('booking:updated', (payload) => {
    queryClient.invalidateQueries({ queryKey: ['bookings'] });
    toast(`Booking ${payload.bookingId.slice(0, 8)} → ${payload.status}`);
  });

  // ...render dashboard, data comes from React Query as usual
}
```

## Step 5 — Optimistic UI with rollback
```jsx
// src/hooks/useOptimisticMutation.js
import { useMutation, useQueryClient } from '@tanstack/react-query';

export function useOptimisticBookingTransition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }) => api.patch(`/bookings/${id}/status`, { status }),
    onMutate: async ({ id, status }) => {
      await queryClient.cancelQueries({ queryKey: ['bookings'] });
      const previous = queryClient.getQueryData(['bookings']);
      queryClient.setQueryData(['bookings'], (old) =>
        old?.map(b => b.id === id ? { ...b, status } : b));
      return { previous };
    },
    onError: (err, vars, context) => {
      queryClient.setQueryData(['bookings'], context.previous); // rollback on conflict
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['bookings'] }),
  });
}
```

## Reconnection & fallback
```js
// feature flag so you can flip to polling instantly if sockets misbehave live
const REALTIME_MODE = import.meta.env.VITE_REALTIME_MODE || 'socket'; // 'socket' | 'poll'

useEffect(() => {
  if (REALTIME_MODE !== 'poll') return;
  const interval = setInterval(() => queryClient.invalidateQueries({ queryKey: ['bookings'] }), 4000);
  return () => clearInterval(interval);
}, []);
```
Keep this as your safety net — don't let a flaky demo wifi connection sink your real-time story; flip the env var and fall back to polling with zero code changes.

## Demo tip
Two browser windows side by side, different roles logged in. Trigger an action in one, show the other update live with no refresh. This is a strong, low-effort "wow" moment — script it explicitly into the Demo Script Builder's walkthrough section.

## Checklist before moving on
- [ ] Socket handshake authenticated with the same JWT secret as REST
- [ ] Events only emitted from the service layer, after a successful DB write
- [ ] Event names agreed and documented before implementation starts
- [ ] Polling fallback wired and tested behind a feature flag
- [ ] Optimistic updates roll back cleanly on a `409` conflict

## Common mistakes to avoid
- Emitting from the route handler instead of the service layer — fires even when the transaction later fails.
- Broadcasting to `io.emit()` (all connected clients) instead of scoped rooms — leaks data across roles/users.
- Not handling reconnection — a dropped wifi connection during the live demo silently stops updates with no visible error.

## Integration with the rest of the stack
- Emits directly out of the **State Machine** skill's `transitionBooking` — one function, one source of truth for both the DB write and the live event.
- Received events feed the **UI Component Kit**'s `useToast` for visible confirmation.
