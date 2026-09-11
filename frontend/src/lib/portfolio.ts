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

/** "YYYY-MM" strictly before the current month (the current month still
 * carries projected flows, so it is NOT past). */
export function isPastMonth(month: string, now: Date = new Date()): boolean {
  const cur = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  return month < cur;
}

/** All of a popover row's dates (ISO yyyy-mm-dd) are in the past — the
 * money is banked, so the row reads as history. */
export function allDatesPast(dates: string[] | null | undefined, now: Date = new Date()): boolean {
  if (!dates?.length) return false;
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
    now.getDate(),
  ).padStart(2, "0")}`;
  return dates.every((d) => d < today);
}

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

/** Received vs projected income from ONE bond across the month buckets.
 * Month-level approximation: the API splits received/projected per month,
 * not per bond, so the current month (which may straddle today) counts
 * fully as projected. */
export function bondIncome(
  cashflows: PortfolioMonth[],
  isin: string,
  now: Date = new Date(),
): { received: number; projected: number } {
  let received = 0;
  let projected = 0;
  for (const m of cashflows) {
    const b = m.bonds.find((x) => x.isin === isin);
    if (!b) continue;
    if (isPastMonth(m.month, now)) received += b.interest + b.principal;
    else projected += b.interest + b.principal;
  }
  return {
    received: Math.round(received * 100) / 100,
    projected: Math.round(projected * 100) / 100,
  };
}

export function portfolioYieldSpread(
  position: Pick<PortfolioPosition, "ytm_effective" | "yield_on_cost">,
): number | null {
  const market = position.ytm_effective;
  const cost = position.yield_on_cost;
  if (market == null || cost == null || !Number.isFinite(market) || !Number.isFinite(cost))
    return null;
  return market - cost;
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
