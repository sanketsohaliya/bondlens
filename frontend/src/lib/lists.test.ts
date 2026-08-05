import { describe, expect, it } from "vitest";
import { nextListName, unionIsins } from "./lists";

const LISTS = [
  { id: 1, name: "Watchlist 1", isins: ["INE001A01001", "INE002A02002"] },
  { id: 2, name: "PSU Bonds", isins: ["INE002A02002", "INE003A03003"] },
];

describe("unionIsins", () => {
  it("dedupes across all lists", () => {
    expect(unionIsins(LISTS, null)).toEqual(["INE001A01001", "INE002A02002", "INE003A03003"]);
  });

  it("narrows to a single list", () => {
    expect(unionIsins(LISTS, 2)).toEqual(["INE002A02002", "INE003A03003"]);
  });

  it("unknown id yields nothing", () => {
    expect(unionIsins(LISTS, 99)).toEqual([]);
  });

  it("empty lists yield nothing", () => {
    expect(unionIsins([], null)).toEqual([]);
  });
});

describe("nextListName", () => {
  it("starts at Watchlist 1", () => {
    expect(nextListName([])).toBe("Watchlist 1");
  });

  it("counts up from the number of lists", () => {
    expect(nextListName(LISTS)).toBe("Watchlist 3");
  });

  it("skips taken names", () => {
    expect(nextListName([{ id: 1, name: "Watchlist 2", isins: [] }])).toBe("Watchlist 3");
  });
});
