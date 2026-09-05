import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { getToken } from '@/lib/auth';
import type { RealtimeEvent, RealtimeState } from '@/types/insights';

/**
 * A WebSocket subscription to one realtime channel (Architecture §8.4).
 *
 * REALTIME IS AN ENHANCEMENT, NEVER A DEPENDENCY
 * ----------------------------------------------
 * Nothing on any screen requires this hook to succeed. A frame arriving is
 * treated as a hint that server state moved, so the hook invalidates the
 * relevant TanStack Query keys and lets the normal REST read produce the new
 * value. It never writes a frame's payload into the cache directly.
 *
 * That distinction is the whole design. If frames became the source of the
 * numbers on screen, a dropped frame would leave a stale payroll figure that
 * looked authoritative, and a reconnect would not fix it. Refetching means the
 * worst a missed frame can cost is a slightly late update — and the user can
 * always reload.
 *
 * `state` is exposed so a screen can SAY the live feed is unavailable rather
 * than looking silently stale.
 */

const MAX_BACKOFF_MS = 15_000;
const BASE_BACKOFF_MS = 1_000;

/** Which query keys a channel's events should invalidate. */
const CHANNEL_INVALIDATIONS: Record<string, string[][]> = {
  attendance: [['attendance'], ['dashboard']],
  time_off: [['time-off'], ['dashboard']],
  approvals: [['time-off'], ['dashboard']],
  payroll: [['payruns'], ['payslips'], ['insights'], ['dashboard']],
};

export function useRealtimeChannel(channel: string, options?: { enabled?: boolean }) {
  const enabled = options?.enabled ?? true;
  const [state, setState] = useState<RealtimeState>('connecting');
  const [lastEvent, setLastEvent] = useState<RealtimeEvent | null>(null);
  const [events, setEvents] = useState<RealtimeEvent[]>([]);
  const queryClient = useQueryClient();

  const socketRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const closedByUs = useRef(false);

  const connect = useCallback(() => {
    const token = getToken();
    if (!token) {
      setState('unavailable');
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${protocol}://${window.location.host}/api/v1/ws/${channel}?token=${encodeURIComponent(token)}`;

    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch {
      // Some browsers throw synchronously on a malformed or blocked URL.
      setState('unavailable');
      return;
    }
    socketRef.current = socket;

    socket.onopen = () => {
      attemptRef.current = 0;
      setState('live');
    };

    socket.onmessage = (message) => {
      let frame: RealtimeEvent;
      try {
        frame = JSON.parse(message.data as string);
      } catch {
        return; // A frame we cannot parse is a frame we ignore.
      }
      if (frame.event === 'connected') return;

      setLastEvent(frame);
      setEvents((current) => [frame, ...current].slice(0, 50));

      // The frame is a HINT. The authoritative value comes from the refetch
      // this triggers — never from the frame's own payload.
      for (const key of CHANNEL_INVALIDATIONS[channel] ?? []) {
        queryClient.invalidateQueries({ queryKey: key });
      }
    };

    socket.onerror = () => {
      // Reported through onclose, which always follows.
    };

    socket.onclose = () => {
      socketRef.current = null;
      if (closedByUs.current) return;
      setState('unavailable');
      // Exponential backoff, capped. An API that is down must not be retried
      // every 100ms by every open tab.
      const delay = Math.min(BASE_BACKOFF_MS * 2 ** attemptRef.current, MAX_BACKOFF_MS);
      attemptRef.current += 1;
      timerRef.current = window.setTimeout(() => {
        setState('connecting');
        connect();
      }, delay);
    };
  }, [channel, queryClient]);

  useEffect(() => {
    if (!enabled) {
      setState('unavailable');
      return;
    }
    closedByUs.current = false;
    setState('connecting');
    connect();

    return () => {
      closedByUs.current = true;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [connect, enabled]);

  return { state, lastEvent, events };
}
