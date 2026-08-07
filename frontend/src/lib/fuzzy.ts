/** Case-insensitive subsequence match: every query char appears in text
 * in order ("mthoot" → "MUTHOOT"). Empty queries match everything. */
export function fuzzyMatch(query: string, text: string): boolean {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  let ti = 0;
  for (const ch of q) {
    if (ch === " ") continue; // spaces are separators, not content
    ti = t.indexOf(ch, ti);
    if (ti === -1) return false;
    ti += 1;
  }
  return true;
}
