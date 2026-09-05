import * as React from 'react';
import { Plus, Trash2 } from 'lucide-react';

import { FormField } from '@/components/FormField';
import { StatusMessage } from '@/components/StatusMessage';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { useSaveWorkingSchedule } from '@/hooks/useHrApi';
import type { ScheduleLineInput, WorkingSchedule } from '@/types/hr';
import { SCHEDULE_TYPE_LABELS, WEEKDAY_LABELS, WEEKDAY_ORDER, Weekday, WorkingScheduleType } from '@/types/hr';

interface ScheduleFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  schedule?: WorkingSchedule;
}

function defaultLine(): ScheduleLineInput {
  return {
    day_of_week: Weekday.MONDAY,
    start_time: '09:00',
    end_time: '17:00',
    break_minutes: 60,
  };
}

function toLines(schedule: WorkingSchedule): ScheduleLineInput[] {
  return schedule.lines.map((line) => ({
    day_of_week: line.day_of_week,
    // The API returns HH:MM:SS; <input type="time"> wants HH:MM.
    start_time: line.start_time.slice(0, 5),
    end_time: line.end_time.slice(0, 5),
    break_minutes: line.break_minutes,
  }));
}

/**
 * Working schedule form with the repeatable line sub-form (PS A3):
 * Day / Start / End / Break.
 *
 * Note what is NOT here: a weekly-hours input. The value is computed by the
 * server from these lines and is not part of the request at all — the preview
 * below is a courtesy so the user can see what they are building, and it is
 * labelled as an estimate precisely so nobody mistakes it for the stored value.
 * Whatever the server returns after saving is the truth.
 */
export function ScheduleForm({ open, onOpenChange, schedule }: ScheduleFormProps) {
  const isEdit = Boolean(schedule);
  const [name, setName] = React.useState('');
  const [scheduleType, setScheduleType] = React.useState<WorkingScheduleType>(
    WorkingScheduleType.FULL_TIME
  );
  const [lines, setLines] = React.useState<ScheduleLineInput[]>([]);

  const save = useSaveWorkingSchedule();

  React.useEffect(() => {
    if (!open) return;
    setName(schedule?.name ?? '');
    setScheduleType(schedule?.schedule_type ?? WorkingScheduleType.FULL_TIME);
    setLines(schedule ? toLines(schedule) : WEEKDAY_ORDER.slice(0, 5).map((day) => ({ ...defaultLine(), day_of_week: day })));
    save.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, schedule?.id]);

  function updateLine(index: number, patch: Partial<ScheduleLineInput>) {
    setLines((previous) => previous.map((line, i) => (i === index ? { ...line, ...patch } : line)));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    await save.mutateAsync({
      id: schedule?.id,
      values: {
        name,
        schedule_type: scheduleType,
        // Seconds appended because the API expects a full time value.
        lines: lines.map((line) => ({
          ...line,
          start_time: `${line.start_time}:00`,
          end_time: `${line.end_time}:00`,
          break_minutes: Number(line.break_minutes) || 0,
        })),
      },
    });
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit ${schedule?.name}` : 'New working schedule'}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          <StatusMessage error={save.error} />

          <div className="grid gap-3 sm:grid-cols-2">
            <FormField label="Name" htmlFor="schedule_name" required>
              <Input
                id="schedule_name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Standard 40h"
                required
              />
            </FormField>
            <FormField label="Type" htmlFor="schedule_type">
              <Select
                id="schedule_type"
                value={scheduleType}
                onChange={(e) => setScheduleType(e.target.value as WorkingScheduleType)}
              >
                {Object.values(WorkingScheduleType).map((type) => (
                  <option key={type} value={type}>
                    {SCHEDULE_TYPE_LABELS[type]}
                  </option>
                ))}
              </Select>
            </FormField>
          </div>

          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                Weekly pattern
              </h3>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setLines((previous) => [...previous, defaultLine()])}
              >
                <Plus className="mr-1.5 h-3.5 w-3.5" />
                Add block
              </Button>
            </div>

            {lines.length === 0 && (
              <p className="rounded-lg border border-dashed border-slate-800 px-3 py-6 text-center text-xs text-slate-500">
                No blocks yet. A schedule with no blocks is 0 hours a week.
              </p>
            )}

            <div className="space-y-2">
              {lines.map((line, index) => (
                <div
                  key={index}
                  className="grid grid-cols-[1fr_auto_auto_auto_auto] items-end gap-2 rounded-lg border border-slate-800 bg-slate-950/40 p-2.5"
                >
                  <FormField label="Day" htmlFor={`day-${index}`}>
                    <Select
                      id={`day-${index}`}
                      value={line.day_of_week}
                      onChange={(e) => updateLine(index, { day_of_week: e.target.value as Weekday })}
                      className="h-9"
                    >
                      {WEEKDAY_ORDER.map((day) => (
                        <option key={day} value={day}>
                          {WEEKDAY_LABELS[day]}
                        </option>
                      ))}
                    </Select>
                  </FormField>

                  <FormField label="Start" htmlFor={`start-${index}`}>
                    <Input
                      id={`start-${index}`}
                      type="time"
                      value={line.start_time}
                      onChange={(e) => updateLine(index, { start_time: e.target.value })}
                      className="h-9 w-28"
                      required
                    />
                  </FormField>

                  <FormField label="End" htmlFor={`end-${index}`}>
                    <Input
                      id={`end-${index}`}
                      type="time"
                      value={line.end_time}
                      onChange={(e) => updateLine(index, { end_time: e.target.value })}
                      className="h-9 w-28"
                      required
                    />
                  </FormField>

                  <FormField label="Break (min)" htmlFor={`break-${index}`}>
                    <Input
                      id={`break-${index}`}
                      type="number"
                      min={0}
                      max={1440}
                      value={line.break_minutes}
                      onChange={(e) => updateLine(index, { break_minutes: Number(e.target.value) })}
                      className="h-9 w-24"
                    />
                  </FormField>

                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="mb-0.5"
                    aria-label={`Remove block ${index + 1}`}
                    onClick={() => setLines((previous) => previous.filter((_, i) => i !== index))}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-rose-400" />
                  </Button>
                </div>
              ))}
            </div>

            <p className="text-[11px] text-slate-500">
              Estimated {estimateWeeklyHours(lines)}h per week. The stored value is computed on the
              server when you save — this preview is only to show what you are building.
            </p>
          </section>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? 'Saving…' : isEdit ? 'Save changes' : 'Create schedule'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * A display-only preview of the weekly total.
 *
 * Mirrors the server's method — sum whole minutes, divide once — so the preview
 * and the stored value agree. It is never sent anywhere and is never treated as
 * authoritative: the server recomputes on save, and its answer replaces this
 * one. Duplicating a calculation is normally a smell; here the alternative is a
 * form where adding a block shows no feedback until after a round trip.
 */
function estimateWeeklyHours(lines: ScheduleLineInput[]): string {
  let minutes = 0;
  for (const line of lines) {
    const [startHour, startMinute] = line.start_time.split(':').map(Number);
    const [endHour, endMinute] = line.end_time.split(':').map(Number);
    if ([startHour, startMinute, endHour, endMinute].some(Number.isNaN)) continue;
    const block = endHour * 60 + endMinute - (startHour * 60 + startMinute) - (Number(line.break_minutes) || 0);
    if (block > 0) minutes += block;
  }
  return (minutes / 60).toFixed(2);
}
