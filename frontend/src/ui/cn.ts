/** Minimal className joiner (drops falsy values). Lighter than clsx for our
 *  hand-built primitives. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
