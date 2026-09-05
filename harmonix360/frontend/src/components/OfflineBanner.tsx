import { WifiOff } from 'lucide-react';
import { useOnlineStatus } from '@/lib/reachability';

/** Honest indicator: only shows once `navigator.onLine` AND a live /health
 * ping both fail (see src/lib/reachability.ts) — not just "the OS thinks
 * there's a network interface." */
export function OfflineBanner() {
  const online = useOnlineStatus();
  if (online) return null;

  return (
    <div data-testid="offline-banner" className="flex items-center justify-center gap-2 bg-amber-950/60 border-b border-amber-800/60 text-amber-300 text-xs font-medium py-2 px-4">
      <WifiOff className="h-3.5 w-3.5" />
      You're offline — changes are being saved locally and will sync automatically once you're back online.
    </div>
  );
}
