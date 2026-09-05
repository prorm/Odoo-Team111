import { cloneElement, useId, useState, type ReactElement } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchApi } from "@/lib/api-client";
import type { EmployeeRef } from "@/types/hr";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function EmployeePicker({
  value,
  onChange,
  optional = false,
}: {
  value: string;
  onChange: (id: string) => void;
  optional?: boolean;
}) {
  const [search, setSearch] = useState("");
  const selectId = useId();
  const { data, error } = useQuery({
    queryKey: ["employees", "lookup", "phase2", search],
    queryFn: () =>
      fetchApi<EmployeeRef[]>(
        `/employees/lookup?limit=50&search=${encodeURIComponent(search)}`,
      ),
  });
  return (
    <div className="block space-y-1 text-sm">
      <label htmlFor={selectId}>Employee</label>
      <Input
        aria-label="Find employee"
        placeholder="Search name or email…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <Select
        id={selectId}
        aria-label="Employee"
        required={!optional}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">
          {optional ? "All employees" : "Select employee"}
        </option>
        {data?.map((e) => (
          <option key={e.id} value={e.id}>
            {e.first_name} {e.last_name} — {e.work_email}
          </option>
        ))}
      </Select>
      {error && <ErrorMessage error={error} />}
    </div>
  );
}
export function ErrorMessage({ error }: { error: unknown }) {
  return error ? (
    <p
      role="alert"
      className="rounded-lg border border-rose-800 bg-rose-950/40 p-3 text-sm text-rose-200"
    >
      {error instanceof Error ? error.message : String(error)}
    </p>
  ) : null;
}
export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactElement<{ id?: string }>;
}) {
  const id = useId();
  return (
    <div className="block space-y-1 text-sm text-slate-300">
      <label htmlFor={id}>{label}</label>
      {cloneElement(children, { id })}
    </div>
  );
}
export function Pagination({
  offset,
  total,
  onChange,
}: {
  offset: number;
  total: number;
  onChange: (n: number) => void;
}) {
  return (
    <div className="flex items-center gap-3 text-sm text-slate-400">
      <Button
        variant="outline"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - 50))}
      >
        Previous
      </Button>
      <span>
        {total === 0
          ? "0 records"
          : `${offset + 1}–${Math.min(offset + 50, total)} of ${total}`}
      </span>
      <Button
        variant="outline"
        disabled={offset + 50 >= total}
        onClick={() => onChange(offset + 50)}
      >
        Next
      </Button>
    </div>
  );
}
export function localDateTime(iso?: string | null) {
  if (!iso) return "";
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
export function stamp(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : "Still checked in";
}
