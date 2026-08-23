import { beforeEach, describe, expect, it, vi } from "vitest";

const { openUrl, isTauri } = vi.hoisted(() => ({
  openUrl: vi.fn<(_url: string) => Promise<void>>(),
  isTauri: vi.fn<() => boolean>(),
}));

vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl }));
vi.mock("@tauri-apps/api/core", () => ({ isTauri }));

import { ExternalBrowserLink } from "./ExternalLink";

describe("ExternalBrowserLink", () => {
  beforeEach(() => {
    openUrl.mockReset();
    openUrl.mockResolvedValue(undefined);
    isTauri.mockReset();
  });

  it("opens an HTTP link in the system browser inside Tauri", async () => {
    isTauri.mockReturnValue(true);
    const preventDefault = vi.fn();
    const link = ExternalBrowserLink({ href: "https://example.com/docs", children: "Docs" });

    await link.props.onClick({ preventDefault });

    expect(preventDefault).toHaveBeenCalledOnce();
    expect(openUrl).toHaveBeenCalledWith("https://example.com/docs");
  });

  it("keeps normal browser navigation outside Tauri", async () => {
    isTauri.mockReturnValue(false);
    const preventDefault = vi.fn();
    const link = ExternalBrowserLink({ href: "https://example.com/docs", children: "Docs" });

    await link.props.onClick({ preventDefault });

    expect(preventDefault).not.toHaveBeenCalled();
    expect(openUrl).not.toHaveBeenCalled();
  });
});
