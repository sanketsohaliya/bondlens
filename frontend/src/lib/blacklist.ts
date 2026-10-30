import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BlacklistEntry } from "../api/types";

/** The user's never-auto-buy list, read from the alerts overview (one
 * query shared with the Alerts panel) with put / remove mutations. */
export function useBlacklist(isin?: string) {
  const qc = useQueryClient();
  const overview = useQuery({
    queryKey: ["alerts"],
    queryFn: () => api.alerts.overview(),
    staleTime: 30_000,
  });
  const entries: BlacklistEntry[] = overview.data?.blacklist ?? [];
  const entry = isin ? (entries.find((e) => e.isin === isin) ?? null) : null;
  const invalidate = () => qc.invalidateQueries({ queryKey: ["alerts"] });
  const putM = useMutation({
    mutationFn: ({ isin, note }: { isin: string; note: string | null }) =>
      api.alerts.putBlacklist(isin, { note: note?.trim() || null }),
    onSuccess: invalidate,
  });
  const removeM = useMutation({
    mutationFn: (isin: string) => api.alerts.deleteBlacklist(isin),
    onSuccess: invalidate,
  });
  return {
    ready: overview.isSuccess || overview.isError,
    entries,
    entry,
    put: (note: string | null, target = isin) => target && putM.mutate({ isin: target, note }),
    remove: (target = isin) => target && removeM.mutate(target),
    pending: putM.isPending || removeM.isPending,
    error: (putM.error || removeM.error) as Error | null,
  };
}
