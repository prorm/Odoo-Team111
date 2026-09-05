import * as React from 'react';

import { FormField } from '@/components/FormField';
import { StatusMessage } from '@/components/StatusMessage';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { useSaveEmployee, useWorkingSchedules } from '@/hooks/useHrApi';
import { fetchApi } from '@/lib/api-client';
import { useQuery } from '@tanstack/react-query';
import type { DepartmentRef, Employee, EmployeeInput } from '@/types/hr';
import {
  EMPLOYEE_STATUS_LABELS,
  EMPLOYEE_TYPE_LABELS,
  EmployeeStatus,
  EmployeeType,
} from '@/types/hr';

import { ManagerSelect } from './ManagerSelect';

interface EmployeeFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Absent for a create. */
  employee?: Employee;
  onSaved?: (employee: Employee) => void;
}

function emptyForm(): EmployeeInput {
  return {
    first_name: '',
    last_name: '',
    work_email: '',
    phone: '',
    job_position: '',
    employee_type: EmployeeType.PERMANENT,
    status: EmployeeStatus.ACTIVE,
    hire_date: '',
    bank_account: '',
    department_id: null,
    manager_id: null,
    default_schedule_id: null,
  };
}

function toForm(employee: Employee): EmployeeInput {
  return {
    first_name: employee.first_name,
    last_name: employee.last_name,
    work_email: employee.work_email,
    phone: employee.phone ?? '',
    job_position: employee.job_position ?? '',
    employee_type: employee.employee_type,
    status: employee.status,
    hire_date: employee.hire_date ?? '',
    bank_account: employee.bank_account ?? '',
    department_id: employee.department?.id ?? null,
    manager_id: employee.manager?.id ?? null,
    default_schedule_id: employee.default_schedule?.id ?? null,
  };
}

/**
 * The unified Employee form (PS A1/B2): identity, role, department, manager,
 * schedule, status.
 *
 * One form for create and edit rather than two. The fields are identical, and
 * two components would drift the moment one gained a field the other did not.
 */
export function EmployeeForm({ open, onOpenChange, employee, onSaved }: EmployeeFormProps) {
  const isEdit = Boolean(employee);
  const [values, setValues] = React.useState<EmployeeInput>(() =>
    employee ? toForm(employee) : emptyForm()
  );

  const save = useSaveEmployee();
  const { data: schedules } = useWorkingSchedules();
  const { data: departments } = useQuery({
    queryKey: ['departments'],
    queryFn: () => fetchApi<DepartmentRef[]>('/departments/'),
    staleTime: 5 * 60 * 1000,
  });

  // Reset when the dialog opens, not on every render: reopening the form for a
  // different employee must not show the previous one's values, but typing must
  // not be clobbered by a background refetch either.
  React.useEffect(() => {
    if (open) {
      setValues(employee ? toForm(employee) : emptyForm());
      save.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, employee?.id]);

  const set = <K extends keyof EmployeeInput>(key: K, value: EmployeeInput[K]) =>
    setValues((previous) => ({ ...previous, [key]: value }));

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    // Empty strings become null: the API distinguishes "not provided" from
    // "cleared", and sending "" for an optional date is a 422.
    const payload: EmployeeInput = {
      ...values,
      phone: values.phone || null,
      job_position: values.job_position || null,
      hire_date: values.hire_date || null,
      bank_account: values.bank_account || null,
    };

    const saved = await save.mutateAsync({ id: employee?.id, values: payload });
    onSaved?.(saved);
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit ${employee?.full_name}` : 'New employee'}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          {/* The server's message is rendered inline rather than as a toast: a
              duplicate work email names the address the user must change, and a
              notification that fades takes that with it. */}
          <StatusMessage error={save.error} />

          <section className="space-y-3">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Identity</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField label="First name" htmlFor="first_name" required>
                <Input
                  id="first_name"
                  value={values.first_name}
                  onChange={(e) => set('first_name', e.target.value)}
                  required
                />
              </FormField>
              <FormField label="Last name" htmlFor="last_name" required>
                <Input
                  id="last_name"
                  value={values.last_name}
                  onChange={(e) => set('last_name', e.target.value)}
                  required
                />
              </FormField>
              <FormField label="Work email" htmlFor="work_email" required>
                <Input
                  id="work_email"
                  type="email"
                  value={values.work_email}
                  onChange={(e) => set('work_email', e.target.value)}
                  required
                />
              </FormField>
              <FormField label="Phone" htmlFor="phone">
                <Input
                  id="phone"
                  value={values.phone ?? ''}
                  onChange={(e) => set('phone', e.target.value)}
                />
              </FormField>
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Role</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField label="Job position" htmlFor="job_position">
                <Input
                  id="job_position"
                  value={values.job_position ?? ''}
                  onChange={(e) => set('job_position', e.target.value)}
                />
              </FormField>
              <FormField label="Department" htmlFor="department_id">
                <Select
                  id="department_id"
                  value={values.department_id ?? ''}
                  onChange={(e) => set('department_id', e.target.value || null)}
                >
                  <option value="">— None —</option>
                  {departments?.map((department) => (
                    <option key={department.id} value={department.id}>
                      {department.name}
                    </option>
                  ))}
                </Select>
              </FormField>

              <FormField
                label="Manager"
                htmlFor="manager"
                className="sm:col-span-2"
                hint="Search by name, email or position."
              >
                <ManagerSelect
                  value={values.manager_id ?? null}
                  currentLabel={employee?.manager ? `${employee.manager.first_name} ${employee.manager.last_name}` : null}
                  excludeId={employee?.id}
                  onChange={(id) => set('manager_id', id)}
                />
              </FormField>

              <FormField label="Employment type" htmlFor="employee_type">
                <Select
                  id="employee_type"
                  value={values.employee_type}
                  onChange={(e) => set('employee_type', e.target.value as EmployeeType)}
                >
                  {Object.values(EmployeeType).map((type) => (
                    <option key={type} value={type}>
                      {EMPLOYEE_TYPE_LABELS[type]}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Status" htmlFor="status">
                <Select
                  id="status"
                  value={values.status}
                  onChange={(e) => set('status', e.target.value as EmployeeStatus)}
                >
                  {Object.values(EmployeeStatus).map((status) => (
                    <option key={status} value={status}>
                      {EMPLOYEE_STATUS_LABELS[status]}
                    </option>
                  ))}
                </Select>
              </FormField>
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Working time &amp; payroll
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField
                label="Working schedule"
                htmlFor="default_schedule_id"
                hint="A contract can override this for the period it covers."
              >
                <Select
                  id="default_schedule_id"
                  value={values.default_schedule_id ?? ''}
                  onChange={(e) => set('default_schedule_id', e.target.value || null)}
                >
                  <option value="">— None —</option>
                  {schedules?.items.map((schedule) => (
                    <option key={schedule.id} value={schedule.id}>
                      {schedule.name} ({schedule.weekly_hours}h/week)
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Hire date" htmlFor="hire_date">
                <Input
                  id="hire_date"
                  type="date"
                  value={values.hire_date ?? ''}
                  onChange={(e) => set('hire_date', e.target.value)}
                />
              </FormField>
              <FormField
                label="Bank account"
                htmlFor="bank_account"
                className="sm:col-span-2"
                hint="Missing bank details block payroll finalisation."
              >
                <Input
                  id="bank_account"
                  value={values.bank_account ?? ''}
                  onChange={(e) => set('bank_account', e.target.value)}
                />
              </FormField>
            </div>
          </section>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? 'Saving…' : isEdit ? 'Save changes' : 'Create employee'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
