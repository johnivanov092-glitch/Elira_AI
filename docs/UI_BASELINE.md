# Protected UI baseline

Status: **locked by user request on 2026-06-20**

The current unified workspace UI is approved and must not be visually
redesigned during agent-core/context work.

## Baseline evidence

- Screenshot: `C:\Users\Root\Desktop\Новый UI.png`
- Dimensions: `1442 × 992`
- SHA-256: `09EE28D6114E4A4D5CCC9DA95B07C035443FB354D68412554BE6CCE015BDA268`
- Source-code baseline: commit `f4617fa`
- Primary implementation:
  - `frontend/src/workspace/WorkspaceShell.tsx`
  - `frontend/src/workspace/Topbar.tsx`
  - `frontend/src/workspace/Sidebar.tsx`
  - `frontend/src/workspace/Composer.tsx`
  - `frontend/src/workspace/Transcript.tsx`
  - `frontend/src/styles.css`
  - `frontend/src/theme.css`

The screenshot is a local visual reference. The commit is the durable code
reference if the screenshot is moved.

## Locked visual characteristics

- Narrow left chat sidebar with the `Новый чат` action and connection status.
- Compact top row: project selector, `Чат` / `Пайплайны`, then utility icons.
- Large quiet central workspace and centered welcome block.
- Four compact starter cards in a two-column grid.
- Composer pinned at the bottom with mode chips immediately above it.
- Current dark palette, blue accent, border contrast, radii, typography scale,
  spacing density and icon style.
- Existing responsive panel behavior and preview/settings overlays.

User-approved activity slice, 2026-09-26: one neutral inline “Действия” disclosure
shows the current action label and subtle animation during real execution.
Tool-call counts, individual failures and diagnostic results appear only inside
the expanded history. No separate tool-error card or red collapsed header is
shown. Full arguments/results remain available on expansion. Reduced-motion
preferences disable animation. The surrounding workspace geometry and theme
stay intact.

## Allowed without separate visual approval

- Backend-only changes.
- Data wiring that does not change layout, spacing, colors or control order.
- Correct loading/error/disabled states inside an existing control footprint.
- Accessibility attributes and non-visual fixes.

## Requires explicit user approval first

- Adding persistent chrome, counters, badges, panels or toolbars.
- Moving or resizing the sidebar, top row, welcome block or composer.
- Changing theme tokens, typography, spacing, radii or icon family.
- Placing the planned context indicator or memory/task controls.
- Any frontend rewrite or broad component refactor during stabilization.

## Visual verification for approved UI slices

1. Run frontend typecheck and build.
2. Open the Tauri/web UI at the baseline viewport.
3. Compare against the screenshot for geometry and hierarchy.
4. Capture an after screenshot and record its path in the active
   coordination note (archived plans live under `docs/archive/`).
5. Stop and ask if required functionality has no approved location.
