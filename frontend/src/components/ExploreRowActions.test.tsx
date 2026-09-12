import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import AiPromptBtn from "./AiPromptBtn";
import { RowLink } from "./ExploreTable";

const client = new QueryClient();
const wrap = (node: React.ReactElement) =>
  renderToStaticMarkup(<QueryClientProvider client={client}>{node}</QueryClientProvider>);

describe("explore row actions", () => {
  it("renders an external link that opens safely in a new tab and explains itself", () => {
    const html = renderToStaticMarkup(
      <RowLink href="https://example.test/x" label="NSDL" tip="Official bond record on NSDL" />,
    );
    expect(html).toContain('href="https://example.test/x"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain('title="Official bond record on NSDL"');
    expect(html).toContain('aria-label="Official bond record on NSDL"');
    expect(html).toContain("NSDL ↗");
  });

  it("labels the AI action as a verb with an explanatory tooltip in both variants", () => {
    for (const variant of ["row", "drawer"] as const) {
      const html = wrap(<AiPromptBtn isin="INE000A01001" variant={variant} />);
      expect(html).toContain("Ask AI ↗");
      expect(html).toContain("Opens ChatGPT with this bond");
      expect(html).toContain('type="button"');
      expect(html).not.toContain("Download");
      expect(html).not.toContain("Open ChatGPT");
    }
  });

  it("uses the compact text style for the row and the pill for the drawer", () => {
    expect(wrap(<AiPromptBtn isin="X" variant="row" />)).toContain("text-[10px]");
    expect(wrap(<AiPromptBtn isin="X" variant="drawer" />)).toContain("rounded-md border");
  });
});
