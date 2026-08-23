import { isTauri } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import type { ComponentPropsWithoutRef, MouseEvent, ReactElement } from "react";
import { buildApiUrl } from "../api/client";

type DownloadLinkProps = Omit<ComponentPropsWithoutRef<"a">, "download" | "href"> & {
  name: string;
  url: string;
};

async function openDownload(
  event: MouseEvent<HTMLAnchorElement>,
  url: string,
): Promise<void> {
  if (!isTauri()) return;
  event.preventDefault();
  try {
    await openUrl(url);
  } catch (error) {
    console.error("Failed to open download URL", error);
  }
}

export function DownloadLink({
  children,
  name,
  onClick,
  url,
  ...props
}: DownloadLinkProps): ReactElement {
  const href = buildApiUrl(url);
  return (
    <a
      {...props}
      href={href}
      download={name}
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) void openDownload(event, href);
      }}
    >
      {children}
    </a>
  );
}
