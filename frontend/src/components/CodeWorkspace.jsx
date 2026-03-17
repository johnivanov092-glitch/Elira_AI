
import Phase19Panel from "./Phase19Panel";

export function autoStageFromPlan(plan, setStagedPaths) {
  const files = plan
    .filter((p) => p.action === "modify" || p.action === "create")
    .map((p) => p.path);

  setStagedPaths((prev) => {
    const merged = new Set([...prev, ...files]);
    return [...merged];
  });
}
