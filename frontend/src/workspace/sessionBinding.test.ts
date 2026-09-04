import { describe, expect, it, vi } from "vitest";
import { bindingFromSession, persistProjectSelection, startWithServerSession } from "./sessionBinding";

describe("per-session project binding", () => {
  it("does not inherit the previously displayed project or model", () => {
    expect(bindingFromSession({ project_root: null, model: null })).toEqual({
      projectRoot: "",
      model: "auto",
    });
  });

  it("restores the project and model owned by the selected session", () => {
    expect(bindingFromSession({ project_root: "C:/CRM BOT", model: "local-model" })).toEqual({
      projectRoot: "C:/CRM BOT",
      model: "local-model",
    });
  });

  it("persists a picked folder immediately for an existing session", async () => {
    const patchSession = vi.fn().mockResolvedValue({});
    await expect(persistProjectSelection("s-1", "C:/CRM BOT", patchSession)).resolves.toBe(true);
    expect(patchSession).toHaveBeenCalledWith("s-1", { projectRoot: "C:/CRM BOT" });
  });

  it("keeps an unsaved draft local until its first send creates the session", async () => {
    const patchSession = vi.fn();
    await expect(persistProjectSelection(null, "C:/CRM BOT", patchSession)).resolves.toBe(false);
    expect(patchSession).not.toHaveBeenCalled();
  });

  it("creates a server session before starting the first run", async () => {
    const order: string[] = [];
    const createSession = vi.fn(async () => {
      order.push("created");
      return "s-1";
    });
    const startRun = vi.fn((sessionId: string) => order.push(`started:${sessionId}`));

    await expect(startWithServerSession(null, createSession, startRun)).resolves.toBe("s-1");
    expect(order).toEqual(["created", "started:s-1"]);
  });

  it("starts an existing server session without creating another one", async () => {
    const createSession = vi.fn();
    const startRun = vi.fn();

    await expect(startWithServerSession("s-existing", createSession, startRun)).resolves.toBe("s-existing");
    expect(createSession).not.toHaveBeenCalled();
    expect(startRun).toHaveBeenCalledWith("s-existing");
  });

  it("does not start a run when server session creation fails", async () => {
    const createSession = vi.fn().mockRejectedValue(new Error("offline"));
    const startRun = vi.fn();

    await expect(startWithServerSession(null, createSession, startRun)).rejects.toThrow("offline");
    expect(startRun).not.toHaveBeenCalled();
  });
});
