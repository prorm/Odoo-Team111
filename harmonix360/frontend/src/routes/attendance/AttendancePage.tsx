import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { useHrCollection, useHrWrite } from "@/hooks/useAttendanceTimeOff";
import { HR_ROLES, hasRole } from "@/types/enums";
import type { Attendance } from "@/types/attendance-time-off";
import {
  EmployeePicker,
  ErrorMessage,
  Field,
  Pagination,
  localDateTime,
  stamp,
} from "../hr-shared";

export function AttendancePage() {
  const { data: user } = useCurrentUser();
  const hr = hasRole(user?.role, HR_ROLES);
  const [params, setParams] = useSearchParams();
  const employee = params.get("employee") ?? "";
  const [offset, setOffset] = useState(0);
  const records = useHrCollection<Attendance>("attendance", employee, offset);
  const write = useHrWrite("attendance");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Attendance>();
  const [selectedEmployee, setSelectedEmployee] = useState("");
  const [checkIn, setCheckIn] = useState("");
  const [checkOut, setCheckOut] = useState("");
  const [reason, setReason] = useState("");
  const [deleting, setDeleting] = useState<Attendance>();
  const [message, setMessage] = useState("");
  const ownId = user?.employee_id ?? "";
  function showForm(row?: Attendance) {
    write.reset();
    setEditing(row);
    setSelectedEmployee(row?.employee.id ?? employee);
    setCheckIn(localDateTime(row?.check_in ?? new Date().toISOString()));
    setCheckOut(localDateTime(row?.check_out));
    setReason("");
    setOpen(true);
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    try {
      await write.mutateAsync({
        path: editing?.id ?? "",
        method: editing ? "PATCH" : "POST",
        values: {
          check_in: new Date(checkIn).toISOString(),
          check_out: checkOut ? new Date(checkOut).toISOString() : null,
          ...(editing
            ? { version: editing.version, correction_reason: reason }
            : { employee_id: hr ? selectedEmployee : ownId }),
        },
      });
      setOpen(false);
      setMessage(editing ? "Attendance corrected." : "Attendance recorded.");
    } catch {
      /* the error remains beside the form */
    }
  }
  async function checkOutNow(row: Attendance) {
    try {
      await write.mutateAsync({
        path: `${row.id}/check-out`,
        values: { version: row.version },
      });
      setMessage("Checked out.");
    } catch {}
  }
  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Attendance</h1>
          <p className="mt-1 text-sm text-slate-400">
            {hr
              ? "Review attendance and correct entries with an audit reason."
              : "Record your attendance and see your worked hours."}
          </p>
        </div>
        <div className="flex gap-2">
          {!hr && ownId && (
            <Button
              disabled={write.isPending}
              onClick={async () => {
                try {
                  await write.mutateAsync({
                    path: "check-in",
                    values: { employee_id: ownId },
                  });
                  setMessage("Checked in.");
                } catch {}
              }}
            >
              Check in now
            </Button>
          )}
          <Button disabled={!hr && !ownId} onClick={() => showForm()}>
            New attendance
          </Button>
        </div>
      </header>
      {!hr && user && !ownId && (
        <p role="status">
          No employee is linked to this login. Ask your administrator to link
          your employee profile.
        </p>
      )}
      {hr && (
        <div className="max-w-md">
          <EmployeePicker
            optional
            value={employee}
            onChange={(id) => {
              setParams(id ? { employee: id } : {});
              setOffset(0);
            }}
          />
        </div>
      )}
      <p className="text-xs text-slate-500">
        Times display in your browser timezone. Status uses the UTC working
        schedule; worked hours are elapsed time.
      </p>
      {message && (
        <p role="status" className="text-sm text-emerald-300">
          {message}
        </p>
      )}
      <ErrorMessage error={records.error} />
      {!open && <ErrorMessage error={write.error} />}
      {records.isLoading ? (
        <p>Loading attendance…</p>
      ) : (
        <div className="rounded-xl border border-slate-800 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                {[
                  "Employee",
                  "Check in",
                  "Check out",
                  "Hours",
                  "Status",
                  "Actions",
                ].map((s) => (
                  <TableHead key={s}>{s}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.data?.items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>
                    {row.employee.first_name} {row.employee.last_name}
                  </TableCell>
                  <TableCell>{stamp(row.check_in)}</TableCell>
                  <TableCell>{stamp(row.check_out)}</TableCell>
                  <TableCell>{row.worked_hours ?? "—"}</TableCell>
                  <TableCell>
                    <span
                      className={
                        row.status === "missing_checkout"
                          ? "text-amber-300"
                          : ""
                      }
                    >
                      {row.status.replaceAll("_", " ")}
                    </span>
                    {row.correction_reason && (
                      <p className="mt-1 text-xs text-slate-500">
                        Correction: {row.correction_reason}
                      </p>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      {!row.check_out && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={write.isPending}
                          onClick={() => checkOutNow(row)}
                        >
                          Check out
                        </Button>
                      )}
                      {hr && (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => showForm(row)}
                          >
                            Correct
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              write.reset();
                              setDeleting(row);
                            }}
                          >
                            Delete
                          </Button>
                        </>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {records.data?.items.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6}>
                    No attendance yet. Create an entry to get started.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}
      <Pagination
        offset={offset}
        total={records.data?.total ?? 0}
        onChange={setOffset}
      />
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogTitle>
            {editing ? "Correct attendance" : "New attendance"}
          </DialogTitle>
          <form onSubmit={save} className="mt-5 space-y-4">
            {hr && !editing && (
              <EmployeePicker
                value={selectedEmployee}
                onChange={setSelectedEmployee}
              />
            )}
            <Field label="Check in">
              <Input
                type="datetime-local"
                required
                value={checkIn}
                onChange={(e) => setCheckIn(e.target.value)}
              />
            </Field>
            <Field label="Check out (optional)">
              <Input
                type="datetime-local"
                value={checkOut}
                onChange={(e) => setCheckOut(e.target.value)}
              />
            </Field>
            {editing && (
              <Field label="Correction reason">
                <Input
                  required
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </Field>
            )}
            <ErrorMessage error={write.error} />
            <Button disabled={write.isPending} type="submit">
              {write.isPending ? "Saving…" : "Save attendance"}
            </Button>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(o) => {
          if (!o) setDeleting(undefined);
        }}
      >
        <DialogContent>
          <DialogTitle>Delete attendance?</DialogTitle>
          <p className="my-4 text-sm text-slate-400">
            Remove this entry from active attendance. Its audit history remains
            available.
          </p>
          <ErrorMessage error={write.error} />
          <Button
            disabled={write.isPending}
            onClick={async () => {
              if (deleting)
                try {
                  await write.mutateAsync({
                    path: `${deleting.id}?version=${deleting.version}`,
                    method: "DELETE",
                  });
                  setDeleting(undefined);
                  setMessage("Attendance deleted.");
                } catch {}
            }}
          >
            Confirm delete
          </Button>
        </DialogContent>
      </Dialog>
    </div>
  );
}
