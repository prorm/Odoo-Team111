import * as React from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Pencil, Plus, Trash2 } from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { useDeleteWorkingSchedule, useWorkingSchedules } from '@/hooks/useHrApi';
import type { WorkingSchedule } from '@/types/hr';
import { SCHEDULE_TYPE_LABELS, WEEKDAY_LABELS } from '@/types/hr';

import { ScheduleForm } from './ScheduleForm';

/** Working Schedule configuration (PS A3). */
export function SchedulesPage() {
  const navigate = useNavigate();
  const [editing, setEditing] = React.useState<WorkingSchedule | undefined>();
  const [formOpen, setFormOpen] = React.useState(false);

  const { data, isLoading, error } = useWorkingSchedules();
  const remove = useDeleteWorkingSchedule();

  const schedules = data?.items ?? [];

  function openCreate() {
    setEditing(undefined);
    setFormOpen(true);
  }

  function openEdit(schedule: WorkingSchedule) {
    setEditing(schedule);
    setFormOpen(true);
  }

  return (
    <div className="space-y-5">
      <div>
        <Button variant="ghost" size="sm" onClick={() => navigate('/employees')} className="-ml-2">
          <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
          Employees
        </Button>
      </div>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Working schedules</h1>
          <p className="mt-0.5 text-xs text-slate-400">
            Weekly patterns assignable to an employee, or overridden per contract. Weekly hours are
            computed from the blocks below and cannot be entered by hand.
          </p>
        </div>
        <Button size="sm" onClick={openCreate}>
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          New schedule
        </Button>
      </header>

      <StatusMessage error={error ?? remove.error} />

      {isLoading && <p className="text-sm text-slate-400">Loading schedules…</p>}

      {!isLoading && schedules.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-sm text-slate-300">No working schedules yet.</p>
            <p className="mt-1 text-xs text-slate-500">
              Create one to assign it to employees and contracts.
            </p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {schedules.map((schedule) => (
          <Card key={schedule.id}>
            <CardContent className="p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-medium text-slate-100">{schedule.name}</h2>
                  <p className="mt-0.5 text-[11px] text-slate-400">
                    {SCHEDULE_TYPE_LABELS[schedule.schedule_type]}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  {/* The computed total is the headline, so it reads as a
                      derived fact rather than a field somebody typed. */}
                  <Badge variant="info" className="text-[10px]">
                    {schedule.weekly_hours}h / week
                  </Badge>
                  <Button variant="ghost" size="icon" onClick={() => openEdit(schedule)} aria-label={`Edit ${schedule.name}`}>
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => remove.mutate(schedule.id)}
                    aria-label={`Delete ${schedule.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-rose-400" />
                  </Button>
                </div>
              </div>

              {schedule.lines.length === 0 ? (
                <p className="mt-3 text-[11px] text-slate-500">
                  No blocks — this schedule is 0 hours a week.
                </p>
              ) : (
                <ul className="mt-3 space-y-1">
                  {schedule.lines.map((line) => (
                    <li key={line.id} className="flex justify-between text-[11px] text-slate-400">
                      <span className="text-slate-300">{WEEKDAY_LABELS[line.day_of_week]}</span>
                      <span className="font-mono">
                        {line.start_time.slice(0, 5)}–{line.end_time.slice(0, 5)}
                        {line.break_minutes > 0 && (
                          <span className="text-slate-500"> · {line.break_minutes}m break</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      <ScheduleForm open={formOpen} onOpenChange={setFormOpen} schedule={editing} />
    </div>
  );
}
