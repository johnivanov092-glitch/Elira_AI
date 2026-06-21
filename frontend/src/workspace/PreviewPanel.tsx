import { useState } from "react";
import { X } from "lucide-react";
import MarkdownRenderer from "../components/MarkdownRenderer";
import { cn } from "../ui/cn";
import { lineDiff, type Artifacts } from "./artifacts";

type Tab = "preview" | "code" | "console" | "diff";

const TABS: { id: Tab; label: string }[] = [
  { id: "preview", label: "Превью" },
  { id: "code", label: "Код" },
  { id: "console", label: "Консоль" },
  { id: "diff", label: "Диф" },
];

export function PreviewPanel({ artifacts, onClose }: { artifacts: Artifacts; onClose: () => void }) {
  const file = artifacts.file;
  const isHtml = !!file && /\.html?$/i.test(file.path);
  const isMd = !!file && /\.(md|markdown)$/i.test(file.path);
  const [tab, setTab] = useState<Tab>(isHtml || isMd ? "preview" : file ? "code" : "console");

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-line bg-side">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <span className="truncate font-mono text-[11.5px] text-t2">{file?.path ?? "превью"}</span>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="ml-auto grid h-6 w-6 place-items-center rounded-md border border-line text-t2 hover:bg-hover hover:text-tx">
          <X size={14} />
        </button>
      </div>

      <div className="flex gap-0.5 border-b border-line px-2 py-1.5">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              "rounded-md px-2.5 py-1 text-[11.5px] font-medium transition-colors",
              tab === t.id ? "bg-acs text-ac" : "text-mut hover:bg-hover hover:text-t2",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "preview" && (
          isHtml && file ? (
            <iframe title="preview" sandbox="allow-scripts" srcDoc={file.content} className="h-full w-full border-0 bg-white" />
          ) : isMd && file ? (
            <div className="px-4 py-3 text-[13px]"><MarkdownRenderer content={file.content} /></div>
          ) : (
            <Empty>Нет визуального превью — смотри вкладку «Код».</Empty>
          )
        )}

        {tab === "code" && (
          file ? <Pre>{file.content}</Pre> : <Empty>Файл ещё не создан.</Empty>
        )}

        {tab === "console" && (
          artifacts.console ? <Pre>{artifacts.console}</Pre> : <Empty>Команды ещё не запускались.</Empty>
        )}

        {tab === "diff" && (
          file?.old !== undefined && file.action !== "create" ? (
            <pre className="px-3 py-3 font-mono text-[11.5px] leading-relaxed">
              {lineDiff(file.old ?? "", file.content).map((l, i) => (
                <div
                  key={i}
                  className={cn(
                    "whitespace-pre-wrap",
                    l.sign === "+" && "text-[#9bb892]",
                    l.sign === "-" && "text-[#c98a8a]",
                    l.sign === " " && "text-mut",
                  )}
                >
                  {l.sign} {l.text}
                </div>
              ))}
            </pre>
          ) : file ? (
            <Empty>{file.action === "create" ? "Новый файл — изменений нет." : "Диф недоступен."}</Empty>
          ) : (
            <Empty>Изменений пока нет.</Empty>
          )
        )}
      </div>
    </aside>
  );
}

function Pre({ children }: { children: string }) {
  return (
    <pre className="whitespace-pre-wrap px-3 py-3 font-mono text-[11.5px] leading-relaxed text-t2">
      {children}
    </pre>
  );
}

function Empty({ children }: { children: string }) {
  return <div className="grid h-full place-items-center px-4 text-center text-[12.5px] text-mut">{children}</div>;
}
