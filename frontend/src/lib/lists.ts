// Per-user bond lists (watchlist / holdings), stored server-side.
//
// One query holds both lists; toggles are optimistic mutations so the
// star/diamond flips instantly and rolls back if the server disagrees.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { api } from "../api/client";
import type { ListName, ListsResponse } from "../api/types";

export const WATCHLIST_KEY: ListName = "watchlist";
export const HOLDINGS_KEY: ListName = "holdings";

const QK = ["lists"] as const;
const EMPTY: string[] = [];

/** A server-backed per-user ISIN list, shared live across components. */
export function useStoredList(key: ListName) {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: QK, queryFn: api.lists.get });
  const items = query.data?.[key] ?? EMPTY;

  const mutation = useMutation({
    mutationFn: ({ isin, present }: { isin: string; present: boolean }) =>
      present ? api.lists.add(key, isin) : api.lists.remove(key, isin),
    onMutate: async ({ isin, present }) => {
      await qc.cancelQueries({ queryKey: QK });
      const prev = qc.getQueryData<ListsResponse>(QK);
      if (prev) {
        qc.setQueryData<ListsResponse>(QK, {
          ...prev,
          [key]: present ? [...prev[key], isin] : prev[key].filter((x) => x !== isin),
        });
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(QK, ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: QK }),
  });

  const has = useCallback((isin: string) => items.includes(isin), [items]);
  const toggle = useCallback(
    (isin: string) => mutation.mutate({ isin, present: !items.includes(isin) }),
    [items, mutation],
  );
  return { items, has, toggle };
}
