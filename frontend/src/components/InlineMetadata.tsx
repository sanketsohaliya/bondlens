import { Fragment, isValidElement, type ReactNode } from "react";

export function MetadataSeparator() {
  return (
    <span aria-hidden="true" className="shrink-0 whitespace-pre text-zinc-600">
      {" | "}
    </span>
  );
}

export default function InlineMetadata({ parts }: { parts: readonly ReactNode[] }) {
  return parts
    .filter((part) => part != null && typeof part !== "boolean" && part !== "")
    .map((part, i) => (
      <Fragment key={isValidElement(part) ? (part.key ?? i) : i}>
        {i > 0 && <MetadataSeparator />}
        {part}
      </Fragment>
    ));
}
