import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import CopyText from "./CopyText";

describe("CopyText", () => {
  it("reads as clickable: copy cursor, emerald hover, and a copy tooltip", () => {
    const html = renderToStaticMarkup(
      <CopyText value="INE000A01001" label="ISIN" className="font-mono text-zinc-500" />,
    );
    expect(html).toContain('type="button"');
    expect(html).toContain("cursor-copy");
    expect(html).toContain("hover:text-emerald-400");
    expect(html).toContain('title="Click to copy ISIN"');
    expect(html).toContain("INE000A01001");
  });
});
