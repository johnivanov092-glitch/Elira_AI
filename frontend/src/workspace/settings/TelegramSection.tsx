import { useEffect, useRef, useState } from "react";
import { Crown, Lock, Play, PlugZap, RefreshCw, Square } from "lucide-react";
import {
  createTelegramPairCode, getTelegramStatus, listTelegramUsers, setTelegramToken, setTelegramUserAccess,
  startTelegramBot, stopTelegramBot, testTelegramBot, type TelegramStatus, type TelegramUser,
} from "../../api/telegram";
import { createPortableSecret, unlockPortableVault } from "../../api/workflows";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

const btn = "flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-t2 hover:bg-hover disabled:opacity-50";

/** Telegram bot settings: token (stored in the secrets vault), start/stop/test and
 *  which chats may talk to Elira. Sending a message from a chat is the agent's job. */
export function TelegramSection() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [users, setUsers] = useState<TelegramUser[]>([]);
  const [token, setToken] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [pair, setPair] = useState<{ code: string; expires_at: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const mounted = useRef(false);

  async function refresh() {
    try {
      const [next, rows] = await Promise.all([getTelegramStatus(), listTelegramUsers()]);
      if (mounted.current) { setStatus(next); setUsers(rows); setError(""); }
    } catch (err) {
      if (mounted.current) setError(String(err));
    }
  }

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => { mounted.current = false; };
  }, []);

  async function run(action: () => Promise<unknown>, done?: (result: unknown) => string) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await action();
      if (mounted.current && done) setMessage(done(result));
    } catch (err) {
      if (mounted.current) setError(String(err));
    } finally {
      if (mounted.current) setBusy(false);
      await refresh();
    }
  }

  async function saveToken() {
    const value = token.trim();
    if (!value) return;
    await run(async () => {
      const secret = await createPortableSecret({ kind: "token", value });
      await setTelegramToken(secret.secret_ref);
      if (mounted.current) setToken("");
    }, () => "Токен сохранён в хранилище секретов.");
  }

  async function unlock() {
    const value = passphrase;
    if (!value) return;
    await run(async () => {
      await unlockPortableVault(value);
      if (mounted.current) setPassphrase("");
    }, () => (status?.enabled ? "Хранилище разблокировано — бот запускается." : "Хранилище разблокировано."));
  }

  async function makePairCode() {
    await run(async () => {
      const result = await createTelegramPairCode();
      if (mounted.current) setPair({ code: result.code, expires_at: result.expires_at });
    });
  }

  const owner = status?.owner;
  return (
    <Wrap title="Telegram">
      <Note>Бот Elira в Telegram. Токен хранится в хранилище секретов (оно должно быть разблокировано). Отправлять сообщения из чата Elira может сама — попросите «скинь в Telegram».</Note>
      {error && <div role="alert" className="mt-3 whitespace-pre-wrap break-words text-[12px] text-red-400">{error}</div>}
      {message && <div className="mt-3 text-[12px] text-t2">{message}</div>}
      {status === null ? (!error && <Loading />) : (
        <div className="mt-3 flex flex-col gap-3 text-[12.5px]">
          {status.vault_initialized === false && (
            <div className="flex flex-col gap-1 rounded-lg border border-line px-3 py-2.5">
              <div className="flex items-center gap-2 font-medium"><Lock size={13} /> Хранилище секретов не создано</div>
              <div className="text-[11.5px] text-mut">Токен бота хранится в нём: создайте хранилище в разделе «Хранилище секретов» и сохраните ключ восстановления.</div>
            </div>
          )}
          {status.vault_locked && (
            <div className="flex flex-col gap-1.5 rounded-lg border border-line px-3 py-2.5">
              <div className="flex items-center gap-2 font-medium"><Lock size={13} /> Хранилище секретов заперто</div>
              <div className="text-[11.5px] text-mut">
                Токен бота лежит в нём: без разблокировки ключ не сохранить и бот не запустить.
                {status.enabled ? " Включённый бот запустится сам после разблокировки." : ""}
              </div>
              <div className="flex gap-2">
                <input type="password" autoComplete="current-password" aria-label="Пароль хранилища" placeholder="Пароль хранилища"
                  value={passphrase} onChange={(e) => setPassphrase(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") void unlock(); }}
                  className="min-w-0 flex-1 rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] outline-none focus:border-acl" />
                <button type="button" className={btn} disabled={busy || !passphrase} onClick={() => void unlock()}>Разблокировать</button>
              </div>
              <div className="text-[11px] text-mut">Ключ восстановления — в разделе «Хранилище секретов».</div>
            </div>
          )}
          <div className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2.5">
            <span className={`h-2 w-2 shrink-0 rounded-full ${status.running ? "bg-green-400" : "bg-mut"}`} />
            <div className="min-w-0 flex-1">
              <div className="font-medium">{status.running ? "Бот работает" : status.stopping ? "Бот останавливается" : "Бот остановлен"}</div>
              <div className="text-[11.5px] text-mut">{status.has_token ? `Токен: ${status.token_preview}` : "Токен не задан"}</div>
            </div>
            <McpBtn busy={busy} label="Обновить" onClick={() => void refresh()}><RefreshCw size={13} /></McpBtn>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={btn} disabled={busy || !status.has_token || status.running} onClick={() => void run(startTelegramBot, () => "Бот запущен.")}><Play size={13} /> Запустить</button>
            <button type="button" className={btn} disabled={busy || !status.running} onClick={() => void run(stopTelegramBot, () => "Бот остановлен.")}><Square size={13} /> Остановить</button>
            <button type="button" className={btn} disabled={busy || !status.has_token} onClick={() => void run(testTelegramBot, (r) => `Связь есть: @${(r as { bot_username?: string }).bot_username ?? ""}`)}><PlugZap size={13} /> Проверить связь</button>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[11.5px] text-mut" htmlFor="tg-token">{status.has_token ? "Заменить токен бота" : "Токен бота (от @BotFather)"}</label>
            <div className="flex gap-2">
              <input id="tg-token" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} className="min-w-0 flex-1 rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] outline-none focus:border-acl" />
              <button type="button" className={btn} disabled={busy || !token.trim()} onClick={() => void saveToken()}>Сохранить</button>
            </div>
          </div>
          <div className="flex flex-col gap-1.5 rounded-lg border border-line px-3 py-2.5">
            <div className="flex items-center gap-2 font-medium"><Crown size={13} /> Владелец</div>
            <div className="text-[11.5px] text-mut">
              {owner
                ? `${owner.first_name || owner.username || owner.chat_id}${owner.username ? ` (@${owner.username})` : ""} — полный доступ, как в Elira; переписка в чате «Telegram».`
                : "Не привязан. Владелец общается с Elira как в приложении — с полным доступом."}
            </div>
            {pair ? (
              <div className="text-[12px]">
                Отправьте боту со своего аккаунта: <code className="rounded bg-surface px-1.5 py-0.5 font-mono">/pair {pair.code}</code>
                <span className="ml-1 text-[11px] text-mut">(код действует 10 минут, одна привязка)</span>
              </div>
            ) : (
              <div>
                <button type="button" className={btn} disabled={busy || !status.running} onClick={() => void makePairCode()}>
                  {owner ? "Сменить владельца" : "Привязать мой Telegram"}
                </button>
                {!status.running && <span className="ml-2 text-[11px] text-mut">сначала запустите бота</span>}
              </div>
            )}
          </div>
          <div>
            <div className="mb-1.5 text-[11.5px] text-mut">Кто ещё может писать боту — ограниченный доступ: разговор без действий</div>
            {users.length === 0 ? <Note>Пока никто не писал боту.</Note> : (
              <div className="flex flex-col gap-1.5">
                {users.map((user) => (
                  <label key={user.chat_id} className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2">
                    <input type="checkbox" checked={Boolean(user.allowed)} disabled={busy} onChange={(e) => void run(() => setTelegramUserAccess(user.chat_id, e.target.checked))} />
                    <span className="min-w-0 flex-1 break-words">{user.first_name || user.username || "Без имени"}{user.username ? ` (@${user.username})` : ""}</span>
                    <span className="text-[11px] text-mut">{owner?.chat_id === user.chat_id ? "владелец" : user.chat_id}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Wrap>
  );
}
