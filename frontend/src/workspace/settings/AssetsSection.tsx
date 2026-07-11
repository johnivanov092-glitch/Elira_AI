import { Activity, CheckCircle2, ChevronRight, Fingerprint, History, ListChecks, Loader2, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { request } from "../../api/client";
import { streamCodeAgent } from "../../api/codeAgent";
import {
  type AssetsResp,
  type DiagnosticsAdapter,
  type EvidenceRecord,
  type EvidenceRun,
  type ItopsAsset,
  type ItopsProfile,
  type PreviewResp,
  getEvidence,
  listAssets,
  listEvidenceRuns,
  sshEnroll,
  sshPreview,
  startDiagnostics,
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
export function AssetsSection({ project }: { project: string }) {
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
  return <AssetsSurface project={project} />;
}

function AssetsSurface({ project }: { project: string }) {
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
        <AssetsList assets={assets} onReload={reload} project={project} />
        <EvidenceHistory assets={assets} />
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
  const fp = preview?.observed_fingerprint;
  // You cannot attest to a key the server never showed you: enrollment needs an
  // actually-observed fingerprint (the backend also refuses with 409 when it has none).
  const fingerprintObserved = fp?.ok === true && fp.fingerprints.length > 0;
  const canSave = Boolean(
    label.trim() && alias.trim() && previewMatches && fingerprintObserved && confirmed && busy === "",
  );

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
                  Отпечаток не наблюдён (хост недоступен или фильтруется). Сохранение
                  недоступно — подтвердить можно только реально показанный отпечаток.
                </div>
              )}
              <div className="mt-1 text-[11px] text-mut">
                Сервер лишь наблюдал этот ключ по сети — это не подтверждает личность хоста.
                Сверьте отпечаток с доверенным источником вне канала.
              </div>
            </div>
          </div>

          {fingerprintObserved && (
            <>
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
            </>
          )}
        </div>
      )}
    </div>
  );
}

