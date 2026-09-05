import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
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
import {
  useOfflineMutation,
  useOfflineReferenceList,
  usePendingOfflineMutations,
} from "@/hooks/useOfflineMutation";
import { HR_ROLES, hasRole } from "@/types/enums";
import type {
  TimeOffType,
  TimeOffAllocation,
  TimeOffRequest,
} from "@/types/attendance-time-off";
import { EmployeePicker, ErrorMessage, Field, Pagination } from "../hr-shared";

type Section = "requests" | "allocations" | "types";
type Row = TimeOffRequest | TimeOffAllocation | TimeOffType;
const captions = {
  requests: "Requests",
  allocations: "Allocations",
  types: "Types",
};
const singular = {
  requests: "request",
  allocations: "allocation",
  types: "type",
};

export function TimeOffPage() {
  const { data: user } = useCurrentUser();
  const hr = hasRole(user?.role, HR_ROLES);
  const [params, setParams] = useSearchParams();
  const section: Section =
    params.get("tab") === "allocations"
      ? "allocations"
      : params.get("tab") === "types"
        ? "types"
        : "requests";
  const employee = params.get("employee") ?? "";
  const [offset, setOffset] = useState(0);
  const resource = `time-off-${section}`;
  const records = useHrCollection<Row>(resource, employee, offset);
  const write = useHrWrite(resource);
  // Architecture §8.3: an Employee SUBMITTING their own request is syncable;
  // approving or refusing one is not, and never will be — approval debits a
  // live allocation inside a transaction, and a device holding a stale copy
  // of that balance must not get to make the decision.
  // Warm the leave-type cache from the PAGE, not only from the form. The form
  // mounts on demand, so a person who never opened it while online would find
  // an empty dropdown the first time they tried offline — which is the one
  // moment it needs to work. Same query key as the form's, so this is one
  // fetch, not two.
  useOfflineReferenceList<TimeOffType>(
    "time_off_type_ref",
    "/time-off-types/lookup?limit=200",
    { enabled: section !== "types" },
  );
  const offlineRequests = useOfflineMutation("time_off_request");
  const queuedRequests = usePendingOfflineMutations("time_off_request");
  const [editing, setEditing] = useState<Row>();
  const [open, setOpen] = useState(false);
  const [decision, setDecision] = useState<{
    row: TimeOffRequest;
    action: "approve" | "refuse";
  }>();
  const [note, setNote] = useState("");
  const [deleting, setDeleting] = useState<Row>();
  const [message, setMessage] = useState("");
  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
    setOffset(0);
    write.reset();
    setMessage("");
  }
  function showForm(row?: Row) {
    write.reset();
    setEditing(row);
    setOpen(true);
  }
  const rows = records.data?.items ?? [];
  const name = (row: TimeOffRequest | TimeOffAllocation) =>
    `${row.employee.first_name} ${row.employee.last_name}`;
  return (
    <div className="space-y-5">
      <header className="flex flex-wrap justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Time Off</h1>
          <p className="mt-1 text-sm text-slate-400">
            Requests, leave policies, and available balances.
          </p>
        </div>
        {(hr || section === "requests") && (
          <Button
            disabled={!hr && !user?.employee_id}
            onClick={() => showForm()}
          >
            New {singular[section]}
          </Button>
        )}
      </header>
      <nav aria-label="Time off sections" className="flex gap-2">
        {(["requests", "allocations", "types"] as Section[]).map((tab) => (
          <Button
            key={tab}
            variant={tab === section ? "default" : "outline"}
            aria-current={tab === section ? "page" : undefined}
            onClick={() => setFilter("tab", tab)}
          >
            {captions[tab]}
          </Button>
        ))}
      </nav>
      {hr && section !== "types" && (
        <div className="max-w-md">
          <EmployeePicker
            optional
            value={employee}
            onChange={(id) => setFilter("employee", id)}
          />
        </div>
      )}
      <p className="text-sm text-slate-400">
        {section === "requests"
          ? "Pending requests do not reserve balance. Available leave is checked when approved."
          : section === "allocations"
            ? "Remaining balance is allocated minus taken. Only confirmed allocations covering the full request can be used."
            : "Define how each leave type is measured, approved, and included in payroll."}
      </p>
      {message && (
        <p role="status" className="text-sm text-emerald-300">
          {message}
        </p>
      )}
      <ErrorMessage error={records.error} />
      {section === "requests" && (queuedRequests.data?.length ?? 0) > 0 && (
        <div
          data-testid="time-off-pending-sync"
          className="rounded-lg border border-amber-800/50 bg-amber-950/25 p-3 text-sm text-amber-200"
        >
          <p className="font-medium">
            {queuedRequests.data!.length} request
            {queuedRequests.data!.length === 1 ? "" : "s"} waiting to sync
          </p>
          <ul className="mt-1 space-y-0.5 text-xs opacity-90">
            {queuedRequests.data!.map((row) => (
              <li key={row.client_mutation_id}>
                {String(row.payload?.date_from ?? "")} to{" "}
                {String(row.payload?.date_to ?? "")} — saved on this device, not
                yet on the server
              </li>
            ))}
          </ul>
        </div>
      )}
      {!open && !decision && !deleting && <ErrorMessage error={write.error} />}
      {records.isLoading ? (
        <p>Loading time off…</p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-800">
          <Table>
            <TableHeader>
              <TableRow>
                {(section === "requests"
                  ? [
                      "Employee / Type",
                      "Dates",
                      "Duration",
                      "Status / Reason",
                      "Actions",
                    ]
                  : section === "allocations"
                    ? [
                        "Employee / Type",
                        "Allocated / Taken",
                        "Remaining",
                        "Validity / Status",
                        "Actions",
                      ]
                    : [
                        "Name / Code",
                        "Unit",
                        "Allocation",
                        "Approval / Payroll",
                        "Actions",
                      ]
                ).map((s) => (
                  <TableHead key={s}>{s}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  {section === "requests" &&
                    (() => {
                      const r = row as TimeOffRequest;
                      return (
                        <>
                          <TableCell>
                            {name(r)}
                            <p className="text-xs text-slate-500">
                              {r.time_off_type.name}
                            </p>
                          </TableCell>
                          <TableCell>
                            {r.date_from} → {r.date_to}
                          </TableCell>
                          <TableCell>
                            {r.duration} {r.time_off_type.unit}
                          </TableCell>
                          <TableCell>
                            {r.status === "to_approve"
                              ? "Pending approval"
                              : r.status}
                            <p className="text-xs text-slate-500">{r.reason}</p>
                            {r.decision_note && (
                              <p className="text-xs text-slate-400">
                                Decision: {r.decision_note}
                              </p>
                            )}
                          </TableCell>
                        </>
                      );
                    })()}
                  {section === "allocations" &&
                    (() => {
                      const r = row as TimeOffAllocation;
                      return (
                        <>
                          <TableCell>
                            {name(r)}
                            <p className="text-xs text-slate-500">
                              {r.time_off_type.name}
                            </p>
                          </TableCell>
                          <TableCell>
                            {r.allocated} / {r.taken} {r.time_off_type.unit}
                          </TableCell>
                          <TableCell className="font-semibold text-emerald-300">
                            {r.remaining} {r.time_off_type.unit}
                          </TableCell>
                          <TableCell>
                            {r.valid_from} → {r.valid_to ?? "No expiry"}
                            <p className="text-xs text-slate-500">{r.status}</p>
                          </TableCell>
                        </>
                      );
                    })()}
                  {section === "types" &&
                    (() => {
                      const r = row as TimeOffType;
                      return (
                        <>
                          <TableCell>
                            {r.name}
                            <p className="text-xs text-slate-500">{r.code}</p>
                          </TableCell>
                          <TableCell>{r.unit}</TableCell>
                          <TableCell>
                            {r.requires_allocation
                              ? "Required"
                              : "Not required"}
                          </TableCell>
                          <TableCell>
                            {r.requires_approval
                              ? "HR approval"
                              : "Automatic approval"}
                            <p className="text-xs text-slate-500">
                              {r.payroll_integration
                                ? "Included in payroll"
                                : "Excluded from payroll"}
                            </p>
                          </TableCell>
                        </>
                      );
                    })()}
                  <TableCell>
                    <div className="flex flex-wrap gap-2">
                      {hr &&
                        section === "requests" &&
                        (row as TimeOffRequest).status === "to_approve" && (
                          <>
                            {(["approve", "refuse"] as const).map((action) => (
                              <Button
                                key={action}
                                size="sm"
                                variant={
                                  action === "approve" ? "default" : "outline"
                                }
                                onClick={() => {
                                  write.reset();
                                  setNote("");
                                  setDecision({
                                    row: row as TimeOffRequest,
                                    action,
                                  });
                                }}
                              >
                                {action === "approve" ? "Approve" : "Refuse"}
                              </Button>
                            ))}
                          </>
                        )}
                      {hr &&
                        (section !== "requests" ||
                          (row as TimeOffRequest).status === "to_approve") && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => showForm(row)}
                          >
                            Edit
                          </Button>
                        )}
                      {hr &&
                        (section !== "requests" ||
                          (row as TimeOffRequest).status !== "approved") && (
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
                        )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5}>
                    No {captions[section].toLowerCase()} found.
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
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogTitle>
            {editing ? "Edit" : "New"} {singular[section]}
          </DialogTitle>
          {open && (
            <TimeOffForm
              section={section}
              row={editing}
              employee={hr ? employee : (user?.employee_id ?? "")}
              hr={hr}
              pending={write.isPending}
              error={write.error}
              onSave={async (values) => {
                if (!editing && !hr && section === "requests") {
                  await offlineRequests.create(
                    values as Record<string, unknown>,
                  );
                  setOpen(false);
                  setMessage(
                    "Request submitted. It will sync automatically if you are offline.",
                  );
                  return;
                }
                await write.mutateAsync({
                  path: editing?.id ?? "",
                  method: editing ? "PATCH" : "POST",
                  values,
                });
                setOpen(false);
                setMessage("Saved successfully.");
              }}
            />
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(decision)}
        onOpenChange={(o) => {
          if (!o) setDecision(undefined);
        }}
      >
        <DialogContent>
          <DialogTitle>
            {decision?.action === "approve"
              ? "Approve request"
              : "Refuse request"}
          </DialogTitle>
          <p className="my-4 text-sm text-slate-400">
            {decision?.row.duration} {decision?.row.time_off_type.unit} for{" "}
            {decision ? name(decision.row) : ""}.{" "}
            {decision?.action === "approve"
              ? "Required balance will be deducted on approval."
              : "Refusal leaves the balance unchanged."}
          </p>
          <Field label="Decision note (optional)">
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <ErrorMessage error={write.error} />
          <Button
            className="mt-4"
            disabled={write.isPending}
            onClick={async () => {
              if (decision)
                try {
                  await write.mutateAsync({
                    path: `${decision.row.id}/${decision.action}`,
                    values: {
                      version: decision.row.version,
                      decision_note: note || null,
                    },
                  });
                  setMessage(
                    decision.action === "approve"
                      ? "Request approved. Balance updated."
                      : "Request refused. Balance unchanged.",
                  );
                  setDecision(undefined);
                } catch {}
            }}
          >
            Confirm {decision?.action}
          </Button>
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(o) => {
          if (!o) setDeleting(undefined);
        }}
      >
        <DialogContent>
          <DialogTitle>Delete {singular[section]}?</DialogTitle>
          <p className="my-4 text-sm text-slate-400">
            Used allocations and types are protected. Audit history is retained.
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
                  setMessage("Deleted successfully.");
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

function TimeOffForm({
  section,
  row,
  employee,
  hr,
  pending,
  error,
  onSave,
}: {
  section: Section;
  row?: Row;
  employee: string;
  hr: boolean;
  pending: boolean;
  error: unknown;
  onSave: (v: unknown) => Promise<void>;
}) {
  const type =
    section === "types" ? (row as TimeOffType | undefined) : undefined;
  const alloc =
    section === "allocations"
      ? (row as TimeOffAllocation | undefined)
      : undefined;
  const req =
    section === "requests" ? (row as TimeOffRequest | undefined) : undefined;
  const [employeeId, setEmployeeId] = useState(
    alloc?.employee.id ?? req?.employee.id ?? employee,
  );
  const [typeId, setTypeId] = useState(
    alloc?.time_off_type.id ?? req?.time_off_type.id ?? "",
  );
  const [name, setName] = useState(type?.name ?? "");
  const [code, setCode] = useState(type?.code ?? "");
  const [unit, setUnit] = useState(type?.unit ?? "days");
  const [requiresAllocation, setRequiresAllocation] = useState(
    type?.requires_allocation ?? true,
  );
  const [requiresApproval, setRequiresApproval] = useState(
    type?.requires_approval ?? true,
  );
  const [payroll, setPayroll] = useState(type?.payroll_integration ?? false);
  const [description, setDescription] = useState(type?.description ?? "");
  const [amount, setAmount] = useState(alloc?.allocated ?? "");
  const [status, setStatus] = useState(alloc?.status ?? "confirmed");
  const [start, setStart] = useState(alloc?.valid_from ?? req?.date_from ?? "");
  const [end, setEnd] = useState(alloc?.valid_to ?? req?.date_to ?? "");
  const [reason, setReason] = useState(req?.reason ?? "");
  // Cached in IndexedDB so the form is fillable with no network. Not a
  // syncable entity — see `useOfflineReferenceList`.
  const types = useOfflineReferenceList<TimeOffType>(
    "time_off_type_ref",
    "/time-off-types/lookup?limit=200",
    { enabled: section !== "types" },
  );
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const values =
      section === "types"
        ? {
            name,
            code,
            unit,
            requires_allocation: requiresAllocation,
            requires_approval: requiresApproval,
            payroll_integration: payroll,
            description: description || null,
          }
        : section === "allocations"
          ? {
              allocated: amount,
              valid_from: start,
              valid_to: end || null,
              status,
              ...(!row
                ? { employee_id: employeeId, time_off_type_id: typeId }
                : {}),
            }
          : {
              employee_id: employeeId,
              time_off_type_id: typeId,
              date_from: start,
              date_to: end,
              reason: reason || null,
            };
    try {
      await onSave({ ...values, ...(row ? { version: row.version } : {}) });
    } catch {}
  }
  return (
    <form onSubmit={submit} className="mt-5 space-y-4">
      {section === "types" ? (
        <>
          <Field label="Name">
            <Input
              required
              maxLength={180}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="Code">
            <Input
              required
              maxLength={64}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </Field>
          <Field label="Unit">
            <Select
              value={unit}
              onChange={(e) => setUnit(e.target.value as "days" | "hours")}
            >
              <option value="days">Days</option>
              <option value="hours">Hours</option>
            </Select>
          </Field>
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={requiresAllocation}
              onChange={(e) => setRequiresAllocation(e.target.checked)}
            />
            Requires allocation
          </label>
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={requiresApproval}
              onChange={(e) => setRequiresApproval(e.target.checked)}
            />
            Requires approval
          </label>
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={payroll}
              onChange={(e) => setPayroll(e.target.checked)}
            />
            Payroll integration
          </label>
          <Field label="Description">
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </Field>
        </>
      ) : (
        <>
          {hr && !(section === "allocations" && row) && (
            <EmployeePicker value={employeeId} onChange={setEmployeeId} />
          )}
          {!(section === "allocations" && row) && (
            <Field label="Time off type">
              <Select
                required
                value={typeId}
                onChange={(e) => setTypeId(e.target.value)}
              >
                <option value="">Select type</option>
                {types.data?.items.map((t) => (
                  <option value={t.id} key={t.id}>
                    {t.name} ({t.unit})
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <ErrorMessage error={types.error} />
          {section === "allocations" && (
            <>
              <Field label="Allocated amount">
                <Input
                  required
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                />
              </Field>
              <Field label="Allocation status">
                <Select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                >
                  {["confirmed", "draft", "expired", "cancelled"].map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </Select>
              </Field>
            </>
          )}
          <Field label={section === "allocations" ? "Valid from" : "From date"}>
            <Input
              required
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </Field>
          <Field
            label={
              section === "allocations" ? "Valid to (optional)" : "To date"
            }
          >
            <Input
              required={section === "requests"}
              type="date"
              min={start}
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </Field>
          {section === "requests" && (
            <>
              <Field label="Reason">
                <Input
                  maxLength={2000}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </Field>
              <p className="text-xs text-slate-400">
                Days count inclusively, including weekends. Hours use scheduled
                working hours on these dates. Types without approval are
                approved immediately if sufficient balance exists.
              </p>
            </>
          )}
        </>
      )}
      <ErrorMessage error={error} />
      <Button type="submit" disabled={pending}>
        {pending ? "Saving…" : `Save ${singular[section]}`}
      </Button>
    </form>
  );
}
