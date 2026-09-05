/**
 * Dual reachability check: `navigator.onLine` alone is unreliable (true on a
 * captive portal, or a VPN/DNS failure that leaves the OS interface "up" but
 * the backend unreachable) — the offline banner is only honest if it actually
 * confirms the backend answers, not just that a network interface exists.
 */
import { useEffect, useState } from 'react';

const PING_INTERVAL_MS = 5000;
const PING_TIMEOUT_MS = 2000;

type Listener = (online: boolean) => void;

class ReachabilityMonitor {
  private online = navigator.onLine;
  private listeners = new Set<Listener>();
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    window.addEventListener('online', () => this.ping());
    window.addEventListener('offline', () => this.setOnline(false));
    this.start();
  }

  private start() {
    if (this.timer) return;
    this.ping();
    this.timer = setInterval(() => this.ping(), PING_INTERVAL_MS);
  }

  private async ping() {
    if (!navigator.onLine) {
      this.setOnline(false);
      return;
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), PING_TIMEOUT_MS);
    try {
      const res = await fetch('/health', { method: 'GET', signal: controller.signal, cache: 'no-store' });
      this.setOnline(res.ok);
    } catch {
      this.setOnline(false);
    } finally {
      clearTimeout(timeout);
    }
  }

  private setOnline(value: boolean) {
    if (value === this.online) return;
    this.online = value;
    for (const listener of this.listeners) listener(value);
  }

  getSnapshot = () => this.online;

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Forces an immediate check instead of waiting for the next tick — used
   * right before a manual "Sync now" so the UI doesn't act on stale state. */
  async checkNow(): Promise<boolean> {
    await this.ping();
    return this.online;
  }
}

export const reachability = new ReachabilityMonitor();

export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(reachability.getSnapshot());
  useEffect(() => reachability.subscribe(setOnline), []);
  return online;
}
