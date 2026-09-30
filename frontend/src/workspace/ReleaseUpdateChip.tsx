import { CheckCircle2, CircleAlert, CircleDashed, Clock3, LoaderCircle, PauseCircle, RefreshCw, RotateCcw, WifiOff } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { API_BASE } from "../api/client";
import {
  createReleaseApproval, createReleaseRollback, pollReleaseStatus, releaseIsBusy, RELEASE_INITIAL_OBSERVATION,
  type ReleaseApproval, type ReleaseConfirmation, type ReleaseObservation, type ReleasePhase, type RollbackApproval,
} from "../api/releases";
import { IconButton } from "../ui/Button";

const PHASE_LABELS: Record<ReleasePhase, string> = {
  idle: "Нет активного обновления",
  preparing: "Подготовка обновления",
  prepared: "Кандидат подготовлен",
  checking: "Проверка обновления",
  verified: "Проверено, ещё не установлено",
  awaiting_confirmation: "Ожидает вашего подтверждения",
  waiting: "Установка одобрена, ожидает завершения работы",
  switching: "Переключение версии",
  completed: "Обновление установлено",
  rolling_back: "Возврат предыдущей версии",
  failed: "Обновление не завершено",
  interrupted: "Обновление прервано",
  unavailable: "Состояние обновления недоступно",
};
const MODES = { legacy: "Локальная установка", foundation: "Служба обновлений", development: "Режим разработки" };
const APPROVAL_LABELS: Record<ReleaseApproval["state"], string> = {
  sending: "Передаём подтверждение…",
  accepted: "Запрос принят. Ожидаем состояние установки.",
  conflict: "Предложение изменилось или уже обработано. Обновляем состояние.",
  uncertain: "Ответ не получен: подтверждение могло быть принято. Проверяем состояние; повторная отправка автоматически не выполняется.",
};
type NoticeStorage = Pick<Storage, "getItem" | "setItem">;
const noticed = new Set<string>();

/** Remember only presentation, never permission to install. */
export function claimReleaseNotice(requestId: string, storage: NoticeStorage | null, apiBase = API_BASE): boolean {
  const key = `elira.release-confirmation.seen:${apiBase}:${requestId}`;
  if (noticed.has(key)) return false;
  noticed.add(key);
  try {
    if (storage?.getItem(key) === "1") return false;
    storage?.setItem(key, "1");
  } catch {
    // Private/quota-limited WebViews still avoid repeated prompts this session.
  }
  return true;
}

function noticeStorage(): NoticeStorage | null {
  try { return window.localStorage; } catch { return null; }
}

export function releasePresentation(observation: ReleaseObservation) {
  const { status, connection } = observation;
  const label = connection === "reconnecting" ? "Восстанавливаем связь"
    : !status ? "Получаем состояние обновления"
      : status.mode === "development" && status.phase === "idle" ? MODES.development : PHASE_LABELS[status.phase];
  const busy = connection === "connected" && status !== null && releaseIsBusy(status.phase);
  const success = connection === "connected" && status?.phase === "completed";
  const failure = connection === "connected" && status?.phase === "failed";
  return { label, busy, success, failure };
}

function eventTime(seconds: number | null): string {
  return seconds === null ? "Нет сведений" : new Date(seconds * 1000).toLocaleString("ru-RU");
}

export function ReleaseUpdateDetails({ observation }: { observation: ReleaseObservation }) {
  const { status, connection } = observation;
  const disconnected = connection !== "connected";
  return (
    <>
      <div className="text-[11.5px] font-medium text-tx" role="status" aria-live="polite">
        {releasePresentation(observation).label}
      </div>
      {status && <div className="mt-1 text-[10px] text-mut">{MODES[status.mode]}</div>}
      {disconnected && (
        <p className="mt-2 text-[11px] text-t2">
          {status ? "Показаны последние полученные данные. Текущее состояние установки не подтверждено."
            : "Ожидаем ответ приложения. Состояние установки пока неизвестно."}
        </p>
      )}
      {status && (
        <dl className="mt-3 space-y-2 text-[11px]">
          <div>
            <dt className="text-mut">Активная версия</dt>
            <dd className="break-all font-mono text-t2">{status.active_release_id || "Не указана"}</dd>
          </div>
          {status.previous_release_id && <div>
            <dt className="text-mut">Сохранённая версия</dt>
            <dd className="break-all font-mono text-t2">{status.previous_release_id}</dd>
          </div>}
          <div>
            <dt className="text-mut">{disconnected ? "Последний известный этап" : "Этап"}</dt>
            <dd className="text-t2">{PHASE_LABELS[status.phase]}</dd>
          </div>
          {status.step && (
            <div>
              <dt className="text-mut">Шаг {status.step.index} из {status.step.total}</dt>
              <dd className="break-words text-t2">{status.step.label}</dd>
            </div>
          )}
          <div>
            <dt className="text-mut">Последнее событие</dt>
            <dd className="text-t2">{eventTime(status.updated_at)}</dd>
          </div>
        </dl>
      )}
      {status?.error && <p className="mt-3 max-h-32 overflow-auto break-words whitespace-pre-wrap text-[11px] text-danger">{status.error}</p>}
    </>
  );
}

