export type SessionBindingSource = {
  project_root?: string | null;
  model?: string | null;
};

export type SessionBinding = {
  projectRoot: string;
  model: string;
};

type PatchSession = (sessionId: string, patch: { projectRoot: string }) => Promise<unknown>;

type CreateSession = () => Promise<string>;
type StartRun = (sessionId: string) => void;

/** A saved session owns its project/model. Missing values mean scratch/auto;
 * they must never fall back to whichever chat happened to be visible before it. */
export function bindingFromSession(session: SessionBindingSource | null | undefined): SessionBinding {
  return {
    projectRoot: session?.project_root ?? "",
    model: session?.model ?? "auto",
  };
}

/** Persist a folder selection immediately for an existing chat. Draft chats have
 * no server id yet; their binding is persisted when the first send creates one. */
export async function persistProjectSelection(
  sessionId: string | null,
  projectRoot: string,
  patchSession: PatchSession,
): Promise<boolean> {
  if (!sessionId) return false;
  await patchSession(sessionId, { projectRoot });
  return true;
}

/** Resolve the server-owned session before a run starts. Persona and memory
 * provenance must never receive the temporary draft key. */
export async function startWithServerSession(
  sessionId: string | null,
  createSession: CreateSession,
  startRun: StartRun,
): Promise<string> {
  const resolved = (sessionId ?? await createSession()).trim();
  if (!resolved) throw new Error("Server session id is empty");
  startRun(resolved);
  return resolved;
}
