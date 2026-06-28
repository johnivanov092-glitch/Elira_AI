import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../../api/client";
import { cn } from "../../ui/cn";
import { Loading, Note, Wrap } from "./_shared";

type ProfileInfo = { name: string; is_default?: boolean; icon?: string; tags?: string[]; short?: string };

export function ProfilesSection() {
  const [profiles, setProfiles] = useState<ProfileInfo[] | null>(null);
  const [active, setActive] = useState("");
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    request<{ profiles?: ProfileInfo[] }>("/api/profiles")
      .then((r) => { if (alive) setProfiles(Array.isArray(r.profiles) ? r.profiles : []); })
      .catch(() => { if (alive) setProfiles([]); });
    request<Record<string, unknown>>("/api/elira/settings")
      .then((s) => { if (alive) { setSettings(s); setActive(String(s.agent_profile ?? "")); } })
      .catch(() => { /* offline */ });
    return () => { alive = false; };
  }, []);

  async function pick(name: string) {
    if (busy || !settings) return;
    setBusy(true);
    try {
      await request("/api/elira/settings", { method: "PUT", body: { ...settings, agent_profile: name } });
      setActive(name);
    } catch { /* ignore */ } finally { setBusy(false); }
  }

  return (
    <Wrap title="Режим Elira">
      <Note>Режим задаёт характер Elira: голос, «теплоту/точность» и к каким инструментам она тянется. «Авто» — Elira сама выбирает режим под сообщение; выбор конкретного фиксирует его.</Note>
      {profiles === null ? (
        <Loading />
      ) : profiles.length === 0 ? (
        <Note>Профили недоступны (сервер не отвечает).</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {profiles.map((p) => (
            <button
              key={p.name}
              type="button"
              onClick={() => pick(p.name)}
              disabled={busy}
              className={cn(
                "flex items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-60",
                p.name === active ? "border-acl bg-acs" : "border-line hover:bg-hover",
              )}
            >
              <span className="mt-0.5 w-4 shrink-0 text-center text-[14px]">{p.icon || "•"}</span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-[13px] text-tx">
                  {p.name}
                  {p.name === active && <Check size={13} className="text-ac" />}
                  {p.is_default && p.name !== active && <span className="text-[10px] text-mut">по умолчанию</span>}
                </span>
                {p.short && <span className="block text-[11.5px] text-mut">{p.short}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </Wrap>
  );
}
