import type { ReactNode } from "react";
import { cn } from "./cn";

type ChipProps = {
  active?: boolean;
  icon?: ReactNode;
  children: ReactNode;
  onClick?: () => void;
  title?: string;
};

/** Pill / mode chip (composer modes, filters). */
export function Chip({ active, icon, children, onClick, title }: ChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cn(
        "flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11px] font-medium transition-colors",
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
