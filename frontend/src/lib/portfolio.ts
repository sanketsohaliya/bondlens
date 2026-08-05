// Portfolio data + the pure math behind the page's charts and grid.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { LotInput, PortfolioMonth, PortfolioPosition } from "../api/types";

const QK = ["portfolio"] as const;

export function usePortfolio() {
  return useQuery({ queryKey: QK, queryFn: api.portfolio.get });
}

/** Lot mutations; portfolio and the ◆ held-markers refresh together. */
export function useLotMutations() {
  const qc = useQueryClient();
  const onSettled = () => {
    qc.invalidateQueries({ queryKey: QK });
    qc.invalidateQueries({ queryKey: ["lists"] });
  };
  const add = useMutation({
    mutationFn: ({ isin, lot }: { isin: string; lot: LotInput }) => api.portfolio.addLot(isin, lot),
    onSettled,
  });
  const update = useMutation({
    mutationFn: ({ id, lot }: { id: number; lot: LotInput }) => api.portfolio.updateLot(id, lot),
    onSettled,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.portfolio.deleteLot(id),
    onSettled,
  });
  return { add, update, remove };
}

// --- pure helpers (unit-tested) --------------------------------------------

export interface YearRow {
  year: number;
  total: number;
  /** Jan..Dec; null = no payment that month. */
  months: (PortfolioMonth | null)[];
}

/** Monthly buckets -> year rows with 12 cells each (IndiaBonds-style grid). */
export function monthGrid(cashflows: PortfolioMonth[]): YearRow[] {
  const years = new Map<number, YearRow>();
  for (const cf of cashflows) {
    const year = Number(cf.month.slice(0, 4));
    const m = Number(cf.month.slice(5, 7)) - 1;
    let row = years.get(year);
    if (!row) {
      row = { year, total: 0, months: Array(12).fill(null) };
      years.set(year, row);
    }
    row.months[m] = cf;
    row.total += cf.interest + cf.principal;
  }
  return [...years.values()]
    .map((r) => ({ ...r, total: Math.round(r.total * 100) / 100 }))
    .sort((a, b) => a.year - b.year);
}

export interface Slice {
  label: string;
  value: number;
  pct: number;
  isins: string[];
}

const BY = {
  issuer: (p: PortfolioPosition) => p.issuer_name ?? p.isin,
  rating: (p: PortfolioPosition) => p.rating_grade ?? "Unrated",
  sector: (p: PortfolioPosition) => p.sector ?? "Other",
} as const;

export type WeightBy = keyof typeof BY;

/** Value weights grouped by issuer/rating/sector; top-N + "Others". */
export function weights(positions: PortfolioPosition[], by: WeightBy, topN = 8): Slice[] {
  const groups = new Map<string, { value: number; isins: string[] }>();
  let total = 0;
  for (const p of positions) {
    const value = p.value ?? 0;
    if (value <= 0) continue;
    total += value;
    const label = BY[by](p);
    const g = groups.get(label) ?? { value: 0, isins: [] };
    g.value += value;
    g.isins.push(p.isin);
    groups.set(label, g);
  }
  if (total === 0) return [];
  const sorted = [...groups.entries()]
    .map(([label, g]) => ({ label, value: g.value, isins: g.isins }))
    .sort((a, b) => b.value - a.value);
  const head = sorted.slice(0, topN);
  const tail = sorted.slice(topN);
  if (tail.length > 0) {
    head.push({
      label: "Others",
      value: tail.reduce((s, x) => s + x.value, 0),
      isins: tail.flatMap((x) => x.isins),
    });
  }
  return head.map((s) => ({ ...s, pct: Math.round((s.value / total) * 1000) / 10 }));
}
