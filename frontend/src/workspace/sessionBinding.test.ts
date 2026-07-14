import { describe, expect, it, vi } from "vitest";
import { bindingFromSession, persistProjectSelection } from "./sessionBinding";

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
});
