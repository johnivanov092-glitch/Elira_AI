import { useEffect, useState } from "react";
import { Play, Volume2 } from "lucide-react";
import { getVoiceStatus, listVoices, type VoiceStatus } from "../../api/voice";
import { getAutoSpeak, getSelectedVoice, setAutoSpeak, setSelectedVoice, speak } from "../voice";
import { cn } from "../../ui/cn";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

export function VoiceSection() {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>(getSelectedVoice());
  const [auto, setAuto] = useState<boolean>(getAutoSpeak());
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    let alive = true;
    getVoiceStatus().then((s) => { if (alive) setStatus(s); });
    listVoices().then((v) => {
      if (!alive) return;
      setVoices(v);
      // Default the picker to the first voice if nothing chosen yet.
      if (!getSelectedVoice() && v.length) { setSelected(v[0]); setSelectedVoice(v[0]); }
    });
    return () => { alive = false; };
  }, []);

  function pickVoice(v: string) {
    setSelected(v);
    setSelectedVoice(v);
  }

  function toggleAuto() {
    const next = !auto;
    setAuto(next);
    setAutoSpeak(next);
  }

  async function test() {
    setTesting(true);
    try {
      await speak("Привет! Это мой голос. Так я буду звучать.");
    } finally {
      setTesting(false);
    }
  }

  return (
    <Wrap title="Голос Elira">
      <Note>
        Озвучивание ответов (TTS) на самохостед-движке Piper (CPU, LAN, строго локально).
        Голос сменяемый — новые голоса добавляются файлами на сервере. Кнопка «Озвучить» есть под каждым ответом.
      </Note>

      {status === null ? (
        <Loading />
      ) : !status.ok || voices.length === 0 ? (
        <Note>
          TTS-сервис недоступен{status.url ? ` (${status.url})` : ""}. Проверь, что контейнер
          elira-tts запущен и в нём есть хотя бы один голос. До этого озвучка работать не будет.
        </Note>
      ) : (
        <div className="mt-2 flex flex-col gap-2.5">
          <div className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2.5 text-[12.5px]">
            <Volume2 size={15} className="shrink-0 text-ac" />
            <span className="min-w-0 flex-1">
              <span className="font-medium text-tx">Голос</span>
              <span className="mt-0.5 block text-[11.5px] text-mut">Какой голос использует Elira.</span>
            </span>
            <select
              value={selected}
              onChange={(e) => pickVoice(e.target.value)}
              className="max-w-[200px] rounded-md border border-line bg-surface px-2 py-1 text-[12px] text-tx"
            >
              {voices.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
            <McpBtn onClick={() => void test()} busy={testing} label="Прослушать">
              <Play size={13} />
            </McpBtn>
          </div>

          <div className="flex items-start gap-2.5 rounded-lg border border-line px-3 py-2.5 text-[12.5px]">
            <span className="relative mt-0.5 shrink-0">
              <Volume2 size={15} className={cn(auto ? "text-ac" : "text-mut")} />
              <span className={cn("absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full ring-2 ring-surface", auto ? "bg-ac" : "bg-mut")} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="font-medium text-tx">Автоозвучивание</span>
              <span className="mt-0.5 block text-[11.5px] text-mut">Озвучивать новые ответы Elira автоматически (только свежие, не историю).</span>
            </span>
            <McpBtn onClick={toggleAuto} busy={false} label={auto ? "Выключить" : "Включить"}>
              {auto ? <Volume2 size={13} /> : <Play size={13} />}
            </McpBtn>
          </div>
        </div>
      )}
    </Wrap>
  );
}
