import { useEffect, useRef, useState } from "react";
import { KeyRound, Lock, RefreshCw, Unlock } from "lucide-react";
import {
  backupPortableVault, createPortableVault, getPortableVaultStatus, lockPortableVault, restorePortableVault,
  rotatePortableVaultRecovery, unlockPortableVault, type PortableVaultStatus,
} from "../../api/workflows";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

const btn = "flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-t2 hover:bg-hover disabled:opacity-50";
const input = "min-w-0 flex-1 rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] outline-none focus:border-acl";

/** Secrets vault: the person's actions only — create, unlock/lock, backup, restore
 *  and a new recovery key. Elira sees secrets only as opaque secret_ref values. */
export function VaultSection() {
  const [status, setStatus] = useState<PortableVaultStatus | null>(null);
  const [credential, setCredential] = useState("");
  const [confirm, setConfirm] = useState("");
  const [method, setMethod] = useState<"passphrase" | "recovery">("passphrase");
  const [path, setPath] = useState("");
  const [recoveryKey, setRecoveryKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const mounted = useRef(false);

  async function refresh() {
    try {
      const next = await getPortableVaultStatus();
      if (mounted.current) { setStatus(next); setError(""); }
    } catch (err) {
      if (mounted.current) setError(String(err));
    }
  }

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => { mounted.current = false; };
  }, []);

  async function run(action: () => Promise<PortableVaultStatus | Record<string, unknown>>, done: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await action();
      const key = (result as PortableVaultStatus).recovery_key;
      if (mounted.current) {
        if (key) setRecoveryKey(key);
        setMessage(done);
        setCredential("");
        setConfirm("");
      }
    } catch (err) {
      if (mounted.current) setError(String(err));
    } finally {
      if (mounted.current) setBusy(false);
      await refresh();
    }
  }

  function create() {
    if (!credential || credential !== confirm) { setError("Пароли не совпадают."); return; }
    void run(() => createPortableVault(credential), "Хранилище создано и разблокировано.");
  }

  return (
    <Wrap title="Хранилище секретов">
      <Note>Пароли, токены и ключи интеграций. Elira получает их только как ссылку secret_ref; ввести новый секрет можно здесь или в карточке, которую покажет задача.</Note>
      {error && <div role="alert" className="mt-3 whitespace-pre-wrap break-words text-[12px] text-red-400">{error}</div>}
      {message && <div className="mt-3 text-[12px] text-t2">{message}</div>}
      {recoveryKey && (
        <div className="mt-3 rounded-lg border border-acl px-3 py-2.5 text-[12.5px]">
          <div className="mb-1 font-medium">Ключ восстановления — сохраните, он показывается один раз</div>
          <code className="break-all text-[12px]">{recoveryKey}</code>
          <div className="mt-2"><button type="button" className={btn} onClick={() => setRecoveryKey("")}>Сохранил(а)</button></div>
        </div>
      )}
      {status === null ? (!error && <Loading />) : (
        <div className="mt-3 flex flex-col gap-3 text-[12.5px]">
          <div className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2.5">
            <span className={`h-2 w-2 shrink-0 rounded-full ${!status.initialized ? "bg-mut" : status.locked ? "bg-amber-400" : "bg-green-400"}`} />
            <div className="min-w-0 flex-1">
              <div className="font-medium">{!status.initialized ? "Не создано" : status.locked ? "Заблокировано" : "Разблокировано"}</div>
              <div className="text-[11.5px] text-mut">{status.initialized ? `Секретов: ${status.record_count}` : "Создайте хранилище, чтобы подключать интеграции с паролями и токенами."}</div>
            </div>
            <McpBtn busy={busy} label="Обновить" onClick={() => void refresh()}><RefreshCw size={13} /></McpBtn>
          </div>

          {!status.initialized && (
            <div className="flex flex-col gap-1.5">
              <input type="password" autoComplete="new-password" placeholder="Пароль хранилища" value={credential} onChange={(e) => setCredential(e.target.value)} className={input} />
              <input type="password" autoComplete="new-password" placeholder="Повторите пароль" value={confirm} onChange={(e) => setConfirm(e.target.value)} className={input} />
              <div><button type="button" className={btn} disabled={busy || !credential} onClick={create}><KeyRound size={13} /> Создать</button></div>
            </div>
          )}

          {status.initialized && status.locked && (
            <div className="flex flex-col gap-1.5">
              <div className="flex gap-3 text-[11.5px] text-mut">
                <label className="flex items-center gap-1"><input type="radio" checked={method === "passphrase"} onChange={() => setMethod("passphrase")} /> Пароль</label>
                <label className="flex items-center gap-1"><input type="radio" checked={method === "recovery"} onChange={() => setMethod("recovery")} /> Ключ восстановления</label>
              </div>
              <div className="flex gap-2">
                <input type="password" autoComplete="current-password" value={credential} onChange={(e) => setCredential(e.target.value)} className={input} />
                <button type="button" className={btn} disabled={busy || !credential} onClick={() => void run(() => unlockPortableVault(credential, method), "Хранилище разблокировано.")}><Unlock size={13} /> Разблокировать</button>
              </div>
            </div>
          )}

          {status.initialized && !status.locked && (
            <div className="flex flex-wrap gap-2">
              <button type="button" className={btn} disabled={busy} onClick={() => void run(lockPortableVault, "Хранилище заблокировано.")}><Lock size={13} /> Заблокировать</button>
              <button type="button" className={btn} disabled={busy} onClick={() => void run(rotatePortableVaultRecovery, "Выдан новый ключ восстановления.")}><KeyRound size={13} /> Новый ключ восстановления</button>
            </div>
          )}

          {status.initialized && (
            <div className="flex flex-col gap-1.5">
              <label className="text-[11.5px] text-mut" htmlFor="vault-path">Файл резервной копии (полный путь)</label>
              <div className="flex flex-wrap gap-2">
                <input id="vault-path" value={path} onChange={(e) => setPath(e.target.value)} placeholder="D:\Backup\elira-vault.json" className={input} />
                <button type="button" className={btn} disabled={busy || !path.trim() || status.locked} onClick={() => void run(() => backupPortableVault(path.trim()), "Резервная копия записана.")}>Сохранить копию</button>
                <button type="button" className={btn} disabled={busy || !path.trim()} onClick={() => void run(() => restorePortableVault(path.trim()), "Копия подготовлена: разблокируйте хранилище, чтобы восстановить.")}>Восстановить</button>
              </div>
            </div>
          )}
        </div>
      )}
    </Wrap>
  );
}
