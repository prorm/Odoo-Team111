import * as React from 'react';

import { FormField } from '@/components/FormField';
import { StatusMessage } from '@/components/StatusMessage';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { useEmployees, useSaveContract, useWorkingSchedules } from '@/hooks/useHrApi';
import { ApiError, fetchApi } from '@/lib/api-client';
import { useQuery } from '@tanstack/react-query';
import type { Contract, ContractInput, DepartmentRef } from '@/types/hr';
import { CONTRACT_STATUS_LABELS, ContractStatus } from '@/types/hr';

interface ContractFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  contract?: Contract;
  defaultEmployeeId?: string;
}

function emptyForm(employeeId?: string): ContractInput {
  return {
    employee_id: employeeId ?? '',
    wage: '',
    start_date: '',
    end_date: '',
    job_position: '',
    status: ContractStatus.DRAFT,
    notes: '',
    department_id: null,
    working_schedule_id: null,
  };
}

function toForm(contract: Contract): ContractInput {
  return {
    employee_id: contract.employee.id,
    wage: contract.wage,
    start_date: contract.start_date,
    end_date: contract.end_date ?? '',
    job_position: contract.job_position ?? '',
    status: contract.status,
    notes: contract.notes ?? '',
    department_id: contract.department?.id ?? null,
    working_schedule_id: contract.working_schedule?.id ?? null,
    salary_structure_id: contract.salary_structure?.id ?? null,
  };
}

/**
 * Contract form (PS A2): duration, department, position, wage, salary
 * structure.
 *
 * THE THING THIS FORM MUST GET RIGHT: an overlapping active contract comes back
 * as a 409 from a Postgres EXCLUDE constraint, and that is a NORMAL outcome —
 * HR renewing a contract without ending the previous one will hit it routinely.
 * It renders inline, above the date fields that caused it, with the server's
 * message naming the conflicting contract and its period. It is never a crash,
 * never a toast that fades while the user is still reading the form, and the
 * form keeps every value the user entered so they can adjust the dates and
 * resubmit.
 */
export function ContractForm({ open, onOpenChange, contract, defaultEmployeeId }: ContractFormProps) {
  const isEdit = Boolean(contract);
  const [values, setValues] = React.useState<ContractInput>(() => emptyForm(defaultEmployeeId));

  const save = useSaveContract();
  const { data: employees } = useEmployees({ limit: 200 });
  const { data: schedules } = useWorkingSchedules();
  const { data: departments } = useQuery({
    queryKey: ['departments'],
    queryFn: () => fetchApi<DepartmentRef[]>('/departments/'),
    staleTime: 5 * 60 * 1000,
  });

  React.useEffect(() => {
    if (!open) return;
    setValues(contract ? toForm(contract) : emptyForm(defaultEmployeeId));
    save.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, contract?.id, defaultEmployeeId]);

  const set = <K extends keyof ContractInput>(key: K, value: ContractInput[K]) =>
    setValues((previous) => ({ ...previous, [key]: value }));

  const overlapError = save.error instanceof ApiError && save.error.isConflict ? save.error : null;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();

    const payload: ContractInput = {
      ...values,
      end_date: values.end_date || null,
      job_position: values.job_position || null,
      notes: values.notes || null,
    };
    // employee_id is immutable once set — moving a contract between people
    // would rewrite two payroll histories, so the API does not accept it on
    // PATCH either.
    if (isEdit) delete payload.employee_id;

    await save.mutateAsync({ id: contract?.id, values: payload });
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit contract' : 'New contract'}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          <StatusMessage error={save.error} />

          <div className="grid gap-3 sm:grid-cols-2">
            <FormField label="Employee" htmlFor="employee_id" required className="sm:col-span-2">
              <Select
                id="employee_id"
                value={values.employee_id ?? ''}
                onChange={(e) => set('employee_id', e.target.value)}
                disabled={isEdit}
                required
              >
                <option value="">Select an employee…</option>
                {employees?.items.map((employee) => (
                  <option key={employee.id} value={employee.id}>
                    {employee.full_name}
                  </option>
                ))}
              </Select>
            </FormField>

            <FormField
              label="Start date"
              htmlFor="start_date"
              required
              error={overlapError ? 'Overlaps an existing active contract — see above.' : null}
            >
              <Input
                id="start_date"
                type="date"
                value={values.start_date}
                onChange={(e) => set('start_date', e.target.value)}
                required
                aria-invalid={Boolean(overlapError)}
              />
            </FormField>

            <FormField
              label="End date"
              htmlFor="end_date"
              hint="Leave empty for an open-ended contract."
              error={overlapError ? 'Overlaps an existing active contract — see above.' : null}
            >
              <Input
                id="end_date"
                type="date"
                value={values.end_date ?? ''}
                onChange={(e) => set('end_date', e.target.value)}
                aria-invalid={Boolean(overlapError)}
              />
            </FormField>

            <FormField label="Job position" htmlFor="contract_position">
              <Input
                id="contract_position"
                value={values.job_position ?? ''}
                onChange={(e) => set('job_position', e.target.value)}
              />
            </FormField>

            <FormField label="Department" htmlFor="contract_department">
              <Select
                id="contract_department"
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

            <FormField label="Wage" htmlFor="wage" required hint="Gross, in the organisation's currency.">
              <Input
                id="wage"
                // `inputMode` + text, not type="number": a number input in some
                // browsers reformats and loses trailing decimal zeros, and this
                // value is sent as a string precisely so it stays exact.
                inputMode="decimal"
                value={values.wage}
                onChange={(e) => set('wage', e.target.value)}
                placeholder="50000.00"
                required
              />
            </FormField>

            <FormField
              label="Status"
              htmlFor="contract_status"
              hint="Only 'Active' contracts are checked for overlap."
            >
              <Select
                id="contract_status"
                value={values.status}
                onChange={(e) => set('status', e.target.value as ContractStatus)}
              >
                {Object.values(ContractStatus).map((status) => (
                  <option key={status} value={status}>
                    {CONTRACT_STATUS_LABELS[status]}
                  </option>
                ))}
              </Select>
            </FormField>

            <FormField
              label="Working schedule"
              htmlFor="contract_schedule"
              className="sm:col-span-2"
              hint="Overrides the employee's default schedule for this contract's period."
            >
              <Select
                id="contract_schedule"
                value={values.working_schedule_id ?? ''}
                onChange={(e) => set('working_schedule_id', e.target.value || null)}
              >
                <option value="">— Use the employee's default —</option>
                {schedules?.items.map((schedule) => (
                  <option key={schedule.id} value={schedule.id}>
                    {schedule.name} ({schedule.weekly_hours}h/week)
                  </option>
                ))}
              </Select>
            </FormField>

            <FormField
              label="Salary structure"
              htmlFor="salary_structure"
              className="sm:col-span-2"
              hint="Salary structures are authored in Phase 3; leave empty until then."
            >
              <Input
                id="salary_structure"
                value={values.salary_structure_id ?? ''}
                onChange={(e) => set('salary_structure_id', e.target.value || null)}
                placeholder="sstr_…"
              />
            </FormField>
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? 'Saving…' : isEdit ? 'Save changes' : 'Create contract'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
