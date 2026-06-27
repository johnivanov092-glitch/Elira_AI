import { useState } from "react";
import { Check } from "lucide-react";
import { cn } from "../../ui/cn";
import { getTheme, setTheme, THEMES, type Theme } from "../../ui/theme";
import { Wrap } from "./_shared";

export function ThemeSection() {
  const [t, setT] = useState<Theme>(getTheme());
  function pick(v: Theme) { setTheme(v); setT(v); }
  return (
    <Wrap title="Тема">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {THEMES.map((theme) => {
          const active = t === theme.id;
          return (
            <button
              key={theme.id}
              type="button"
              onClick={() => pick(theme.id)}
              className={cn(
                "flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-[13px] transition-colors",
                active ? "border-acl bg-acs text-ac" : "border-line text-t2 hover:bg-hover hover:text-tx",
              )}
            >
              <span
                className="grid h-5 w-5 shrink-0 place-items-center rounded-full ring-1 ring-inset ring-black/15"
                style={{ background: theme.swatch }}
              >
                {active && <Check size={12} className={theme.dark ? "text-white" : "text-black"} />}
              </span>
              <span className="truncate">{theme.label}</span>
            </button>
          );
        })}
      </div>
    </Wrap>
  );
}
