/// <reference types="vite/client" />
import { describe, it, expect } from "vitest";
import composerSource from "./Composer.tsx?raw";
import shellSource from "./WorkspaceShell.tsx?raw";

// R1: the composer UPLOADS a file on pick (registration only) and never shows a
// processing/transcription indicator — content is read later by resource_process.
describe("Composer resource attach flow", () => {
  it("uploads via uploadResource, not the eager attachToChat parser", () => {
    expect(composerSource).toContain("uploadResource");
    expect(composerSource).not.toContain("attachToChat");
  });

  it("does not claim processing/transcription on a plain attach", () => {
    // No "идёт транскрипция / расшифровка / обработка" wording on the upload chip.
    expect(composerSource).not.toMatch(/транскрип|расшифров/i);
    expect(composerSource).not.toContain("и обрабатывается");
  });

  it("keys chips by resource_id so duplicate filenames never merge", () => {
    expect(composerSource).toMatch(/key=\{a\.resource_id/);
  });

  it("tracks an honest upload/error status per attachment", () => {
    expect(composerSource).toContain('status: "ready"');
    expect(composerSource).toContain('status: "error"');
    expect(composerSource).toContain('a.status === "error"');
  });

  it("does not keep the raw File on the attachment after upload", () => {
    // The raw File is only a local `file` variable in onPickFiles; it must not be
    // stored on the attachment object (no `file,` field carried into state).
    expect(composerSource).not.toMatch(/setAttachments\([^)]*file:\s*file/);
  });

  it("routes drag-and-drop through the same deferred resource intake", () => {
    expect(shellSource).toContain("attachControls.current?.attachFiles(files)");
    expect(shellSource).not.toContain("run.addFiles(files)");
  });

  it("remounts staged attachments when the active chat changes", () => {
    expect(shellSource).toContain("<Composer key={activeKey}");
  });

  it("does not route by extension in the file picker", () => {
    expect(composerSource).toContain('type="file"');
    expect(composerSource).not.toContain('accept="');
  });
});
