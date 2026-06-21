import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  active?: boolean;
};

/** Square icon button used across the topbar / composer. */
export function IconButton({ active, className, children, ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "grid h-8 w-8 place-items-center rounded-lg border transition-colors",
        active
          ? "border-acl bg-acs text-ac"
          : "border-line bg-surface text-t2 hover:bg-hover hover:text-tx",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
