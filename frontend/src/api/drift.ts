// drift.ts — server drift detector alerts (active model / context window).

import { request, safeRequest } from "./client";

export type DriftFact = {
  key: string;
  value: string | null;
  verified_at?: string | null;
  previous_value?: string | null;
  changed_at?: string | null;
};

export type DriftAlerts = { count: number; drifts: DriftFact[] };

/** Active (unacknowledged) drifts. [] / 0 on any error — never throws. */
export async function getDriftAlerts(): Promise<DriftAlerts> {
  return safeRequest<DriftAlerts>("/api/drift/alerts", {}, { count: 0, drifts: [] });
}

/** Mark all active drifts as seen (clears the red badge). */
export async function ackDrift(): Promise<void> {
  await request("/api/drift/ack", { method: "POST" });
}
