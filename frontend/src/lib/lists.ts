// Per-user bond lists (named watchlists / holdings), stored server-side.
//
// One query holds everything; toggles are optimistic mutations so the
// star/diamond flips instantly and rolls back if the server disagrees.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { api } from "../api/client";
import type { ListsResponse, Watchlist } from "../api/types";

export const HOLDINGS_KEY = "holdings" as const;

const QK = ["lists"] as const;
const EMPTY: string[] = [];
const NO_LISTS: Watchlist[] = [];

/** The server-backed holdings ISIN list, shared live across components. */
export function useStoredList(_key: typeof HOLDINGS_KEY) {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: QK, queryFn: api.lists.get });
  const items = query.data?.holdings ?? EMPTY;

  const mutation = useMutation({
    mutationFn: ({ isin, present }: { isin: string; present: boolean }) =>
      present ? api.lists.add(HOLDINGS_KEY, isin) : api.lists.remove(HOLDINGS_KEY, isin),
    onMutate: async ({ isin, present }) => {
      await qc.cancelQueries({ queryKey: QK });
      const prev = qc.getQueryData<ListsResponse>(QK);
      if (prev) {
        qc.setQueryData<ListsResponse>(QK, {
          ...prev,
          holdings: present ? [...prev.holdings, isin] : prev.holdings.filter((x) => x !== isin),
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

/** Union of watchlist ISINs: all lists (id == null) or a single one. */
export function unionIsins(lists: Watchlist[], watchlistId: number | null): string[] {
  const src = watchlistId == null ? lists : lists.filter((l) => l.id === watchlistId);
  return [...new Set(src.flatMap((l) => l.isins))];
}

/** Next default name: "Watchlist N" avoiding collisions with existing. */
export function nextListName(lists: Watchlist[]): string {
  const taken = new Set(lists.map((l) => l.name));
  for (let n = lists.length + 1; ; n++) {
    const name = `Watchlist ${n}`;
    if (!taken.has(name)) return name;
  }
}

/** All named watchlists with optimistic membership toggles + CRUD. */
export function useWatchlists() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: QK, queryFn: api.lists.get });
  const lists = query.data?.watchlists ?? NO_LISTS;

  const toggleMutation = useMutation({
    mutationFn: ({ id, isin, present }: { id: number; isin: string; present: boolean }) =>
      present ? api.watchlists.addIsin(id, isin) : api.watchlists.removeIsin(id, isin),
    onMutate: async ({ id, isin, present }) => {
      await qc.cancelQueries({ queryKey: QK });
      const prev = qc.getQueryData<ListsResponse>(QK);
      if (prev) {
        qc.setQueryData<ListsResponse>(QK, {
          ...prev,
          watchlists: prev.watchlists.map((l) =>
            l.id !== id
              ? l
              : {
                  ...l,
                  isins: present ? [...l.isins, isin] : l.isins.filter((x) => x !== isin),
                },
          ),
        });
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(QK, ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: QK }),
  });

  const createMutation = useMutation({
    // Optionally star a bond into the new list in the same action.
    mutationFn: async ({ name, isin }: { name: string; isin?: string }) => {
      const created = await api.watchlists.create(name);
      if (isin) await api.watchlists.addIsin(created.id, isin);
      return created;
    },
    onSettled: () => qc.invalidateQueries({ queryKey: QK }),
  });

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => api.watchlists.rename(id, name),
    onSettled: () => qc.invalidateQueries({ queryKey: QK }),
  });

  const removeMutation = useMutation({
    mutationFn: (id: number) => api.watchlists.remove(id),
    onSettled: () => qc.invalidateQueries({ queryKey: QK }),
  });

  const starred = useCallback((isin: string) => lists.some((l) => l.isins.includes(isin)), [lists]);
  const toggle = useCallback(
    (id: number, isin: string) => {
      const list = lists.find((l) => l.id === id);
      toggleMutation.mutate({ id, isin, present: !list?.isins.includes(isin) });
    },
    [lists, toggleMutation],
  );

  return {
    lists,
    starred,
    toggle,
    create: (name: string, isin?: string) => createMutation.mutate({ name, isin }),
    rename: (id: number, name: string) => renameMutation.mutate({ id, name }),
    remove: (id: number) => removeMutation.mutate(id),
  };
}
