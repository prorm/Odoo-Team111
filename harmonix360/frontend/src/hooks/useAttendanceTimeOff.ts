import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchApi, queryString } from "@/lib/api-client";
import { useCurrentUser } from "./useCurrentUser";
import { HR_ROLES, hasRole } from "@/types/enums";
import type { PaginatedResponse } from "@/types/common";

export function useHrCollection<T>(
  resource: string,
  employee?: string,
  offset = 0,
) {
  const { data: user } = useCurrentUser();
  const hr = hasRole(user?.role, HR_ROLES);
  return useQuery({
    queryKey: [resource, hr, employee, offset, user?.id],
    queryFn: () =>
      fetchApi<PaginatedResponse<T>>(
        `/${resource}/${hr ? "" : resource === "time-off-types" ? "lookup" : "me"}${queryString({ employee_id: hr ? employee : undefined, limit: 50, offset })}`,
      ),
    enabled: Boolean(user),
  });
}
export function useHrWrite(resource: string) {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: ({
      path = "",
      method = "POST",
      values,
    }: {
      path?: string;
      method?: string;
      values?: unknown;
    }) =>
      fetchApi(`/${resource}/${path}`, {
        method,
        ...(values === undefined ? {} : { body: JSON.stringify(values) }),
      }),
    onSuccess: async () => {
      await Promise.all(
        [
          "attendance",
          "time-off-types",
          "time-off-allocations",
          "time-off-requests",
          "employees",
        ].map((key) => cache.invalidateQueries({ queryKey: [key] })),
      );
    },
  });
}
