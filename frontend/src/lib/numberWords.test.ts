import { describe, expect, it } from "vitest";
import { replaceNumberWords, wordsToNumber } from "./numberWords";

describe("wordsToNumber", () => {
  it("parses plain numbers", () => {
    expect(wordsToNumber("five")).toBe(5);
    expect(wordsToNumber("fifty")).toBe(50);
    expect(wordsToNumber("twenty two")).toBe(22);
    expect(wordsToNumber("one hundred two")).toBe(102);
  });

  it("parses Indian scales", () => {
    expect(wordsToNumber("one lakh")).toBe(100000);
    expect(wordsToNumber("two crores")).toBe(20000000);
    expect(wordsToNumber("one thousand one hundred sixty five")).toBe(1165);
  });

  it("parses the NSDL redemption phrase", () => {
    expect(wordsToNumber("twenty two thousand one hundred two decimal fifty")).toBe(22102.5);
  });

  it("parses decimals with 'point'", () => {
    expect(wordsToNumber("nine point five")).toBe(9.5);
    expect(wordsToNumber("nine point twenty five")).toBe(9.25);
  });

  it("rejects non-numbers", () => {
    expect(wordsToNumber("redemption date")).toBeNull();
    expect(wordsToNumber("")).toBeNull();
    expect(wordsToNumber("and")).toBeNull();
  });
});

describe("replaceNumberWords", () => {
  it("rewrites the NSDL redemption sentence", () => {
    expect(
      replaceNumberWords(
        "Rupees Twenty Two Thousand One Hundred Two Decimal Fifty per " +
          "Debenture to be paid on the Redemption Date",
      ),
    ).toBe("₹22,102.5 per Debenture to be paid on the Redemption Date");
  });

  it("handles Rs. and INR prefixes", () => {
    expect(replaceNumberWords("Rs. One Lakh only")).toBe("₹1,00,000 only");
    expect(replaceNumberWords("INR five crores raised")).toBe("₹5,00,00,000 raised");
  });

  it("leaves prose without amounts untouched", () => {
    const s = "Secured by first pari passu charge on receivables";
    expect(replaceNumberWords(s)).toBe(s);
  });

  it("does not eat a trailing 'and' clause", () => {
    expect(replaceNumberWords("Rupees fifty and the premium")).toBe("₹50 and the premium");
  });
});