function AssetsList({ assets, onReload, project }: { assets: ItopsAsset[] | null; onReload: () => void; project: string }) {
  const [verifying, setVerifying] = useState("");
  const [diagFor, setDiagFor] = useState("");
  const [diagOut, setDiagOut] = useState<{ profileId: string; text: string; err: string } | null>(null);

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

  // Start ONE server-scoped read-only diagnostic run for the chosen adapter and
  // stream it. The backend binds the run's scope to exactly that one tool; we match
  // the tool named in the start response and surface its output inline.
  async function doDiagnose(profile_id: string, adapter: DiagnosticsAdapter) {
    if (diagFor) return;
    setDiagFor(`${profile_id}:${adapter}`);
    setDiagOut({ profileId: profile_id, text: "", err: "" });
    try {
      const s = await startDiagnostics(profile_id, adapter);
      const wantTool = s.tool || "";
      let toolOut = "";
      let finalText = "";
      await streamCodeAgent({
        message: s.message,
        projectRoot: project,
        runId: s.run_id,
        maxSteps: 8,
        onEvent: (ev) => {
          if (ev.type === "tool_call" && ev.tool === wantTool) toolOut = ev.result || toolOut;
          else if (ev.type === "final_response") finalText = ev.text || finalText;
        },
        onError: (e) => setDiagOut({ profileId: profile_id, text: "", err: e.message }),
      });
      setDiagOut({ profileId: profile_id, text: toolOut || finalText || "готово", err: "" });
    } catch (e) {
      setDiagOut({ profileId: profile_id, text: "", err: errText(e, "Диагностика не удалась") });
    } finally {
      setDiagFor("");
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
                <div key={p.profile_id}>
                  <div className="mt-1.5 flex items-center gap-2 border-t border-line pt-1.5">
                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", healthDot(p.last_health?.status || "unknown"))} />
                    <span className="min-w-0 flex-1 truncate text-[11.5px] text-t2">
                      <span className="font-mono">{p.ssh_alias || p.profile_id}</span>
                      <span className="ml-1.5 text-mut">
                        {p.last_health?.status === "verified"
                          ? "проверено"
                          : p.last_health?.reason || "не проверено"}
                      </span>
                    </span>
                    {a.lifecycle_state === "enabled" && (
                      <>
                        <button
                          type="button"
                          onClick={() => void doDiagnose(p.profile_id, "healthcheck")}
                          disabled={diagFor !== ""}
                          title="Read-only health: hostname / uname -a / uptime"
                          className="flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50"
                        >
                          {diagFor === `${p.profile_id}:healthcheck` ? <Loader2 size={11} className="animate-spin" /> : <Activity size={11} />} Health
                        </button>
                        {a.kind === "linux" && (
                          <button
                            type="button"
                            onClick={() => void doDiagnose(p.profile_id, "linux_inventory")}
                            disabled={diagFor !== ""}
                            title="Read-only инвентарь Linux (os / cpu / mem / disk / net / uptime)"
                            className="flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50"
                          >
                            {diagFor === `${p.profile_id}:linux_inventory` ? <Loader2 size={11} className="animate-spin" /> : <ListChecks size={11} />} Инвентарь
                          </button>
                        )}
                      </>
                    )}
                    <button
                      type="button"
                      onClick={() => void doVerify(p.profile_id)}
                      disabled={verifying !== ""}
                      className="flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50"
                    >
                      {verifying === p.profile_id ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle2 size={11} />} Проверить
                    </button>
                  </div>
                  {diagOut?.profileId === p.profile_id && (
                    <div className="mt-1.5">
                      {diagOut.err ? (
                        <div className="rounded-lg border border-[#c98a8a]/40 bg-[#c98a8a]/10 px-3 py-2 text-[11.5px] text-[#d99a9a]">{diagOut.err}</div>
                      ) : diagOut.text ? (
                        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[11px] text-t2">{diagOut.text}</pre>
                      ) : (
                        <div className="flex items-center gap-2 px-1 text-[11.5px] text-mut"><Loader2 size={12} className="animate-spin" /> диагностика…</div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function evidenceStatus(e: EvidenceRecord): string {
  const s = (e.result?.status || "").trim();
  if (s === "ok" || s === "failed" || s === "unsupported") return s;
  return (e.exit_status || "").trim() === "0" ? "ok" : "failed";   // mirror the server fallback
}

function fmtTime(epochSeconds: number): string {
  if (!epochSeconds) return "";
  try {
    return new Date(epochSeconds * 1000).toLocaleString();
  } catch {
    return "";
  }
}

// Read-only diagnostics journal: recent runs (server summary) with an expandable,
// already-redacted per-command output. No SSH / model / host changes here.
function EvidenceHistory({ assets }: { assets: ItopsAsset[] | null }) {
  const [runs, setRuns] = useState<EvidenceRun[] | null>(null);
  const [open, setOpen] = useState("");
  const [detail, setDetail] = useState<{ runId: string; records: EvidenceRecord[] } | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const reload = useCallback(() => {
    listEvidenceRuns(50).then((r) => setRuns(r.runs ?? [])).catch(() => setRuns([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  function labelFor(targetIdentity: string): string {
    const assetId = (targetIdentity || "").split("/")[0] || "";
    return (assets ?? []).find((a) => a.asset_id === assetId)?.label || assetId || targetIdentity;
  }

  async function toggle(runId: string) {
    if (open === runId) { setOpen(""); return; }
    setOpen(runId);
    setDetail(null);
    setLoadingDetail(true);
    try {
      const r = await getEvidence(runId);
      setDetail({ runId, records: r.evidence ?? [] });
    } catch {
      setDetail({ runId, records: [] });
    } finally {
      setLoadingDetail(false);
    }
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-[12.5px] font-medium text-tx">
          <History size={13} /> История диагностик
        </span>
        <button
          type="button"
          onClick={reload}
          aria-label="Обновить"
          title="Обновить"
          className="grid h-6 w-6 shrink-0 place-items-center rounded-md border border-line text-t2 transition-colors hover:bg-hover hover:text-tx"
        >
          <RefreshCw size={12} />
        </button>
      </div>

      {runs === null ? (
        <Loading />
      ) : runs.length === 0 ? (
        <Note>Пока нет прогонов диагностики.</Note>
      ) : (
        <div className="flex flex-col gap-1.5">
          {runs.map((run) => (
            <div key={run.run_id} className="rounded-lg border border-line text-[12px]">
              <button
                type="button"
                onClick={() => void toggle(run.run_id)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-hover"
              >
                <ChevronRight size={12} className={cn("shrink-0 text-mut transition-transform", open === run.run_id && "rotate-90")} />
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium text-tx">{labelFor(run.target_identity)}</span>
                  <span className="ml-1.5 text-mut">{run.adapter}</span>
                  <span className="ml-1.5 text-[11px] text-mut">{fmtTime(run.last_at)}</span>
                </span>
                <span className="shrink-0 text-[11px]">
                  <span className="text-[#7ac98a]">ok {run.ok}</span>
                  {run.failed > 0 && <span className="ml-1.5 text-[#d99a9a]">fail {run.failed}</span>}
                  {run.unsupported > 0 && <span className="ml-1.5 text-[#e0a87a]">n/a {run.unsupported}</span>}
                </span>
              </button>
              {open === run.run_id && (
                <div className="border-t border-line px-3 py-2">
                  {loadingDetail || detail?.runId !== run.run_id ? (
                    <div className="flex items-center gap-2 text-[11.5px] text-mut"><Loader2 size={12} className="animate-spin" /> загрузка…</div>
                  ) : detail.records.length === 0 ? (
                    <Note>Нет записей для этого прогона.</Note>
                  ) : (
                    <div className="flex flex-col gap-2">
                      {detail.records.map((e) => (
                        <div key={e.evidence_id}>
                          <div className="flex items-center gap-2 text-[11.5px] text-t2">
                            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", healthDot(evidenceStatus(e) === "ok" ? "verified" : evidenceStatus(e) === "unsupported" ? "unverified" : "failed"))} />
                            <span className="font-mono">{e.operation}</span>
                            <span className="text-mut">{evidenceStatus(e)}{e.exit_status !== "" ? ` · exit ${e.exit_status}` : ""}</span>
                          </div>
                          {(e.result?.stdout || e.result?.stderr) && (
                            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[11px] text-t2">
                              {e.result?.stdout || ""}{e.result?.stderr ? `\n[stderr] ${e.result.stderr}` : ""}
                            </pre>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
