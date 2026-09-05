import { useEffect, useState } from 'react';
import { Trash2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useOfflineEntities, useOfflineMutation } from '@/hooks/useOfflineMutation';
import { useOnlineStatus } from '@/lib/reachability';
import { onSyncSettled, runSync } from '@/lib/sync-engine';
import { useQueryClient } from '@tanstack/react-query';

interface NoteRow {
  content: string;
  version: number;
  updated_at: string;
  _publicId: string;
  _version: number;
}

/**
 * Reference "Product Surface" proving the generic offline layer end to end.
 * Notes was already Harmonix360's generic-scaffolding proof entity
 * (app/verify_scaffolding.py) before this pass — reusing it here means this
 * page needs zero new backend domain surface, only the sync wiring.
 */
export function NotesPage() {
  const online = useOnlineStatus();
  const queryClient = useQueryClient();
  const { data: notes, isLoading } = useOfflineEntities<NoteRow>('note');
  const { create, update, remove } = useOfflineMutation('note');
  const [draft, setDraft] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState('');

  useEffect(() => onSyncSettled(() => queryClient.invalidateQueries({ queryKey: ['offline', 'note'] })), [queryClient]);

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Notes</h1>
          <p className="text-sm text-slate-500">Offline-first reference surface — works with the network off.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => runSync()} disabled={!online}>
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" /> Sync now
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">New note</CardTitle>
          <CardDescription>Saved locally immediately, queued for sync.</CardDescription>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Input
            data-testid="new-note-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Write something..."
            onKeyDown={async (e) => {
              if (e.key === 'Enter' && draft.trim()) {
                await create({ content: draft.trim() });
                setDraft('');
              }
            }}
          />
          <Button
            data-testid="add-note-button"
            onClick={async () => {
              if (!draft.trim()) return;
              await create({ content: draft.trim() });
              setDraft('');
            }}
          >
            Add
          </Button>
        </CardContent>
      </Card>

      <div className="space-y-2">
        {isLoading && <p className="text-sm text-slate-500">Loading local cache…</p>}
        {notes?.length === 0 && <p className="text-sm text-slate-500">No notes yet.</p>}
        {notes?.map((note) => (
          <Card key={note._publicId} className="p-4" data-testid={`note-row-${note._publicId}`}>
            {editingId === note._publicId ? (
              <div className="flex gap-2">
                <Input data-testid="edit-note-input" value={editText} onChange={(e) => setEditText(e.target.value)} autoFocus />
                <Button
                  data-testid="save-edit-button"
                  size="sm"
                  onClick={async () => {
                    await update(note._publicId, note._version, { content: editText });
                    setEditingId(null);
                  }}
                >
                  Save
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                  Cancel
                </Button>
              </div>
            ) : (
              <div className="flex items-center justify-between gap-3">
                <div
                  data-testid="note-content"
                  className="flex-1 cursor-text text-sm text-slate-200"
                  onClick={() => {
                    setEditingId(note._publicId);
                    setEditText(note.content);
                  }}
                >
                  {note.content}
                </div>
                <Badge variant="outline" className="font-mono text-[10px]" data-testid="note-version">
                  v{note._version}
                </Badge>
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => remove(note._publicId, note._version)}
                  aria-label="Delete note"
                >
                  <Trash2 className="h-4 w-4 text-red-400" />
                </Button>
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
