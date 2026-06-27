import { useState } from "react";
import { cn } from "../../ui/cn";
import { getTheme, setTheme, type Theme } from "../../ui/theme";
import { Wrap } from "./_shared";

export function ThemeSection() {
  const [t, setT] = useState<Theme>(getTheme());
  function pick(v: Theme) { setTheme(v); setT(v); }
  return (
    <Wrap title="Тема">
      <div className="flex gap-2">
        {(["dark", "cursor"] as Theme[]).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => pick(v)}
            className={cn(
              "flex-1 rounded-lg border px-3 py-2.5 text-[13px] transition-colors",
              t === v ? "border-acl bg-acs text-ac" : "border-line text-t2 hover:bg-hover hover:text-tx",
            )}
          >
            {v === "dark" ? "Тёмная" : "Cursor"}
          </button>
        ))}
      </div>
    </Wrap>
  );
}
