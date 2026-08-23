import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CircleHelp,
  KeyRound,
  Loader2,
  MessageSquareText,
  ShieldAlert,
} from "lucide-react";
import {
  createPortableSecret,
  createPortableVault,
  getPortableVaultStatus,
  listPendingWorkflowRequests,
  resolveWorkflowRequest,
  runNativeElevation,
  unlockPortableVault,
  type PortableSecretKind,
  type PortableVaultStatus,
  type WorkflowRequest,
  type WorkflowRequestAction,
} from "../api/workflows";
import { cn } from "../ui/cn";
import { useWorkflowRequestEvents } from "./useWorkflowRequestEvents";

type RequestValues = Record<string, unknown>;
type WorkflowFormValue = string | number | boolean;

type WorkflowRequestCardProps = {
  request: WorkflowRequest;
  onResolve: (
    requestId: string,
    action: WorkflowRequestAction,
    values?: RequestValues,
  ) => Promise<void>;
  onElevation?: (request: WorkflowRequest) => Promise<RequestValues>;
};

type JsonSchemaProperty = {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
};

function schemaProperties(request: WorkflowRequest): Record<string, JsonSchemaProperty> {
  const properties = request.schema?.properties;
  if (!properties || typeof properties !== "object" || Array.isArray(properties)) return {};
  return properties as Record<string, JsonSchemaProperty>;
}

function initialValues(request: WorkflowRequest): Record<string, WorkflowFormValue> {
  const values: Record<string, WorkflowFormValue> = {};
  for (const [name, property] of Object.entries(schemaProperties(request))) {
    if (property.type === "boolean") {
      values[name] = Boolean(property.default);
    } else if (property.default !== undefined) {
      values[name] = String(property.default);
    } else if (Array.isArray(property.enum) && property.enum.length > 0) {
      values[name] = String(property.enum[0]);
    } else {
      values[name] = "";
    }
  }
  return values;
}

function requestedSecretKind(request: WorkflowRequest): PortableSecretKind {
  const value = request.schema?.["x-elira-secret-kind"];
  return value === "password"
    || value === "private_key"
    || value === "connection_string"
    || value === "token"
    ? value
    : "token";
}

function existingSecretRef(request: WorkflowRequest): string {
  const value = request.schema?.["x-elira-existing-secret-ref"];
  return typeof value === "string" ? value : "";
}

function requestPresentation(kind: WorkflowRequest["kind"]) {
  if (kind === "secret") {
    return { Icon: KeyRound, title: "Нужен секрет", className: "text-amber-300" };
  }
  if (kind === "elevation") {
    return { Icon: ShieldAlert, title: "Требуются права Windows", className: "text-amber-300" };
  }
  if (kind === "approval") {
    return { Icon: CircleHelp, title: "Нужно разрешение", className: "text-ac" };
  }
  return { Icon: MessageSquareText, title: "Нужны данные", className: "text-ac" };
}

