/** Elira brand mark — the project logo (AI.svg): a rounded-square tile with a
 *  violet→cyan gradient ring and a filled core dot. Vector (scales crisply).
 *
 *  The brand gradient (ring + core) is fixed — it is the identity. The tile
 *  background uses the `--color-card` token so the mark sits cleanly on every
 *  theme instead of being a hard dark square on the light/bw/sepia themes.
 *  `id` makes the gradient defs unique per instance (safe to render more than
 *  once on a page). */
export function EliraMark({ className, id = "elira-mark" }: { className?: string; id?: string }) {
  const grad = `${id}-grad`;
  return (
    <svg
      viewBox="0 0 64 64"
      className={className}
      role="img"
      aria-label="Elira"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient id={grad} x1="12" y1="10" x2="52" y2="54" gradientUnits="userSpaceOnUse">
          <stop stopColor="#7C3AED" />
          <stop offset="1" stopColor="#06B6D4" />
        </linearGradient>
      </defs>
      <rect x="5" y="5" width="54" height="54" rx="14" fill="var(--color-card)" />
      <circle cx="32" cy="32" r="14" stroke={`url(#${grad})`} strokeWidth="3" />
      <circle cx="32" cy="32" r="6" fill={`url(#${grad})`} />
    </svg>
  );
}
