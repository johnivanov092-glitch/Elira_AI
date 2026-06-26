import { request, safeRequest } from "./client";

// ── Persona evolution (read-only observation panel) ─────────────────────────
//
// Elira's *identity* (the immutable `ELIRA_PERSONA_BASE_PAYLOAD` core) never
// changes, but her *manner* is a versioned, learning layer: each dialogue is
// observed, candidate traits accumulate in quarantine, and qualifying ones are
// promoted into a new active persona version. These helpers surface that state
// for the Settings → "Личность" panel. All four endpoints already exist on the
// backend (`/api/persona/*`); nothing here mutates the persona except the
// explicit version rollback.

/** A model-calibration row for the active version (one per observed model). */
export type PersonaModelCalibration = {
  model: string;
  version_id: number;
  consistency_score: number;
  updated_at: string;
  calibration: {
    verbosity?: string;
    formatting?: string;
    list_bias?: string;
    [key: string]: unknown;
  };
};

/** A trait that was promoted into the active persona (history, newest first). */
export type PersonaPromotedTrait = {
  trait_key: string;
  summary: string;
  promoted_version: number | null;
  last_seen: string;
};

/** Aggregate snapshot of the persona-evolution state. */
export type PersonaStatus = {
  ok: boolean;
  persona_name: string;
  active_version: number;
  status: string;
  last_evolution_at: string | null;
  quarantine_candidates: number;
  /** Version to roll back to, or null when the active one is the first. */
  previous_version: number | null;
  latest_traits: PersonaPromotedTrait[];
  model_consistency: PersonaModelCalibration[];
};

/** A candidate trait still in quarantine (not yet promoted). */
export type PersonaCandidate = {
  id: number;
  trait_key: string;
  layer: string;
  evidence_count: number;
  confidence_avg: number;
  contradiction_score: number;
  first_seen: string;
  last_seen: string;
  status: string;
  /** Decoded `candidate_json`; `summary` is the human-readable description. */
  candidate: { summary?: string; [key: string]: unknown };
};

const EMPTY_STATUS: PersonaStatus = {
  ok: false,
  persona_name: "Elira",
  active_version: 1,
  status: "active",
  last_evolution_at: null,
  quarantine_candidates: 0,
  previous_version: null,
  latest_traits: [],
  model_consistency: [],
};

/** Read the persona-evolution snapshot. Falls back to an empty state on error. */
export async function getPersonaStatus(): Promise<PersonaStatus> {
  return safeRequest<PersonaStatus>("/api/persona/status", {}, EMPTY_STATUS);
}

/** List quarantined candidate traits (newest/strongest first). [] on error. */
export async function listPersonaCandidates(
  limit = 20,
): Promise<PersonaCandidate[]> {
  const payload = await safeRequest<{ items?: PersonaCandidate[] }>(
    `/api/persona/candidates?limit=${limit}`,
    {},
    { items: [] },
  );
  return Array.isArray(payload.items) ? payload.items : [];
}

/** Roll the active persona back to an earlier version. */
export async function rollbackPersona(
  version: number,
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/persona/rollback/${version}`, {
    method: "POST",
  });
}
