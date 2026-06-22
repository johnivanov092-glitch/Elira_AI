import { request } from "./client";

/** One agent profile (persona + system prompt + routing). */
export type ProfileInfo = {
  name: string;
  is_default?: boolean;
  icon?: string;
  tags?: string[];
  short?: string;
};

/** List the available agent profiles. Returns [] on any error. */
export async function listProfiles(): Promise<ProfileInfo[]> {
  try {
    const r = await request<{ profiles?: ProfileInfo[] }>("/api/profiles");
    return Array.isArray(r.profiles) ? r.profiles : [];
  } catch {
    return [];
  }
}

/** The active agent profile is stored under `agent_profile` in the global
 *  Elira settings. Returns the raw settings object plus the active name. */
export async function getActiveProfile(): Promise<{
  settings: Record<string, unknown>;
  active: string;
}> {
  const settings = await request<Record<string, unknown>>("/api/elira/settings");
  return { settings, active: String(settings.agent_profile ?? "") };
}

/** Switch the global active profile. Preserves the full settings object,
 *  overriding only `agent_profile` — mirrors Settings' ProfilesSection.pick(). */
export async function setActiveProfile(
  name: string,
  settings: Record<string, unknown>,
): Promise<void> {
  await request("/api/elira/settings", {
    method: "PUT",
    body: { ...settings, agent_profile: name },
  });
}
