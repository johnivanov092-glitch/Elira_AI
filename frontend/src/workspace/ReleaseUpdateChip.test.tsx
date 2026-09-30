import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RELEASE_PHASES, type ReleaseObservation, type ReleasePhase } from "../api/releases";
import { claimReleaseNotice, ReleaseConfirmationPrompt, ReleaseRollbackControl, ReleaseUpdateChip, ReleaseUpdateDetails, releasePresentation } from "./ReleaseUpdateChip";

function observation(phase: ReleasePhase, connection: ReleaseObservation["connection"] = "connected"): ReleaseObservation {
  return {
    connection, receivedAt: 1_790_770_010_000,
    status: {
      version: 1, mode: "foundation", phase, active_release_id: "release-a", target_release_id: "release-b",
      operation_id: "operation-b", updated_at: 1_790_770_000,
      previous_release_id: null, rollback_available: false,
      step: { index: 2, total: 4, label: "Проверка backend" }, error: null, confirmation: null,
    },
  };
}

describe("update presentation", () => {
  it.each(RELEASE_PHASES)("maps %s without treating a build or pending request as installation", (phase) => {
    const view = releasePresentation(observation(phase));
    expect(view.label.length).toBeGreaterThan(0);
    expect(view.success).toBe(phase === "completed");
    expect(view.busy).toBe(["preparing", "checking", "switching", "rolling_back"].includes(phase));
    expect(view.failure).toBe(phase === "failed");
    expect(view.label).not.toContain("%");
  });

  it("does not animate or claim current success after losing the connection", () => {
    for (const phase of ["checking", "switching", "completed"] as const) {
      expect(releasePresentation(observation(phase, "reconnecting"))).toMatchObject({
        label: "Восстанавливаем связь", busy: false, success: false,
      });
    }
  });

  it("names development mode explicitly", () => {
    const value = observation("idle");
    if (value.status) value.status.mode = "development";
    expect(releasePresentation(value).label).toBe("Режим разработки");
  });

  it("renders real steps, preserved versions and escaped errors without overall percentages", () => {
    const value = observation("failed", "reconnecting");
    if (value.status) {
      value.status.previous_release_id = "release-b";
      value.status.error = "Проверка <script>alert('x')</script> не прошла";
    }
    const html = renderToStaticMarkup(<ReleaseUpdateDetails observation={value} />);
    expect(html).toContain("release-a");
    expect(html).toContain("release-b");
    expect(html).toContain("Шаг 2 из 4");
    expect(html).toContain("Проверка backend");
    expect(html).toContain("Последний известный этап");
    expect(html).toContain("Текущее состояние установки не подтверждено");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("%");
  });

  it("renders an accessible compact disclosure while status is initially unknown", () => {
    const html = renderToStaticMarkup(<ReleaseUpdateChip />);
    expect(html).toContain('aria-haspopup="dialog"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("Обновление Elira: Получаем состояние обновления");
    expect(html).not.toContain("Обновление установлено");
    expect(html).not.toContain("animate-spin");
  });
});

describe("installation proposal presentation", () => {
  const proposal = { request_id: "request-b", release_id: "release-b", sha256: "a".repeat(64), requested_at: 1_790_770_000 };

  it("offers explicit install/later actions without sending anything during render", () => {
    const onConfirm = vi.fn();
    const onLater = vi.fn();
    const html = renderToStaticMarkup(<ReleaseConfirmationPrompt confirmation={proposal} connected
      approval={null} onConfirm={onConfirm} onLater={onLater} />);
    expect(html).toContain("Обновиться на новую версию");
    expect(html).toContain("Установить сейчас");
    expect(html).toContain("Позже");
    expect(html).toContain("дождётся завершения активных задач");
    expect(html).toContain("ненадолго перезапустится");
    expect(html).toContain("Вручную закрывать приложение не нужно");
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onLater).not.toHaveBeenCalled();
    expect(releasePresentation(observation("awaiting_confirmation"))).toMatchObject({ busy: false, success: false });
    expect(releasePresentation(observation("waiting")).label).toContain("одобрена");
  });

  it("disables both actions during submission, and install while disconnected", () => {
    const pending = renderToStaticMarkup(<ReleaseConfirmationPrompt confirmation={proposal} connected
      approval={{ requestId: proposal.request_id, state: "sending" }} onConfirm={vi.fn()} onLater={vi.fn()} />);
    expect(pending.match(/disabled=""/g)).toHaveLength(2);
    const offline = renderToStaticMarkup(<ReleaseConfirmationPrompt confirmation={proposal} connected={false}
      approval={{ requestId: proposal.request_id, state: "uncertain" }} onConfirm={vi.fn()} onLater={vi.fn()} />);
    expect(offline.match(/disabled=""/g)).toHaveLength(1);
    expect(offline).toContain("подтверждение могло быть принято");
    expect(offline).not.toContain("Обновление установлено");
  });

  it("does not attach an earlier proposal's response to a newer proposal", () => {
    const html = renderToStaticMarkup(<ReleaseConfirmationPrompt confirmation={{ ...proposal, request_id: "new-request" }} connected
      approval={{ requestId: proposal.request_id, state: "accepted" }} onConfirm={vi.fn()} onLater={vi.fn()} />);
    expect(html).not.toContain("Запрос принят");
    expect(html).not.toContain("disabled=");
  });

  it("offers close rather than postpone once approval was accepted or its result is uncertain", () => {
    for (const state of ["accepted", "uncertain"] as const) {
      const html = renderToStaticMarkup(<ReleaseConfirmationPrompt confirmation={proposal} connected
        approval={{ requestId: proposal.request_id, state }} onConfirm={vi.fn()} onLater={vi.fn()} />);
      expect(html).toContain("Закрыть");
      expect(html).not.toContain("Позже");
      if (state === "accepted") expect(html.match(/<button/g)).toHaveLength(1);
    }
  });

  it("remembers a displayed or deferred proposal across reload, scoped to the API", async () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
    const endpoint = "http://fixture-notice:18582";
    expect(claimReleaseNotice(proposal.request_id, storage, endpoint)).toBe(true);
    expect(claimReleaseNotice(proposal.request_id, storage, endpoint)).toBe(false);
    expect(values.size).toBe(1);
    vi.resetModules();
    const { claimReleaseNotice: afterReload } = await import("./ReleaseUpdateChip");
    expect(afterReload(proposal.request_id, storage, endpoint)).toBe(false);
    expect(afterReload("new-proposal", storage, endpoint)).toBe(true);
    expect(afterReload(proposal.request_id, storage, "http://other-api:18582")).toBe(true);
  });

  it("does not repeatedly reopen when persistent storage is unavailable", () => {
    const storage = { getItem: () => { throw new Error("disabled storage"); }, setItem: vi.fn() };
    expect(claimReleaseNotice("storage-unavailable", storage)).toBe(true);
    expect(claimReleaseNotice("storage-unavailable", storage)).toBe(false);
  });
});

describe("saved version direction", () => {
  it.each(["rollback", "update"] as const)("names the %s action without inferring it from identifiers", (direction) => {
    const value = observation("completed");
    value.status!.previous_release_id = "release-z";
    value.status!.rollback_available = true;
    value.status!.saved_release_action = direction;
    const confirm = vi.fn();
    const html = renderToStaticMarkup(<ReleaseRollbackControl observation={value} approval={null} onConfirm={confirm} />);
    expect(html).toContain(direction === "update" ? "Обновиться на новую версию" : "Откатиться на прежнюю версию");
    expect(html).not.toContain("Переключить на сохранённую версию");
    expect(confirm).not.toHaveBeenCalled();
  });

  it("does not duplicate the forward action when the saved release is already proposed", () => {
    const value = observation("awaiting_confirmation");
    value.status!.previous_release_id = "release-b";
    value.status!.confirmation = { request_id: "proposal", release_id: "release-b", sha256: "a".repeat(64), requested_at: 1 };
    expect(renderToStaticMarkup(<ReleaseRollbackControl observation={value} approval={null} onConfirm={vi.fn()} />)).toBe("");
  });
});
