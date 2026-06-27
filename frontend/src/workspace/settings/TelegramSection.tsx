import { Play, Square } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  getTelegramConfig, listTelegramUsers, startTelegramBot, stopTelegramBot,
  testTelegramBot, toggleTelegramUser, updateTelegramConfig, type TelegramUser,
} from "../../api/telegram";
import { cn } from "../../ui/cn";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

export function TelegramSection() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [users, setUsers] = useState<TelegramUser[] | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const reload = useCallback(() => {
    getTelegramConfig().then((c) => setCfg(c as Record<string, unknown>)).catch(() => setCfg({}));
    listTelegramUsers().then((r) => setUsers(Array.isArray(r.users) ? (r.users as TelegramUser[]) : [])).catch(() => setUsers([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  const running = !!cfg?.running;
  const hasToken = !!cfg?.has_token;

  async function act(fn: () => Promise<unknown>, okMsg: string) {
    setBusy(true); setMsg("");
    try { await fn(); if (okMsg) setMsg(okMsg); reload(); }
    catch (e) { setMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  }
  async function saveToken() {
    const t = token.trim();
    if (!t) return;
    await act(() => updateTelegramConfig({ bot_token: t }), "Токен сохранён.");
    setToken("");
  }
  function toggleUser(u: TelegramUser) {
    const chatId = (u.chat_id ?? u.id) as string | number | undefined;
    const allowed = (u.allowed ?? u.is_allowed) === false; // flip
    void act(() => toggleTelegramUser({ chat_id: chatId, allowed }), "");
  }

  return (
    <Wrap title="Telegram-бот">
      <div className="mb-3 flex items-center gap-2">
        <span className={cn("h-2 w-2 shrink-0 rounded-full", running ? "bg-ac" : "bg-mut")} />
        <span className="text-[12.5px] text-t2">{running ? "запущен" : "остановлен"}{hasToken ? "" : " · нет токена"}</span>
        <div className="ml-auto flex gap-1.5">
          {running ? (
            <button type="button" onClick={() => act(stopTelegramBot, "Остановлен.")} disabled={busy} className="rounded-lg border border-line px-2.5 py-1.5 text-[12px] text-t2 hover:bg-hover hover:text-tx disabled:opacity-50">Стоп</button>
          ) : (
            <button type="button" onClick={() => act(startTelegramBot, "Запущен.")} disabled={busy || !hasToken} className="rounded-lg bg-ac px-2.5 py-1.5 text-[12px] font-medium text-[#14151b] disabled:opacity-50">Запустить</button>
          )}
          <button type="button" onClick={() => act(testTelegramBot, "Тест отправлен.")} disabled={busy} className="rounded-lg border border-line px-2.5 py-1.5 text-[12px] text-t2 hover:bg-hover hover:text-tx disabled:opacity-50">Тест</button>
        </div>
      </div>

      <div className="mb-1 text-[11.5px] text-mut">Токен бота {hasToken ? "(установлен — вставь новый, чтобы заменить)" : "— получи у @BotFather"}</div>
      <div className="mb-3 flex gap-2">
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="123456:ABC-DEF… от @BotFather" className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[12px] text-tx outline-none placeholder:text-mut focus:border-acl" />
        <button type="button" onClick={saveToken} disabled={busy || !token.trim()} className="rounded-lg bg-ac px-3 py-1.5 text-[12.5px] font-medium text-[#14151b] disabled:opacity-50">Сохранить</button>
      </div>

      {msg && <Note>{msg}</Note>}

      <div className="mb-1.5 mt-3 text-[11.5px] font-medium text-t2">Пользователи (whitelist)</div>
      {users === null ? (
        <Loading />
      ) : users.length === 0 ? (
        <Note>Пока нет. Появятся после первого сообщения боту — затем можно блокировать/разрешать.</Note>
      ) : (
        <div className="flex flex-col gap-1.5">
          {users.map((u, i) => {
            const name = String(u.username ?? u.name ?? u.chat_id ?? u.id ?? `user ${i + 1}`);
            const allowed = (u.allowed ?? u.is_allowed) !== false;
            return (
              <div key={i} className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2 text-[12.5px]">
                <span className="min-w-0 flex-1 truncate text-tx">{name}</span>
                <span className={cn("text-[11px]", allowed ? "text-ac" : "text-mut")}>{allowed ? "разрешён" : "заблокирован"}</span>
                <McpBtn onClick={() => toggleUser(u)} busy={busy} label={allowed ? "Заблокировать" : "Разрешить"}>
                  {allowed ? <Square size={13} /> : <Play size={13} />}
                </McpBtn>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-3"><Note>Создай бота у @BotFather → вставь токен → «Запустить». В режиме whitelist бот отвечает только разрешённым; блокируй/разрешай кнопкой справа.</Note></div>
    </Wrap>
  );
}
