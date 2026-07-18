// Brand mark — same artwork as /favicon.svg, inlined so it scales crisply.
export default function Logo({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} className={className} aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#18181b" />
      <path
        d="M10 46 C 22 44, 26 32, 34 26 S 48 16, 54 14"
        fill="none"
        stroke="#34d399"
        strokeWidth="4.5"
        strokeLinecap="round"
      />
      <circle cx="30" cy="30" r="13" fill="none" stroke="#e4e4e7" strokeWidth="4" />
      <line
        x1="40"
        y1="40"
        x2="50"
        y2="50"
        stroke="#e4e4e7"
        strokeWidth="5"
        strokeLinecap="round"
      />
    </svg>
  );
}
