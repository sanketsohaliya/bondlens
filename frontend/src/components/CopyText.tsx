import { useEffect, useRef, useState } from "react";

type Phase = "idle" | "copied" | "select";

/** Inline text that copies itself when clicked. Used for identifiers such as
 * the symbol and ISIN, which sit inside a clickable row: the click is
 * swallowed so the row never opens by accident. On success the text flashes
 * "✓ copied"; if the clipboard is unavailable the text is selected instead so
 * a manual Ctrl+C still works. */
export default function CopyText({
  value,
  label,
  className = "",
}: {
  value: string;
  label: string;
  className?: string;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (phase === "idle") return;
    const t = setTimeout(() => setPhase("idle"), 1200);
    return () => clearTimeout(t);
  }, [phase]);

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      setPhase("copied");
    } catch {
      const el = ref.current;
      if (el) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
      setPhase("select");
    }
  };

  const text = phase === "copied" ? "✓ copied" : phase === "select" ? "select & copy" : value;
  return (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      title={`Click to copy ${label}`}
      aria-label={`Copy ${label} ${value}`}
      className={`cursor-copy whitespace-nowrap ${phase === "copied" ? "text-emerald-400" : ""} ${className}`}
    >
      {text}
    </button>
  );
}
