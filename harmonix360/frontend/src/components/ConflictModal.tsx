import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { getConflicts, type ConflictRecord } from '@/lib/offline-db';
import { onSyncSettled, resolveConflictKeepMine, resolveConflictOverwrite } from '@/lib/sync-engine';

/** Minimal, functional resolution UI (offline-sync requirement): surfaces a
 * version conflict rather than silently picking a winner. One conflict at a
 * time — resolving it re-checks for the next. */
export function ConflictModal() {
  const [conflicts, setConflicts] = useState<ConflictRecord[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    void getConflicts().then(setConflicts);
  };

  useEffect(() => {
    refresh();
    return onSyncSettled(refresh);
  }, []);

  const current = conflicts[0];
  if (!current) return null;

  const mineText = (current.mine_payload?.content as string) ?? JSON.stringify(current.mine_payload);
  const serverText = (current.current_state?.content as string) ?? JSON.stringify(current.current_state);

  return (
    <Dialog open={true}>
      {/* data-testid sits on this inner div, not DialogContent: DialogContent
          (src/components/ui/dialog.tsx) only destructures className/children
          and doesn't spread other props onto its rendered element. */}
      <DialogContent>
        <div data-testid="conflict-modal">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-400" />
              Server version has changed
            </DialogTitle>
            <DialogDescription>
              {current.entity_type} <span className="font-mono text-slate-300">{current.entity_id}</span> was
              modified by someone else while you were offline. Choose which version to keep.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
              <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">Your offline version</div>
              <div className="text-slate-200">{mineText}</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
              <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
                Server version (v{current.current_version})
              </div>
              <div className="text-slate-200">{serverText}</div>
            </div>
          </div>

          <DialogFooter>
            <Button
              data-testid="conflict-overwrite-button"
              variant="outline"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                await resolveConflictOverwrite(current.client_mutation_id);
                setBusy(false);
                refresh();
              }}
            >
              Overwrite with Server
            </Button>
            <Button
              data-testid="conflict-keep-mine-button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                await resolveConflictKeepMine(current.client_mutation_id);
                setBusy(false);
                refresh();
              }}
            >
              Keep Mine
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