export function ReleaseRollbackControl({ observation, approval, onConfirm }: {
  observation: ReleaseObservation;
  approval: RollbackApproval | null;
  onConfirm: (active: string, previous: string) => void;
}) {
  const [selection, setSelection] = useState<{ active: string; previous: string; update: boolean } | null>(null);
  const status = observation.status;
  const update = status?.saved_release_action === "update";
  const label = update ? "Обновиться на новую версию" : "Откатиться на прежнюю версию";
  const current = approval && selection?.active === approval.active && selection?.previous === approval.previous ? approval : null;
  const available = observation.connection === "connected" && status?.rollback_available;
  const pending = approval?.state === "sending";
  const unchanged = available && selection?.active === status?.active_release_id
    && selection?.previous === status?.previous_release_id;
  if (!status?.previous_release_id || (status.phase === "awaiting_confirmation"
    && status.confirmation?.release_id === status.previous_release_id)) return null;
  return (
    <section className="mt-3 border-t border-line pt-3" aria-label={label}>
      {!selection ? <button type="button" disabled={!available || pending}
        onClick={() => setSelection({ active: status.active_release_id!, previous: status.previous_release_id!, update })}
        className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-[11px] text-t2 hover:bg-hover disabled:opacity-50">
        {update ? <RefreshCw size={12} /> : <RotateCcw size={12} />} {label}
      </button> : <>
        <h3 className="text-[12px] font-medium text-tx">{selection.update ? "Обновиться на новую версию" : "Откатиться на прежнюю версию"}</h3>
        <div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
          <p className="break-all font-mono text-[10px] text-t2">{selection.previous}</p>
          <div className="flex flex-col gap-1.5">
            <button type="button" disabled={!unchanged || pending || current?.state === "accepted" || current?.state === "uncertain"}
              onClick={() => onConfirm(selection.active, selection.previous)}
              className="rounded-md border border-acl bg-acs px-2.5 py-1.5 text-[11px] text-ac hover:bg-hover disabled:opacity-50">Установить сейчас</button>
            <button type="button" disabled={pending} onClick={() => setSelection(null)}
              className="rounded-md border border-line px-2.5 py-1.5 text-[11px] text-t2 hover:bg-hover disabled:opacity-50">{current && current.state !== "sending" ? "Закрыть" : "Позже"}</button>
          </div>
        </div>
        <p className="mt-2 text-[11px] text-t2">Установка дождётся завершения активных задач. Версия {selection.active} и её кандидат сохранятся; чаты останутся на месте.</p>
        {!unchanged && !pending && <p role="status" className="mt-2 text-[11px] text-t2">Выбранная версия изменилась или связь потеряна. Выберите версию заново после обновления состояния.</p>}
      </>}
      {current && <p role="status" aria-live="polite" className="mt-2 text-[11px] text-t2">{
        current.state === "sending" ? "Передаём запрос переключения…"
          : current.state === "accepted" ? "Запрос принят. Ожидаем переключение версии."
            : current.state === "conflict" ? "Выбранная версия изменилась или выполняется другая операция. Обновляем состояние."
              : "Ответ не получен: переключение могло быть принято. Проверяем состояние."
      }</p>}
    </section>
  );
}

export function ReleaseConfirmationPrompt({ confirmation, connected, approval, onConfirm, onLater, actionLabel = "Обновиться на новую версию" }: {
  confirmation: ReleaseConfirmation;
  connected: boolean;
  approval: ReleaseApproval | null;
  onConfirm: (requestId: string) => void;
  onLater: () => void;
  actionLabel?: string;
}) {
  const current = approval?.requestId === confirmation.request_id ? approval : null;
  const pending = approval?.state === "sending";
  return (
    <section className="mb-3 border-b border-line pb-3" aria-label="Подтверждение установки">
      <h3 className="text-[12px] font-medium text-tx">{actionLabel}</h3>
      <div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
        <p className="break-all font-mono text-[10px] text-t2">{confirmation.release_id}</p>
        <div className="flex flex-col gap-1.5">
        {current?.state !== "accepted" && <button type="button" disabled={!connected || pending}
          onClick={() => onConfirm(confirmation.request_id)}
          className="rounded-md border border-acl bg-acs px-2.5 py-1.5 text-[11px] text-ac hover:bg-hover disabled:cursor-default disabled:opacity-50">
          Установить сейчас
        </button>}
        <button type="button" disabled={pending} onClick={onLater}
          className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-[11px] text-t2 hover:bg-hover disabled:opacity-50">
          {current && current.state !== "sending" ? "Закрыть" : "Позже"}
        </button>
        </div>
      </div>
      <p className="mt-2 text-[11px] text-t2">
        Текущая работа сохранится. Установка дождётся завершения активных задач, затем приложение ненадолго перезапустится. Вручную закрывать приложение не нужно.
      </p>
      {current && <p role="status" aria-live="polite" className="mt-2 text-[11px] text-t2">{APPROVAL_LABELS[current.state]}</p>}
    </section>
  );
}

