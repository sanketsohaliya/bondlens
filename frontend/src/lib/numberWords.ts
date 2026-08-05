/** NSDL free-text fields spell amounts out in words ("Rupees Twenty Two
 * Thousand One Hundred Two Decimal Fifty per Debenture"). Rewrite such
 * phrases as numerals (₹22,102.50) for display. Indian scales included.
 */

const SMALL: Record<string, number> = {
  zero: 0,
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
  eleven: 11,
  twelve: 12,
  thirteen: 13,
  fourteen: 14,
  fifteen: 15,
  sixteen: 16,
  seventeen: 17,
  eighteen: 18,
  nineteen: 19,
};
const TENS: Record<string, number> = {
  twenty: 20,
  thirty: 30,
  forty: 40,
  fifty: 50,
  sixty: 60,
  seventy: 70,
  eighty: 80,
  ninety: 90,
};
const SCALES: Record<string, number> = {
  hundred: 100,
  thousand: 1e3,
  lakh: 1e5,
  lakhs: 1e5,
  lac: 1e5,
  lacs: 1e5,
  million: 1e6,
  crore: 1e7,
  crores: 1e7,
};

const WORD =
  "(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|" +
  "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|" +
  "thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakhs?|" +
  "lacs?|crores?|million|and|decimal|point)";
const PHRASE = new RegExp(`\\b(rupees|rs\\.?|inr)\\s+(${WORD}(?:[\\s-]+${WORD})*)\\b`, "gi");

/** "twenty two thousand one hundred two decimal fifty" -> 22102.5;
 * null when the phrase does not reduce to a number. */
export function wordsToNumber(phrase: string): number | null {
  const tokens = phrase
    .toLowerCase()
    .split(/[\s-]+/)
    .filter(Boolean);
  let total = 0;
  let current = 0;
  let seenNumber = false;
  let i = 0;
  for (; i < tokens.length; i++) {
    const tok = tokens[i];
    if (tok === "and") continue;
    if (tok === "decimal" || tok === "point") break;
    if (tok in SMALL) {
      current += SMALL[tok];
      seenNumber = true;
    } else if (tok in TENS) {
      current += TENS[tok];
      seenNumber = true;
    } else if (tok === "hundred") {
      current = (current || 1) * 100;
      seenNumber = true;
    } else if (tok in SCALES) {
      total += (current || 1) * SCALES[tok];
      current = 0;
      seenNumber = true;
    } else {
      return null;
    }
  }
  if (!seenNumber) return null;
  let value = total + current;
  if (i < tokens.length && (tokens[i] === "decimal" || tokens[i] === "point")) {
    const fracTokens = tokens.slice(i + 1).filter((t) => t !== "and");
    if (fracTokens.length > 0) {
      const frac = wordsToNumber(fracTokens.join(" "));
      if (frac != null && frac >= 0) {
        value += frac / 10 ** String(frac).length;
      }
    }
  }
  return value;
}

/** Replace "Rupees <words>" phrases in prose with "₹<numeral>". Text
 * without such phrases is returned unchanged. */
export function replaceNumberWords(text: string): string {
  return text.replace(PHRASE, (match, _prefix: string, phrase: string) => {
    // Trailing "and" belongs to the surrounding sentence, not the number.
    const trailingAnd = /[\s-]+and$/i.test(phrase);
    const cleaned = phrase.replace(/[\s-]+and$/i, "");
    const n = wordsToNumber(cleaned);
    if (n == null) return match;
    const num = `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
    return trailingAnd ? `${num} and` : num;
  });
}
