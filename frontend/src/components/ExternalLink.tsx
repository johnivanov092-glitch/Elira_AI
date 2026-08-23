import { isTauri } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import type { ComponentPropsWithoutRef, MouseEvent, ReactElement } from "react";

type ExternalBrowserLinkProps = Omit<ComponentPropsWithoutRef<"a">, "href"> & {
  href: string;
};

const SYSTEM_URL_RE = /^(?:https?|mailto|tel):/i;

async function openExternalUrl(event: MouseEvent<HTMLAnchorElement>, href: string): Promise<void> {
  if (!isTauri() || !SYSTEM_URL_RE.test(href)) return;
  event.preventDefault();
  try {
    await openUrl(href);
  } catch (error) {
    console.error("Failed to open external URL", error);
  }
}

export function ExternalBrowserLink({
  href,
  children,
  onClick,
  ...props
}: ExternalBrowserLinkProps): ReactElement {
  return (
    <a
      {...props}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) void openExternalUrl(event, href);
      }}
    >
      {children}
    </a>
  );
}
