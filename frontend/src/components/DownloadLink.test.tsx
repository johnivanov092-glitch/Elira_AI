import { beforeEach, describe, expect, it, vi } from "vitest";

const { openUrl, isTauri } = vi.hoisted(() => ({
  openUrl: vi.fn<(_url: string) => Promise<void>>(),
  isTauri: vi.fn<() => boolean>(),
}));

vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl }));
vi.mock("@tauri-apps/api/core", () => ({ isTauri }));

import { DownloadLink } from "./DownloadLink";

describe("DownloadLink", () => {
  beforeEach(() => {
    openUrl.mockReset();
    openUrl.mockResolvedValue(undefined);
    isTauri.mockReset();
  });

  it("is a real download anchor in a normal browser", async () => {
    isTauri.mockReturnValue(false);
    const preventDefault = vi.fn();
    const link = DownloadLink({
      name: "report.pdf",
      url: "/api/skills/download/report.pdf",
      children: "Скачать",
    });

    expect(link.type).toBe("a");
    expect(link.props.href).toBe("http://127.0.0.1:8000/api/skills/download/report.pdf");
    expect(link.props.download).toBe("report.pdf");
    await link.props.onClick({ preventDefault });
    expect(preventDefault).not.toHaveBeenCalled();
    expect(openUrl).not.toHaveBeenCalled();
  });

  it("opens the backend download in the system browser inside Tauri", async () => {
    isTauri.mockReturnValue(true);
    const preventDefault = vi.fn();
    const link = DownloadLink({
      name: "report.pdf",
      url: "/api/skills/download/report.pdf",
      children: "Скачать",
    });

    await link.props.onClick({ preventDefault });
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(openUrl).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/skills/download/report.pdf",
    );
  });
});
