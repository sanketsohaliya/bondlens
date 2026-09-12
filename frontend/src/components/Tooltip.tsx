import { type ReactNode, useState } from "react";

/** v1-style hover card: bordered, fixed-position, no dependencies. */
export default function Tip({
  text,
  children,
  className,
}: {
  text: string;
  children: ReactNode;
  className?: string;
}) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);

  return (
    <span
      className={className}
      onMouseEnter={(e) => {
        // With display:contents the wrapper has a zero rect — measure the child.
        const el = (e.currentTarget.firstElementChild ?? e.currentTarget) as HTMLElement;
        const r = el.getBoundingClientRect();
        setPos({ x: Math.max(8, Math.min(r.left, window.innerWidth - 332)), y: r.bottom + 6 });
      }}
      onMouseLeave={() => setPos(null)}
    >
      {children}
      {pos && (
        <span className="tip-card" style={{ left: pos.x, top: pos.y }}>
          {text}
        </span>
      )}
    </span>
  );
}
