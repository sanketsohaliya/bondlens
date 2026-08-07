import { describe, expect, it } from "vitest";
import { fuzzyMatch } from "./fuzzy";

describe("fuzzyMatch", () => {
  it("matches exact and substring queries", () => {
    expect(fuzzyMatch("muthoot", "MUTHOOT MICROFIN LIMITED")).toBe(true);
    expect(fuzzyMatch("microfin", "MUTHOOT MICROFIN LIMITED")).toBe(true);
  });

  it("matches subsequences with missing letters", () => {
    expect(fuzzyMatch("mthoot", "MUTHOOT MICROFIN LIMITED")).toBe(true);
    expect(fuzzyMatch("kif", "KERALA INFRASTRUCTURE INVEST")).toBe(true);
  });

  it("is case-insensitive both ways", () => {
    expect(fuzzyMatch("MUTHOOT", "muthoot microfin")).toBe(true);
    expect(fuzzyMatch("ine04", "INE046W07321")).toBe(true);
  });

  it("rejects out-of-order or absent characters", () => {
    expect(fuzzyMatch("toothum", "MUTHOOT")).toBe(false);
    expect(fuzzyMatch("xyz", "MUTHOOT")).toBe(false);
  });

  it("ignores spaces in the query and matches everything when empty", () => {
    expect(fuzzyMatch("kerala infra", "KERALAINFRASTRUCTURE")).toBe(true);
    expect(fuzzyMatch("", "anything")).toBe(true);
    expect(fuzzyMatch(" ", "anything")).toBe(true);
  });
});