export function WorkflowRequestCard({
  request,
  onResolve,
  onElevation,
}: WorkflowRequestCardProps) {
  const [values, setValues] = useState<Record<string, WorkflowFormValue>>(
    () => initialValues(request),
  );
  const [secretRef, setSecretRef] = useState(() => existingSecretRef(request));
  const [secretValue, setSecretValue] = useState("");
  const [secretKind, setSecretKind] = useState<PortableSecretKind>(
    () => requestedSecretKind(request),
  );
  const [vaultStatus, setVaultStatus] = useState<PortableVaultStatus | null>(null);
  const [vaultCredential, setVaultCredential] = useState("");
  const [vaultCredentialConfirm, setVaultCredentialConfirm] = useState("");
  const [vaultUnlockMethod, setVaultUnlockMethod] = useState<"passphrase" | "recovery">("passphrase");
  const [recoveryKey, setRecoveryKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const properties = useMemo(() => schemaProperties(request), [request]);
  const required = useMemo(
    () => new Set(Array.isArray(request.schema?.required) ? request.schema.required.map(String) : []),
    [request],
  );
  const needsReconciliation = request.status === "needs_reconciliation";
  const { Icon, title, className } = requestPresentation(request.kind);

  useEffect(() => {
    if (request.kind !== "secret") return;
    setSecretRef(existingSecretRef(request));
    setSecretKind(requestedSecretKind(request));
    let disposed = false;
    void getPortableVaultStatus().then(
      (status) => {
        if (!disposed) {
          setVaultStatus(status);
          setError("");
        }
      },
      (cause) => {
        if (!disposed) {
          setError(cause instanceof Error ? cause.message : "Не удалось проверить portable vault.");
        }
      },
    );
    return () => { disposed = true; };
  }, [request.kind, request.request_id]);

  const submit = async (
    action: WorkflowRequestAction,
    requestValues: RequestValues = {},
  ) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await onResolve(request.request_id, action, requestValues);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось продолжить workflow.");
      setBusy(false);
    }
  };

  const submitInput = () => {
    const missing = [...required].filter((name) => {
      const value = values[name];
      return value === undefined || (typeof value === "string" && !value.trim());
    });
    if (missing.length > 0) {
      setError(`Заполните обязательные поля: ${missing.join(", ")}`);
      return;
    }
    void submit("accept", values);
  };

  const runElevation = async () => {
    if (!onElevation || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await onElevation(request);
      await onResolve(request.request_id, "accept", result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "UAC bootstrap не выполнен.");
      setBusy(false);
    }
  };

  const createVault = async () => {
    if (busy || !vaultCredential) return;
    if (vaultCredential !== vaultCredentialConfirm) {
      setError("Passphrase и подтверждение не совпадают.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const status = await createPortableVault(vaultCredential);
      setVaultStatus(status);
      setRecoveryKey(status.recovery_key ?? "");
      setVaultCredential("");
      setVaultCredentialConfirm("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось создать portable vault.");
    } finally {
      setBusy(false);
    }
  };

  const unlockVault = async () => {
    if (busy || !vaultCredential) return;
    setBusy(true);
    setError("");
    try {
      const status = await unlockPortableVault(vaultCredential, vaultUnlockMethod);
      setVaultStatus(status);
      setVaultCredential("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось разблокировать portable vault.");
    } finally {
      setBusy(false);
    }
  };

  const storeSecretAndContinue = async () => {
    if (busy || !secretValue) return;
    setBusy(true);
    setError("");
    try {
      const stored = await createPortableSecret({
        kind: secretKind,
        value: secretValue,
        lifecycle: "persistent",
      });
      setSecretValue("");
      setSecretRef(stored.secret_ref);
      await onResolve(request.request_id, "accept", { secret_ref: stored.secret_ref });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось сохранить секрет.");
      setBusy(false);
    }
  };

  return (
    <section
      className="rounded-xl border border-acl bg-surface px-3.5 py-3 shadow-sm"
      aria-live="polite"
      data-workflow-request={request.request_id}
    >
      <div className="flex items-center gap-2">
        <Icon size={16} className={className} />
        <span className="text-[12.5px] font-medium text-tx">{title}</span>
        <span className="ml-auto font-mono text-[10px] text-mut">{request.step_id}</span>
      </div>

      {request.message && <p className="mt-2 text-[12px] text-t2">{request.message}</p>}
      {needsReconciliation && (
        <p className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-[11px] text-amber-200">
          Не повторяйте действие вслепую: после сбоя внешний результат мог уже измениться.
        </p>
      )}

      {request.kind === "input" && (
        <div className="mt-3 flex flex-col gap-2">
          {Object.entries(properties).map(([name, property]) => (
            <label key={name} className="flex flex-col gap-1 text-[11.5px] text-t2">
              <span>{property.title || name}{required.has(name) ? " *" : ""}</span>
              {Array.isArray(property.enum) && property.enum.length > 0 ? (
                <select
                  name={name}
                  value={String(values[name] ?? "")}
                  onChange={(event) => setValues((current) => ({
                    ...current,
                    [name]: event.target.value,
                  }))}
                  className="rounded-lg border border-line bg-bg px-2.5 py-2 text-[12px] text-tx outline-none focus:border-acl"
                >
                  {!required.has(name) && <option value="">—</option>}
                  {property.enum.map((option) => (
                    <option key={String(option)} value={String(option)}>{String(option)}</option>
                  ))}
                </select>
              ) : property.type === "boolean" ? (
                <input
                  name={name}
                  type="checkbox"
                  checked={Boolean(values[name])}
                  onChange={(event) => setValues((current) => ({
                    ...current,
                    [name]: event.target.checked,
                  }))}
                  className="h-4 w-4 accent-[var(--color-ac)]"
                />
              ) : (
                <input
                  name={name}
                  type={property.type === "number" || property.type === "integer" ? "number" : "text"}
                  value={String(values[name] ?? "")}
                  onChange={(event) => setValues((current) => ({
                    ...current,
                    [name]: property.type === "number" || property.type === "integer"
                      ? event.target.valueAsNumber
                      : event.target.value,
                  }))}
                  className="rounded-lg border border-line bg-bg px-2.5 py-2 text-[12px] text-tx outline-none focus:border-acl"
                />
              )}
              {property.description && <span className="text-[10.5px] text-mut">{property.description}</span>}
            </label>
          ))}
          <button
            type="button"
            onClick={submitInput}
            disabled={busy}
            className="mt-1 rounded-lg bg-ac px-3 py-2 text-[12px] font-medium text-[#14151b] disabled:opacity-50"
          >
            {busy ? "Продолжаю…" : needsReconciliation ? "Я проверил — повторить шаг" : "Продолжить workflow"}
          </button>
        </div>
      )}

      {request.kind === "secret" && (
        <div className="mt-3 flex flex-col gap-2">
          {vaultStatus === null && !error && (
            <div className="flex items-center gap-2 text-[11px] text-mut">
              <Loader2 size={12} className="animate-spin" /> Проверяю portable vault…
            </div>
          )}

          {vaultStatus && !vaultStatus.initialized && (
            <div className="flex flex-col gap-2 rounded-lg border border-line bg-bg p-2.5">
              <p className="text-[11px] text-t2">
                Создайте переносимое зашифрованное хранилище. Оно не зависит от Windows Credential Manager или DPAPI.
              </p>
              <input
                name="vault_passphrase"
                type="password"
                value={vaultCredential}
                onChange={(event) => setVaultCredential(event.target.value)}
                autoComplete="new-password"
                placeholder="Passphrase"
                className="rounded-lg border border-line bg-surface px-2.5 py-2 text-[12px] text-tx outline-none focus:border-acl"
              />
              <input
                name="vault_passphrase_confirm"
                type="password"
                value={vaultCredentialConfirm}
                onChange={(event) => setVaultCredentialConfirm(event.target.value)}
                autoComplete="new-password"
                placeholder="Повторите passphrase"
                className="rounded-lg border border-line bg-surface px-2.5 py-2 text-[12px] text-tx outline-none focus:border-acl"
              />
              <button
                type="button"
                onClick={() => void createVault()}
                disabled={busy || !vaultCredential || !vaultCredentialConfirm}
                className="rounded-lg bg-ac px-3 py-2 text-[12px] font-medium text-[#14151b] disabled:opacity-50"
              >
                {busy ? "Создаю…" : "Создать portable vault"}
              </button>
            </div>
          )}

          {vaultStatus?.initialized && vaultStatus.locked && (
            <div className="flex flex-col gap-2 rounded-lg border border-line bg-bg p-2.5">
              <select
                value={vaultUnlockMethod}
                onChange={(event) => setVaultUnlockMethod(event.target.value as "passphrase" | "recovery")}
                className="rounded-lg border border-line bg-surface px-2.5 py-2 text-[12px] text-tx outline-none focus:border-acl"
              >
                <option value="passphrase">Passphrase</option>
                <option value="recovery">Recovery key</option>
              </select>
              <input
                name="vault_credential"
                type="password"
                value={vaultCredential}
                onChange={(event) => setVaultCredential(event.target.value)}
                autoComplete="current-password"
                placeholder={vaultUnlockMethod === "recovery" ? "elira-recovery-v1-…" : "Passphrase"}
                className="rounded-lg border border-line bg-surface px-2.5 py-2 text-[12px] text-tx outline-none focus:border-acl"
              />
              <button
                type="button"
                onClick={() => void unlockVault()}
                disabled={busy || !vaultCredential}
                className="rounded-lg bg-ac px-3 py-2 text-[12px] font-medium text-[#14151b] disabled:opacity-50"
              >
                {busy ? "Разблокирую…" : "Разблокировать vault"}
              </button>
            </div>
          )}

          {recoveryKey && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5">
              <p className="text-[11px] text-amber-200">
                Сохраните recovery key отдельно. После закрытия карточки он больше не показывается.
              </p>
              <code className="mt-1 block break-all select-all text-[10.5px] text-tx">{recoveryKey}</code>
            </div>
          )}

          {vaultStatus?.initialized && !vaultStatus.locked && (
            <div className="flex flex-col gap-2 rounded-lg border border-line bg-bg p-2.5">
              <div className="flex items-center justify-between gap-2 text-[10.5px] text-mut">
                <span>Portable vault разблокирован</span>
                <span>{vaultStatus.record_count} записей</span>
              </div>
              {secretRef && (
                <button
                  type="button"
                  onClick={() => void submit("accept", { secret_ref: secretRef })}
                  disabled={busy}
                  className="rounded-lg border border-acl bg-acs px-3 py-2 text-[12px] font-medium text-ac disabled:opacity-50"
                >
                  {busy ? "Продолжаю…" : "Продолжить с сохранённым secret_ref"}
                </button>
              )}
              <select
                name="secret_kind"
                value={secretKind}
                onChange={(event) => setSecretKind(event.target.value as PortableSecretKind)}
                className="rounded-lg border border-line bg-surface px-2.5 py-2 text-[12px] text-tx outline-none focus:border-acl"
              >
                <option value="token">Токен / API key</option>
                <option value="password">Пароль</option>
                <option value="private_key">Приватный ключ</option>
                <option value="connection_string">Строка подключения</option>
              </select>
              {secretKind === "private_key" || secretKind === "connection_string" ? (
                <textarea
                  name="secret_value"
                  value={secretValue}
                  onChange={(event) => setSecretValue(event.target.value)}
                  autoComplete="off"
                  rows={4}
                  placeholder="Значение записывается напрямую в vault"
                  className="resize-y rounded-lg border border-line bg-surface px-2.5 py-2 font-mono text-[12px] text-tx outline-none focus:border-acl"
                />
              ) : (
                <input
                  name="secret_value"
                  type="password"
                  value={secretValue}
                  onChange={(event) => setSecretValue(event.target.value)}
                  autoComplete="off"
                  placeholder="Значение записывается напрямую в vault"
                  className="rounded-lg border border-line bg-surface px-2.5 py-2 text-[12px] text-tx outline-none focus:border-acl"
                />
              )}
              <p className="text-[10.5px] text-mut">
                Модель и workflow получат только непрозрачный secret_ref; значение не попадёт в transcript или resolution event.
              </p>
              <button
                type="button"
                onClick={() => void storeSecretAndContinue()}
                disabled={busy || !secretValue}
                className="rounded-lg bg-ac px-3 py-2 text-[12px] font-medium text-[#14151b] disabled:opacity-50"
              >
                {busy ? "Сохраняю…" : needsReconciliation ? "Сохранить и повторить шаг" : "Сохранить и продолжить workflow"}
              </button>
            </div>
          )}

          <details className="rounded-lg border border-line px-2.5 py-2">
            <summary className="cursor-pointer text-[10.5px] text-mut">Использовать существующий secret_ref</summary>
            <div className="mt-2 flex flex-col gap-2">
              <input
                name="secret_ref"
                value={secretRef}
                onChange={(event) => setSecretRef(event.target.value)}
                autoComplete="off"
                placeholder="sref_…"
                className="rounded-lg border border-line bg-bg px-2.5 py-2 font-mono text-[12px] text-tx outline-none focus:border-acl"
              />
              <button
                type="button"
                onClick={() => void submit("accept", { secret_ref: secretRef.trim() })}
                disabled={busy || !secretRef.trim()}
                className="rounded-lg border border-acl bg-acs px-3 py-2 text-[12px] font-medium text-ac disabled:opacity-50"
              >
                {busy ? "Продолжаю…" : "Использовать secret_ref"}
              </button>
            </div>
          </details>
        </div>
      )}

      {request.kind === "elevation" && (
        <div className="mt-3 flex flex-col gap-2">
          <p className="text-[10.5px] text-mut">
            Windows покажет системный UAC-диалог. Команда выполнится отдельным повышенным процессом; backend не требует постоянных прав администратора.
          </p>
          <button
            type="button"
            onClick={() => void runElevation()}
            disabled={busy || !onElevation}
            className="rounded-lg border border-acl bg-acs px-3 py-2 text-[12px] font-medium text-ac disabled:opacity-50"
          >
            {busy ? "Проверяю…" : "Запустить UAC bootstrap"}
          </button>
        </div>
      )}

      {request.kind === "approval" && (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={() => void submit("accept")}
            disabled={busy}
            className="rounded-lg bg-ac px-3 py-2 text-[12px] font-medium text-[#14151b] disabled:opacity-50"
          >
            {needsReconciliation ? "Я проверил — повторить действие" : "Разрешить"}
          </button>
          <button
            type="button"
            onClick={() => void submit("decline")}
            disabled={busy}
            className="rounded-lg border border-line px-3 py-2 text-[12px] text-t2 hover:bg-hover disabled:opacity-50"
          >
            Отклонить
          </button>
        </div>
      )}

      {request.kind !== "approval" && (
        <button
          type="button"
          onClick={() => void submit("cancel")}
          disabled={busy}
          className="mt-2 text-[11px] text-mut hover:text-t2 disabled:opacity-50"
        >
          Отменить workflow
        </button>
      )}
      {error && <p className="mt-2 text-[11px] text-danger">{error}</p>}
    </section>
  );
}


