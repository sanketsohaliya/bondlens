import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import InlineMetadata from "./InlineMetadata";

const text = (html: string) => html.replace(/<[^>]*>/g, "");

describe("inline metadata", () => {
  it("separates present fields without splitting their contents or dropping zero", () => {
    const html = renderToStaticMarkup(
      <InlineMetadata
        parts={[null, "INDIA RATINGS (S)", false, 0, undefined, "8.92% annual", ""]}
      />,
    );
    expect(text(html)).toBe("INDIA RATINGS (S) | 0 | 8.92% annual");
    expect(html.match(/aria-hidden="true"/g)).toHaveLength(2);
    expect(html).not.toContain("tabindex");
  });

  it("renders no separator for empty or single-field groups", () => {
    expect(renderToStaticMarkup(<InlineMetadata parts={[null, false, undefined, ""]} />)).toBe("");
    expect(text(renderToStaticMarkup(<InlineMetadata parts={[null, "Nov 2033", false]} />))).toBe(
      "Nov 2033",
    );
  });

  it("keeps separators outside interactive fields", () => {
    const html = renderToStaticMarkup(
      <InlineMetadata
        parts={[
          <button key="copy" type="button" aria-label="Copy ISIN">
            INE000A01001
          </button>,
          <a key="link" href="https://example.test/">
            NSDL ↗
          </a>,
        ]}
      />,
    );
    expect(text(html)).toBe("INE000A01001 | NSDL ↗");
    expect(html).toMatch(/<button[^>]*>INE000A01001<\/button><span[^>]*> \| <\/span><a/);
  });
});
