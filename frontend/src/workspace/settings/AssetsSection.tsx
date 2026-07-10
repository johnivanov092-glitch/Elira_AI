import { CheckCircle2, Fingerprint, Loader2, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { request } from "../../api/client";
import {
  type AssetsResp,
  type ItopsAsset,
  type ItopsProfile,
  type PreviewResp,
  listAssets,
  sshEnroll,
  sshPreview,
  verifyProfile,
} from "../../api/itops";
import { toast } from "../../components/ToastHost";
import { cn } from "../../ui/cn";
import { Loading, Note, Wrap } from "./_shared";

const TITLE = "Активы / Подключения";

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function healthDot(status: string): string {
  if (status === "verified") return "bg-[#7ac98a]";
  if (status === "unverified") return "bg-[#e0a87a]";
  return "bg-[#c98a8a]";
}

function lifecycleTone(state: string): string {
  return state === "enabled" ? "text-[#7ac98a]" : "text-[#e0a87a]";
}

// Whole surface is flag-gated: the section body reads the itops flag on mount and
// shows a pointer to the Experimental tab when it is off (and every /api/itops route
// 404s anyway). Nothing here grants the model host access — enroll/verify only save
// and check a connection; model use needs a separate scope/approval layer.
export function AssetsSection() {
  const [ready, setReady] = useState(false);
  const [on, setOn] = useState(false);

  useEffect(() => {
    let alive = true;
    request<{ itops?: boolean }>("/api/elira/feature-flags")
      .then((f) => { if (alive) { setOn(Boolean(f.itops)); setReady(true); } })
      .catch(() => { if (alive) { setOn(false); setReady(true); } });
    return () => { alive = false; };
  }, []);

  if (!ready) return <Wrap title={TITLE}><Loading /></Wrap>;
  if (!on) {
    return (
      <Wrap title={TITLE}>
        <Note>
          Раздел выключен. Включите флаг «IT-операции (SSH-соединения)» во вкладке
          «Экспериментальное», затем вернитесь сюда.
        </Note>
      </Wrap>
    );
  }
  return <AssetsSurface />;
}

function AssetsSurface() {
  const [assets, setAssets] = useState<ItopsAsset[] | null>(null);

  const reload = useCallback(() => {
    listAssets()
      .then((r: AssetsResp) => setAssets(r.assets ?? []))
      .catch(() => setAssets([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  return (
    <Wrap title={TITLE}>
      <div className="flex flex-col gap-6">
        <EnrollBlock onEnrolled={reload} />
        <AssetsList assets={assets} onReload={reload} />
      </div>
    </Wrap>
  );
}

function EnrollBlock({ onEnrolled }: { onEnrolled: () => void }) {
  const [label, setLabel] = useState("");
  const [alias, setAlias] = useState("");
  const [preview, setPreview] = useState<PreviewResp | null>(null);
  // The exact alias `preview` (and therefore `confirmed`) belongs to. The attestation
  // is bound to THIS alias; editing the alias away from it voids preview+confirm so a
  // user can never attest to host A's fingerprint and enroll host B under it.
  const [previewAlias, setPreviewAlias] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState<"" | "preview" | "save">("");
  const [err, setErr] = useState("");

  async function doPreview() {
    const a = alias.trim();
    if (!a || busy) return;
    setBusy("preview"); setErr(""); setPreview(null); setConfirmed(false); setPreviewAlias("");
    try {
      const p = await sshPreview(a);
      setPreview(p);
      setPreviewAlias(a);   // bind the attestation-to-be to the alias actually previewed
    } catch (e) {
      setErr(errText(e, "Не удалось получить конфигурацию алиаса"));
    } finally {
      setBusy("");
    }
  }

  // Save requires the preview/attestation to still match the current alias — a stale
  // preview for a different host can never enable Save (belt to the onChange reset).
  const previewMatches = preview?.ok === true && previewAlias === alias.trim();
  const canSave = Boolean(label.trim() && alias.trim() && previewMatches && confirmed && busy === "");

  async function doSave() {
    if (!canSave) return;
    setBusy("save"); setErr("");
    try {
      const r = await sshEnroll({ label: label.trim(), ssh_alias: alias.trim() });
      toast.success(`Сохранено: ${r.asset.label} (${r.asset.lifecycle_state})`);
      setLabel(""); setAlias(""); setPreview(null); setConfirmed(false);
      onEnrolled();
    } catch (e) {
      setErr(errText(e, "Не удалось сохранить подключение"));
    } finally {
      setBusy("");
    }
  }

  const fp = preview?.observed_fingerprint;

  return (
    <div>
      <div className="mb-2 text-[12.5px] font-medium text-tx">Новое SSH-подключение</div>
      <Note>
        Работает только с уже настроенным в вашем OpenSSH алиасом (~/.ssh/config). Ничего
        не создаёт и не меняет в ~/.ssh, не хранит пароль/ключ. Модель по этому подключению
        не получает доступа — сохранение и проверка не дают ей подключаться.
      </Note>

      {err && (
        <div className="mt-2 rounded-lg border border-[#c98a8a]/40 bg-[#c98a8a]/10 px-3 py-2 text-[12px] text-[#d99a9a]">
          {err}
        </div>
      )}

      <div className="mt-2 flex flex-col gap-2">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Название (например «Прод-сервер лаборатории»)"
          className="rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
        />
        <div className="flex gap-2">
          <input
            value={alias}
            onChange={(e) => {
              const v = e.target.value;
              setAlias(v);
              // Diverging from the previewed alias voids the attestation: force a
              // re-preview + re-confirm so the displayed/attested host always equals
              // the enrolled one.
              if (v.trim() !== previewAlias) { setPreview(null); setConfirmed(false); }
            }}
            onKeyDown={(e) => { if (e.key === "Enter") void doPreview(); }}
            placeholder="ssh-алиас (например ai-server)"
            className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
          />
          <button
            type="button"
            onClick={() => void doPreview()}
            disabled={busy !== "" || !alias.trim()}
            className={cn(
              "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] text-[#14151b] transition-opacity",
              alias.trim() && busy === "" ? "bg-ac hover:opacity-90" : "cursor-not-allowed bg-ac/40",
            )}
          >
            {busy === "preview" ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />} Проверить алиас
          </button>
        </div>
      </div>

      {preview?.ok && previewMatches && (
        <div className="mt-2 flex flex-col gap-2 rounded-lg border border-line px-3 py-2.5 text-[12.5px]">
          <div className="text-t2">
            <span className="text-mut">Хост: </span>
            <span className="font-mono text-tx">{preview.effective.hostname}:{String(preview.effective.port)}</span>
            {preview.effective.user && (
              <>
                <span className="text-mut"> · пользователь: </span>
                <span className="font-mono text-tx">{preview.effective.user}</span>
              </>
            )}
          </div>

          <div className="flex items-start gap-2 rounded-lg border border-[#e0a87a]/40 bg-[#e0a87a]/10 px-2.5 py-2 text-[12px] text-t2">
            <Fingerprint size={14} className="mt-0.5 shrink-0 text-[#e0a87a]" />
            <div className="min-w-0">
              {fp?.ok && fp.fingerprints.length > 0 ? (
                <>
                  <div className="mb-1 text-mut">Наблюдаемый отпечаток (advisory, НЕ доказательство):</div>
                  {fp.fingerprints.map((f, i) => (
                    <div key={i} className="break-all font-mono text-[11.5px] text-tx">{f}</div>
                  ))}
                </>
              ) : (
                <div className="text-mut">
                  Отпечаток не наблюдён (хост недоступен/фильтруется). Сверьте ключ хоста
                  самостоятельно перед подтверждением.
                </div>
              )}
              <div className="mt-1 text-[11px] text-mut">
                Сервер лишь наблюдал этот ключ по сети — это не подтверждает личность хоста.
                Сверьте отпечаток с доверенным источником вне канала.
              </div>
            </div>
          </div>

          <label className="flex cursor-pointer items-start gap-2 text-[12px] text-t2">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 shrink-0"
            />
            <span>
              Я сверил(а) отпечаток с доверенным источником вне канала (out-of-band).
              Это подтверждение действия человека, а не криптографическое доказательство.
            </span>
          </label>

          <div>
            <button
              type="button"
              onClick={() => void doSave()}
              disabled={!canSave}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] text-[#14151b] transition-opacity",
                canSave ? "bg-ac hover:opacity-90" : "cursor-not-allowed bg-ac/40",
              )}
            >
              {busy === "save" ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />} Сохранить подключение
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function AssetsList({ assets, onReload }: { assets: ItopsAsset[] | null; onReload: () => void }) {
  const [verifying, setVerifying] = useState("");

  async function doVerify(profile_id: string) {
    if (verifying) return;
    setVerifying(profile_id);
    try {
      const r = await verifyProfile(profile_id);
      if (r.ok) toast.success("Подключение проверено — актив включён");
      else toast.error(r.health?.reason || "Проверка не удалась");
      onReload();
    } catch (e) {
      toast.error(errText(e, "Не удалось выполнить проверку"));
    } finally {
      setVerifying("");
    }
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[12.5px] font-medium text-tx">Сохранённые активы</span>
        <button
          type="button"
          onClick={onReload}
          aria-label="Обновить"
          title="Обновить"
          className="grid h-6 w-6 shrink-0 place-items-center rounded-md border border-line text-t2 transition-colors hover:bg-hover hover:text-tx"
        >
          <RefreshCw size={12} />
        </button>
      </div>

      {assets === null ? (
        <Loading />
      ) : assets.length === 0 ? (
        <Note>Пока нет сохранённых активов. Добавьте SSH-подключение выше.</Note>
      ) : (
        <div className="flex flex-col gap-2">
          {assets.map((a) => (
            <div key={a.asset_id} className="rounded-lg border border-line px-3 py-2.5 text-[12.5px]">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium text-tx">{a.label}</span>
                  <span className="ml-1.5 font-mono text-[11.5px] text-mut">{a.endpoint}</span>
                </span>
                <span className={cn("shrink-0 text-[11px] font-medium", lifecycleTone(a.lifecycle_state))}>
                  {a.lifecycle_state === "enabled" ? "включён" : "черновик"}
                </span>
              </div>
              {a.profiles.map((p: ItopsProfile) => (
                <div key={p.profile_id} className="mt-1.5 flex items-center gap-2 border-t border-line pt-1.5">
                  <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", healthDot(p.last_health?.status || "unknown"))} />
                  <span className="min-w-0 flex-1 truncate text-[11.5px] text-t2">
                    <span className="font-mono">{p.ssh_alias || p.profile_id}</span>
                    <span className="ml-1.5 text-mut">
                      {p.last_health?.status === "verified"
                        ? "проверено"
                        : p.last_health?.reason || "не проверено"}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => void doVerify(p.profile_id)}
                    disabled={verifying !== ""}
                    className={cn(
                      "flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50",
                    )}
                  >
                    {verifying === p.profile_id ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle2 size={11} />} Проверить
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
