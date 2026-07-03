import type { ReactNode } from "react";
import { cn } from "./cn";

type ChipProps = {
  active?: boolean;
  icon?: ReactNode;
  /** Omit for an icon-only chip (square pill, no label). */
  children?: ReactNode;
  onClick?: () => void;
  title?: string;
  "aria-label"?: string;
};

/** Pill / mode chip (composer modes, filters). Icon-only when no children. */
export function Chip({ active, icon, children, onClick, title, "aria-label": ariaLabel }: ChipProps) {
  const iconOnly = children == null || children === false;
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      className={cn(
        "flex h-7 shrink-0 items-center rounded-full border text-[11px] font-medium transition-colors",
        iconOnly ? "w-7 justify-center" : "gap-1.5 px-2.5",
        active
          ? "border-acl bg-acs text-ac"
          : "border-line text-t2 hover:bg-hover hover:text-tx",
      )}
    >
      {icon}
      {children}
    </button>
  );
}
