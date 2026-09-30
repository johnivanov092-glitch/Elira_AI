import { FileText, Loader2 } from "lucide-react";
import { AgentTurnView } from "./AgentTurn";
import type { FileEntry, Turn } from "./types";

export function Transcript({ turns, onResume }: { turns: Turn[]; onResume?: (turnId: string, runId: string) => void }) {
  return (
    <div className="mx-auto max-w-[760px] px-5 py-5">
      {turns.map((t) => {
        if (t.kind === "user") {
          return (
            <div key={t.id} className="my-3.5">
              <div className="ml-auto max-w-[82%] whitespace-pre-wrap rounded-[13px_13px_4px_13px] border border-acl bg-acs px-3.5 py-2.5 text-[13px]">
                {t.text}
              </div>
              {t.steering && <p role="status" className="mt-1 text-right text-[11px] text-mut">{
                t.steering.state === "applied" ? "Уточнение передано в контекст задачи"
                  : turns.some(turn => turn.kind === "agent" && turn.runId === t.steering!.runId && turn.running)
                    ? "Доставлено — ожидает следующего шага"
                    : "Ожидает обработки; сохранено для продолжения задачи"
              }</p>}
              {t.steering?.reply && <p className="mt-2 whitespace-pre-wrap text-[13px] text-t2">{t.steering.reply}</p>}
            </div>
          );
        }
        if (t.kind === "files") {
          return (
            <div key={t.id} className="my-2 flex flex-wrap justify-end gap-2">
              {t.files.map((f, i) => <FileChip key={i} file={f} />)}
            </div>
          );
        }
        return <AgentTurnView key={t.id} turn={t} onResume={onResume} />;
      })}
    </div>
  );
}

function FileChip({ file }: { file: FileEntry }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-surface p-1.5 pr-2.5">
      {file.isImage && file.url ? (
        <img src={file.url} alt={file.name} className="h-8 w-10 rounded object-cover" />
      ) : (
        <span className="grid h-8 w-8 place-items-center rounded text-ac"><FileText size={15} /></span>
      )}
      <span className="max-w-[160px] truncate text-[11.5px] text-t2">{file.name}</span>
      <span className="text-[10.5px] text-mut">
        {file.status === "uploading" ? <Loader2 size={11} className="animate-spin" /> : file.status === "saved" ? "в памяти" : file.status === "error" ? "ошибка" : "вложение"}
      </span>
    </div>
  );
}
