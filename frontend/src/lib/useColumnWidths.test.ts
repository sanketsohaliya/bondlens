import { describe, expect, it } from "vitest";
import { pickBase, storageKeyFor } from "./useColumnWidths";

describe("column widths", () => {
  it("keeps each column layout's widths under its own key", () => {
    // Explore has 8 columns before Kite status arrives and 9 once
    // connected; one shared array went stale and froze resizing.
    expect(storageKeyFor("explore-col-widths", 8)).not.toBe(storageKeyFor("explore-col-widths", 9));
  });

  it("starts a drag from the live header when the saved array does not fit it", () => {
    const measured = [40, 300, 120, 120, 160, 140, 160, 90, 90];
    const stale8 = [40, 300, 120, 160, 140, 160, 90, 90];
    expect(pickBase(stale8, measured)).toBe(measured);
    expect(pickBase(null, measured)).toBe(measured);
    const fits = [50, 310, 120, 120, 160, 140, 160, 90, 90];
    expect(pickBase(fits, measured)).toBe(fits);
  });
});
