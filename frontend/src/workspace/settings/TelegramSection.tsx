import { useEffect, useRef, useState } from "react";
import { Play, PlugZap, RefreshCw, Square } from "lucide-react";
import {
  getTelegramStatus, listTelegramUsers, setTelegramToken, setTelegramUserAccess, startTelegramBot,
  stopTelegramBot, testTelegramBot, type TelegramStatus, type TelegramUser,
} from "../../api/telegram";
import { createPortableSecret } from "../../api/workflows";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

const btn = "flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-t2 hover:bg-hover disabled:opacity-50";

/** Telegram bot settings: token (stored in the secrets vault), start/stop/test and
 *  which chats may talk to Elira. Sending a message from a chat is the agent's job. */
export function TelegramSection() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [users, setUsers] = useState<TelegramUser[]>([]);
  const [token, setToken] = useState("");
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

  return (
    <Wrap title="Telegram">
      <Note>Бот Elira в Telegram. Токен хранится в хранилище секретов (оно должно быть разблокировано). Отправлять сообщения из чата Elira может сама — попросите «скинь в Telegram».</Note>
      {error && <div role="alert" className="mt-3 whitespace-pre-wrap break-words text-[12px] text-red-400">{error}</div>}
      {message && <div className="mt-3 text-[12px] text-t2">{message}</div>}
      {status === null ? (!error && <Loading />) : (
        <div className="mt-3 flex flex-col gap-3 text-[12.5px]">
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
          <div>
            <div className="mb-1.5 text-[11.5px] text-mut">Кто может писать боту</div>
            {users.length === 0 ? <Note>Пока никто не писал боту.</Note> : (
              <div className="flex flex-col gap-1.5">
                {users.map((user) => (
                  <label key={user.chat_id} className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2">
                    <input type="checkbox" checked={Boolean(user.allowed)} disabled={busy} onChange={(e) => void run(() => setTelegramUserAccess(user.chat_id, e.target.checked))} />
                    <span className="min-w-0 flex-1 break-words">{user.first_name || user.username || "Без имени"}{user.username ? ` (@${user.username})` : ""}</span>
                    <span className="text-[11px] text-mut">{user.chat_id}</span>
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
