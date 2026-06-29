/**
 * Voice input (Living Persona step D, slice 2). Toggle-record: click to start,
 * click to stop → the clip is transcribed by the self-hosted whisper service
 * and the text is handed to `onText` (the composer appends it). Mic access is
 * the webview's getUserMedia (the OS prompts for permission); strictly local —
 * audio goes only to the LAN STT service.
 */
import { useRef, useState } from "react";
import { Loader2, Mic, Square } from "lucide-react";
import { transcribeAudio } from "../api/voice";
import { toast } from "../components/ToastHost";
import { cn } from "../ui/cn";

export function MicButton({ onText, disabled }: { onText: (text: string) => void; disabled?: boolean }) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  async function start() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        setBusy(true);
        try {
          const text = await transcribeAudio(blob);
          if (text.trim()) onText(text.trim());
          else toast.info("Ничего не распознано");
        } catch {
          toast.error("Распознавание не удалось");
        } finally {
          setBusy(false);
        }
      };
      rec.start();
      recRef.current = rec;
      setRecording(true);
    } catch {
      toast.error("Нет доступа к микрофону");
    }
  }

  function stop() {
    try {
      recRef.current?.stop();
    } catch {
      /* ignore */
    }
    recRef.current = null;
    setRecording(false);
  }

  const Icon = busy ? Loader2 : recording ? Square : Mic;
  return (
    <button
      type="button"
      onClick={() => (recording ? stop() : void start())}
      disabled={disabled || busy}
      title={recording ? "Остановить и распознать" : "Голосовой ввод"}
      aria-label="Голосовой ввод"
      className={cn(
        "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border transition-colors disabled:opacity-60",
        recording ? "border-acl bg-acs text-ac" : "border-line text-t2 hover:bg-hover hover:text-tx",
      )}
    >
      <Icon size={13} className={busy ? "animate-spin" : recording ? "animate-pulse" : ""} />
    </button>
  );
}