/** Only the explicit button submits a proposal; polling never grants approval. */
export function ReleaseUpdateChip() {
  const [observation, setObservation] = useState(RELEASE_INITIAL_OBSERVATION);
  const [approval, setApproval] = useState<ReleaseApproval | null>(null);
  const [rollbackApproval, setRollbackApproval] = useState<RollbackApproval | null>(null);
  const [open, setOpen] = useState(false);
  const latest = useRef(RELEASE_INITIAL_OBSERVATION);
  const action = useRef<ReturnType<typeof createReleaseApproval> | null>(null);
  const rollbackAction = useRef<ReturnType<typeof createReleaseRollback> | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const presentation = releasePresentation(observation);
  useEffect(() => {
    const polling = pollReleaseStatus((value) => {
      latest.current = value;
      setObservation(value);
    });
    const controller = createReleaseApproval({ current: () => latest.current, observe: setApproval, refresh: polling.refresh });
    action.current = controller;
    const rollback = createReleaseRollback({ current: () => latest.current, observe: setRollbackApproval, refresh: polling.refresh });
    rollbackAction.current = rollback;
    return () => {
      action.current = null;
      controller.stop();
      rollback.stop();
      rollbackAction.current = null;
      polling.stop();
    };
  }, []);
  const confirmation = observation.status?.phase === "awaiting_confirmation" ? observation.status.confirmation : null;
  useEffect(() => {
    if (observation.connection === "connected" && confirmation
      && claimReleaseNotice(confirmation.request_id, noticeStorage())) setOpen(true);
  }, [confirmation, observation.connection]);
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    }
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        root.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", keydown);
    };
  }, [open]);
  const phase = observation.status?.phase;
  const Icon = observation.connection === "reconnecting" ? WifiOff
    : presentation.busy ? LoaderCircle
      : presentation.success ? CheckCircle2
        : presentation.failure || phase === "unavailable" ? CircleAlert
          : phase === "waiting" || phase === "awaiting_confirmation" ? Clock3
            : phase === "interrupted" ? PauseCircle
              : observation.status ? RefreshCw : CircleDashed;
  const tone = presentation.success ? "text-success" : presentation.failure ? "text-danger"
    : presentation.busy ? "text-ac" : "text-t2";
  return (
    <div ref={root} className="relative">
      <IconButton
        onClick={() => setOpen((value) => !value)} active={open}
        title={`Обновление Elira: ${presentation.label}`}
        aria-label={`Обновление Elira: ${presentation.label}`}
        aria-expanded={open} aria-controls={open ? id : undefined} aria-haspopup="dialog"
      >
        <Icon size={16} aria-hidden="true" className={`${tone}${presentation.busy ? " animate-spin motion-reduce:animate-none" : ""}`} />
      </IconButton>
      {open && (
        <div ref={panel} id={id} role="dialog" aria-labelledby={`${id}-title`} tabIndex={-1}
          className="absolute right-0 top-full z-30 mt-1.5 w-[320px] max-w-[calc(100vw-24px)] rounded-lg border border-line bg-card p-3 shadow-lg outline-none">
          <h2 id={`${id}-title`} className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-mut">Обновление Elira</h2>
          {confirmation && <ReleaseConfirmationPrompt confirmation={confirmation}
            actionLabel={confirmation.release_id === observation.status?.previous_release_id
              && observation.status.saved_release_action !== "update" ? "Откатиться на прежнюю версию" : "Обновиться на новую версию"}
            connected={observation.connection === "connected"} approval={approval}
            onConfirm={(requestId) => { void action.current?.confirm(requestId); }}
            onLater={() => { setOpen(false); root.current?.querySelector<HTMLButtonElement>("button")?.focus(); }} />}
          <ReleaseUpdateDetails observation={observation} />
          <ReleaseRollbackControl observation={observation} approval={rollbackApproval}
            onConfirm={(active, previous) => { void rollbackAction.current?.confirm(active, previous); }} />
        </div>
      )}
    </div>
  );
}
