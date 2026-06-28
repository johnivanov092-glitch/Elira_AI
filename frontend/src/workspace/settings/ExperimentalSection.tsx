import { Braces, Globe, Play, Square, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../../api/client";
import { cn } from "../../ui/cn";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

type FeatureFlags = { remote_mcp: boolean; action_envelopes: boolean };

const FLAG_META: { key: keyof FeatureFlags; label: string; hint: string; icon: LucideIcon }[] = [
  {
    key: "remote_mcp",
    label: "Удалённые MCP-серверы (HTTP)",
    hint: "Разрешает MCP-серверам с transport=http запускаться. По умолчанию доступен только локальный stdio-транспорт.",
    icon: Globe,
  },
  {
    key: "action_envelopes",
    label: "Структурированные action-конверты",
    hint: "Строгая JSON-валидация вызовов инструментов в цикле агента (одна попытка починки → откат). Обычный чат не затрагивается.",
    icon: Braces,
  },
];

export function ExperimentalSection() {
  const [flags, setFlags] = useState<FeatureFlags | null>(null);
  const [busy, setBusy] = useState<keyof FeatureFlags | "">("");

  useEffect(() => {
    let alive = true;
    request<FeatureFlags>("/api/elira/feature-flags")
      .then((f) => { if (alive) setFlags(f); })
      .catch(() => { if (alive) setFlags({ remote_mcp: false, action_envelopes: false }); });
    return () => { alive = false; };
  }, []);

  async function toggle(key: keyof FeatureFlags, value: boolean) {
    if (!flags) return;
    setBusy(key);
    try {
      const next = await request<FeatureFlags>("/api/elira/feature-flags", {
        method: "PUT",
        body: { name: key, value },
      });
      setFlags(next);
    } catch {
      /* offline — leave state unchanged */
    } finally {
      setBusy("");
    }
  }

  return (
    <Wrap title="Экспериментальное">
      <Note>
        Отложенные возможности агента. По умолчанию выключены; включаются без перезапуска.
        Переменная окружения ELIRA_* , если задана, перебивает тумблер.
      </Note>
      {flags === null ? (
        <Loading />
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {FLAG_META.map((f) => {
            const on = flags[f.key];
            const Icon = f.icon;
            return (
              <div key={f.key} className="flex items-start gap-2.5 rounded-lg border border-line px-3 py-2.5 text-[12.5px]">
                <span className="relative mt-0.5 shrink-0">
                  <Icon size={15} className={cn(on ? "text-ac" : "text-mut")} />
                  <span className={cn("absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full ring-2 ring-surface", on ? "bg-ac" : "bg-mut")} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-tx">{f.label}</span>
                  <span className="mt-0.5 block text-[11.5px] text-mut">{f.hint}</span>
                </span>
                <McpBtn onClick={() => toggle(f.key, !on)} busy={busy === f.key} label={on ? "Выключить" : "Включить"}>
                  {on ? <Square size={13} /> : <Play size={13} />}
                </McpBtn>
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-2.5">
        <Note>LSP-контекст (D2) включается отдельно во вкладке «Интеграции» — у него свой список серверов и кнопка запуска.</Note>
      </div>
    </Wrap>
  );
}
