import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  getPersonaStatus, listPersonaCandidates, rollbackPersona,
  type PersonaCandidate, type PersonaStatus,
} from "../../api/persona";
import { Loading, Note, Wrap } from "./_shared";

export function PersonaSection() {
  const [status, setStatus] = useState<PersonaStatus | null>(null);
  const [candidates, setCandidates] = useState<PersonaCandidate[] | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    getPersonaStatus().then(setStatus).catch(() => setStatus(null));
    listPersonaCandidates().then(setCandidates).catch(() => setCandidates([]));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  async function rollback() {
    if (busy || !status || status.previous_version == null) return;
    const target = status.previous_version;
    if (!window.confirm(`Откатить личность к версии ${target}? Текущая версия ${status.active_version} будет заменена.`)) return;
    setBusy(true);
    try {
      await rollbackPersona(target);
      reload();
    } catch { /* offline */ } finally { setBusy(false); }
  }

  if (status === null) return <Wrap title="Личность"><Loading /></Wrap>;

  return (
    <Wrap title="Личность">
      <Note>
        Ядро личности «{status.persona_name}» неизменно во всех профилях и моделях. Меняется только манера —
        наблюдаемый, версионируемый слой: реплики анализируются, черты-кандидаты копятся в карантине и при
        достаточных подтверждениях продвигаются в новую версию. Эта панель — только наблюдение и откат.
      </Note>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="rounded-md border border-acl bg-acs px-2.5 py-1 text-[12px] text-ac">
          версия {status.active_version}
        </span>
        <span className="rounded-md border border-line px-2.5 py-1 text-[12px] text-mut">
          статус: {status.status}
        </span>
        <span className="rounded-md border border-line px-2.5 py-1 text-[12px] text-mut">
          в карантине: {status.quarantine_candidates}
        </span>
        {status.last_evolution_at && (
          <span className="rounded-md border border-line px-2.5 py-1 text-[12px] text-mut">
            эволюция: {fmtWhen(status.last_evolution_at)}
          </span>
        )}
        {status.previous_version != null && (
          <button
            type="button"
            onClick={rollback}
            disabled={busy}
            className="ml-auto rounded-md border border-line px-2.5 py-1 text-[12px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-60"
          >
            Откатить к версии {status.previous_version}
          </button>
        )}
      </div>

      <PersonaGroup title="Кандидаты в карантине">
        {candidates === null ? (
          <Loading />
        ) : candidates.length === 0 ? (
          <Note>Карантин пуст — новых черт-кандидатов пока нет.</Note>
        ) : (
          <div className="flex flex-col gap-1.5">
            {candidates.map((c) => (
              <div key={c.id} className="rounded-lg border border-line px-3 py-2.5">
                <div className="flex items-center gap-2 text-[13px] text-tx">
                  <span className="min-w-0 flex-1 truncate">{c.candidate.summary || c.trait_key}</span>
                  <span className="shrink-0 text-[11px] text-ac">{(c.confidence_avg * 100).toFixed(0)}%</span>
                </div>
                <div className="mt-0.5 text-[11.5px] text-mut">
                  слой {c.layer} · улик {c.evidence_count} · противоречие {(c.contradiction_score * 100).toFixed(0)}%
                </div>
              </div>
            ))}
          </div>
        )}
      </PersonaGroup>

      <PersonaGroup title="История продвижений">
        {status.latest_traits.length === 0 ? (
          <Note>Пока ни одна черта не продвинута — личность на исходной версии.</Note>
        ) : (
          <div className="flex flex-col gap-1.5">
            {status.latest_traits.map((t) => (
              <div key={t.trait_key} className="rounded-lg border border-line px-3 py-2.5">
                <div className="flex items-center gap-2 text-[13px] text-tx">
                  <span className="min-w-0 flex-1 truncate">{t.summary || t.trait_key}</span>
                  {t.promoted_version != null && (
                    <span className="shrink-0 text-[11px] text-mut">v{t.promoted_version}</span>
                  )}
                </div>
                <div className="mt-0.5 text-[11.5px] text-mut">{fmtWhen(t.last_seen)}</div>
              </div>
            ))}
          </div>
        )}
      </PersonaGroup>

      <PersonaGroup title="Калибровка по моделям">
        {status.model_consistency.length === 0 ? (
          <Note>Калибровок пока нет — личность ещё не наблюдалась ни на одной модели.</Note>
        ) : (
          <div className="flex flex-col gap-1.5">
            {status.model_consistency.map((m) => (
              <div key={`${m.model}:${m.version_id}`} className="rounded-lg border border-line px-3 py-2.5">
                <div className="flex items-center gap-2 text-[13px] text-tx">
                  <span className="min-w-0 flex-1 truncate">{m.model}</span>
                  <span className="shrink-0 text-[11px] text-ac">{(m.consistency_score * 100).toFixed(0)}%</span>
                </div>
                <div className="mt-0.5 text-[11.5px] text-mut">
                  {[m.calibration.verbosity, m.calibration.formatting, m.calibration.list_bias]
                    .filter(Boolean)
                    .join(" · ") || "без отклонений"}
                </div>
              </div>
            ))}
          </div>
        )}
      </PersonaGroup>
    </Wrap>
  );
}

function PersonaGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-4">
      <div className="mb-1.5 text-[11.5px] font-medium uppercase tracking-wide text-mut">{title}</div>
      {children}
    </div>
  );
}

function fmtWhen(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString();
}