export function WorkflowRequestTray({ connected }: { connected: boolean }) {
  const [requests, setRequests] = useState<WorkflowRequest[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!connected) return;
    try {
      const pending = await listPendingWorkflowRequests();
      setRequests(pending);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить запросы workflow.");
    }
  }, [connected]);
  useEffect(() => {
    if (!connected) setRequests([]);
  }, [connected]);
  const { streamError, retryStream } = useWorkflowRequestEvents(connected, refresh);

  const resolve = async (
    requestId: string,
    action: WorkflowRequestAction,
    values: RequestValues = {},
  ) => {
    setLoading(true);
    try {
      await resolveWorkflowRequest(requestId, action, values);
      setRequests((current) => current.filter((item) => item.request_id !== requestId));
      setError("");
    } finally {
      setLoading(false);
    }
  };

  const visibleError = error || streamError;
  if (requests.length === 0 && !visibleError) return null;
  return (
    <div className="border-t border-line bg-bg px-4 py-3">
      <div className="mx-auto flex max-w-[760px] flex-col gap-2">
        <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-mut">
          Workflow ожидает вас
          {loading && <Loader2 size={12} className="animate-spin" />}
        </div>
        {requests.map((item) => (
          <WorkflowRequestCard
            key={item.request_id}
            request={item}
            onResolve={resolve}
            onElevation={runNativeElevation}
          />
        ))}
        {visibleError && (
          <div className={cn("rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-[11px] text-danger")}>
            <span>{visibleError}</span>
            {streamError && (
              <button
                type="button"
                onClick={retryStream}
                className="ml-2 underline decoration-dotted underline-offset-2 hover:text-red-200"
              >
                Повторить live-подключение
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
