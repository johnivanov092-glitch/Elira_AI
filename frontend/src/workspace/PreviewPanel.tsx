import { useEffect, useState } from "react";
import { CheckCircle2, ChevronRight, Download, FileText, Folder, Loader2, X } from "lucide-react";
import { DownloadLink } from "../components/DownloadLink";
import MarkdownRenderer from "../components/MarkdownRenderer";
import { getAdvancedProjectTree, readAdvancedProjectFile } from "../api/project";
import { buildApiUrl } from "../api/client";
import { cn } from "../ui/cn";
import { lineDiff, type Artifacts } from "./artifacts";

type Tab = "preview" | "code" | "console" | "diff" | "tree";

const TABS: { id: Tab; label: string }[] = [
  { id: "preview", label: "Превью" },
  { id: "code", label: "Код" },
  { id: "console", label: "Консоль" },
  { id: "diff", label: "Диф" },
  { id: "tree", label: "Дерево" },
];

function pdfPreviewUrl(url: string, name: string): string | null {
  if (!/\.pdf$/i.test(name) && !/\.pdf(?:$|[?#])/i.test(url)) return null;
  const full = buildApiUrl(url);
  try {
    const parsed = new URL(full);
    parsed.pathname = parsed.pathname.replace("/api/skills/download/", "/api/skills/view/");
    return parsed.toString();
  } catch {
    return null;
  }
}

export function PreviewPanel({ artifacts, project, onClose }: { artifacts: Artifacts; project: string; onClose: () => void }) {
  const file = artifacts.file;
  const server = artifacts.server;
  const downloads = artifacts.downloads;
  const newestDownload = downloads.at(-1);
  const [selectedDownloadKey, setSelectedDownloadKey] = useState(newestDownload?.key ?? "");
  const selectedDownload = selectedDownloadKey
    ? downloads.find((item) => item.key === selectedDownloadKey) ?? newestDownload
    : undefined;
  const isHtml = !!file && /\.html?$/i.test(file.path);
  const isMd = !!file && /\.(md|markdown)$/i.test(file.path);
  const pdfUrl = selectedDownload
    ? pdfPreviewUrl(selectedDownload.url, selectedDownload.name)
    : null;
  const [tab, setTab] = useState<Tab>(server || isHtml || isMd || downloads.length > 0 ? "preview" : file ? "code" : "console");

  useEffect(() => {
    if (!newestDownload) return;
    setSelectedDownloadKey(newestDownload.key);
    setTab("preview");
  }, [newestDownload?.key]);

  useEffect(() => {
    if (!server) return;
    setSelectedDownloadKey("");
    setTab("preview");
  }, [server?.key]);

  function selectDownload(key: string) {
    setSelectedDownloadKey(key);
    setTab("preview");
  }

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-line bg-side">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <span className="truncate font-mono text-[11.5px] text-t2">{selectedDownload?.name ?? server?.url ?? file?.path ?? "превью"}</span>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="ml-auto grid h-6 w-6 place-items-center rounded-md border border-line text-t2 hover:bg-hover hover:text-tx">
          <X size={14} />
        </button>
      </div>

      {downloads.length > 0 && (
        <div className="border-b border-line bg-card px-2.5 py-2">
          <div className="mb-1.5 px-1 text-[10.5px] font-medium uppercase tracking-wide text-mut">
            Файлы · {downloads.length}
          </div>
          <div className="max-h-44 space-y-1 overflow-y-auto pr-0.5">
            {downloads.map((download) => {
              const active = download.key === selectedDownload?.key;
              return (
                <div
                  key={download.key}
                  data-artifact-option={download.key}
                  className={cn(
                    "flex min-w-0 items-center gap-1 rounded-lg border px-1.5 py-1 transition-colors",
                    active ? "border-acl bg-acs" : "border-transparent hover:border-line hover:bg-hover",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => selectDownload(download.key)}
                    className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-0.5 text-left"
                    title={`Открыть ${download.name}`}
                  >
                    <FileText size={14} className={cn("shrink-0", active ? "text-ac" : "text-mut")} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-[11px] text-t2">{download.name}</span>
                      {download.documentQa?.status === "passed" && (
                        <span data-document-qa="passed" className="mt-0.5 flex items-center gap-1 text-[9.5px] text-success">
                          <CheckCircle2 size={10} /> Проверено
                          {download.documentQa.page_count ? ` · ${download.documentQa.page_count} стр.` : ""}
                        </span>
                      )}
                    </span>
                  </button>
                  <DownloadLink
                    url={download.url}
                    name={download.name}
                    title={`Скачать ${download.name}`}
                    aria-label={`Скачать ${download.name}`}
                    className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-ac hover:bg-surface"
                  >
                    <Download size={13} />
                  </DownloadLink>
                </div>
              );
            })}
          </div>
        </div>
      )}

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
          pdfUrl ? (
            <iframe
              title="PDF preview"
              src={pdfUrl}
              className="h-full w-full border-0 bg-white"
            />
          ) : selectedDownload ? (
            <div className="grid h-full place-items-center px-5 text-center">
              <div className="max-w-[300px] rounded-xl border border-line bg-card px-4 py-4">
                <FileText size={24} className="mx-auto mb-2 text-ac" />
                <div className="truncate font-mono text-[12px] text-t2" title={selectedDownload.name}>{selectedDownload.name}</div>
                <p className="mt-1.5 text-[11.5px] leading-relaxed text-mut">
                  Встроенное визуальное превью для этого формата недоступно. Файл можно скачать отдельно.
                </p>
                <DownloadLink
                  url={selectedDownload.url}
                  name={selectedDownload.name}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-acl bg-acs px-3 py-1.5 text-[11.5px] font-medium text-ac hover:bg-hover"
                >
                  <Download size={13} /> Скачать
                </DownloadLink>
              </div>
            </div>
          ) : server ? (
            <iframe
              title="live preview"
              sandbox="allow-scripts allow-forms allow-modals allow-same-origin"
              src={server.url}
              className="h-full w-full border-0 bg-white"
            />
          ) : isHtml && file ? (
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

        {tab === "tree" && <ProjectTree project={project} />}
      </div>
    </aside>
  );
}

type TreeItem = { path: string; type: "dir" | "file"; name: string };

/** Project file-tree tab: fetches the flat tree for the active project root and
 *  renders it as a collapsible tree. Clicking a file reads and previews it. */
function ProjectTree({ project }: { project: string }) {
  const [items, setItems] = useState<TreeItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [sel, setSel] = useState<{ path: string; content: string } | null>(null);
  const [selLoading, setSelLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setSel(null);
    getAdvancedProjectTree({ root: project, maxDepth: 6, maxItems: 1500 })
      .then((res) => {
        if (!alive) return;
        if (res?.ok === false) { setError(String(res.error || "Не удалось прочитать дерево")); setItems([]); return; }
        const raw = Array.isArray(res?.items) ? (res.items as TreeItem[]) : [];
        setItems(raw);
      })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "Ошибка"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [project]);

  function openFile(path: string) {
    setSelLoading(true);
    readAdvancedProjectFile(path, 200000, project)
      .then((res) => {
        if (res?.ok === false) { setSel({ path, content: `// ${res.error || "не удалось прочитать файл"}` }); return; }
        setSel({ path, content: String(res?.content ?? "") });
      })
      .catch((e) => setSel({ path, content: `// ${e instanceof Error ? e.message : "ошибка"}` }))
      .finally(() => setSelLoading(false));
  }

  if (loading) return <div className="flex items-center gap-2 px-4 py-3 text-[12px] text-mut"><Loader2 size={13} className="animate-spin" /> Читаю дерево…</div>;
  if (error) return <Empty>{error}</Empty>;
  if (!items.length) return <Empty>Папка пуста или проект не выбран.</Empty>;

  // A path is visible when every ancestor dir is expanded.
  const visible = items.filter((it) => {
    const parts = it.path.split("/");
    for (let i = 1; i < parts.length; i++) {
      if (!open[parts.slice(0, i).join("/")]) return false;
    }
    return true;
  });

  if (sel) {
    const isMd = /\.(md|markdown)$/i.test(sel.path);
    return (
      <div className="flex h-full min-h-0 flex-col">
        <button
          type="button"
          onClick={() => setSel(null)}
          className="flex items-center gap-1.5 border-b border-line px-3 py-2 text-left text-[11.5px] text-t2 hover:bg-hover hover:text-tx"
        >
          <ChevronRight size={13} className="rotate-180" />
          <span className="truncate font-mono">{sel.path}</span>
        </button>
        <div className="min-h-0 flex-1 overflow-auto">
          {selLoading ? (
            <div className="flex items-center gap-2 px-4 py-3 text-[12px] text-mut"><Loader2 size={13} className="animate-spin" /> Читаю…</div>
          ) : isMd ? (
            <div className="px-4 py-3 text-[13px]"><MarkdownRenderer content={sel.content} /></div>
          ) : (
            <Pre>{sel.content}</Pre>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="py-1.5">
      {visible.map((it) => {
        const depth = it.path.split("/").length - 1;
        const isDir = it.type === "dir";
        return (
          <button
            key={it.path}
            type="button"
            onClick={() => (isDir ? setOpen((o) => ({ ...o, [it.path]: !o[it.path] })) : openFile(it.path))}
            className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[12px] text-t2 hover:bg-hover hover:text-tx"
            style={{ paddingLeft: 8 + depth * 14 }}
            title={it.path}
          >
            {isDir ? (
              <ChevronRight size={13} className={cn("shrink-0 text-mut transition-transform", open[it.path] && "rotate-90")} />
            ) : (
              <span className="w-[13px] shrink-0" />
            )}
            {isDir ? <Folder size={13} className="shrink-0 text-ac" /> : <FileText size={13} className="shrink-0 text-mut" />}
            <span className="truncate">{it.name}</span>
          </button>
        );
      })}
    </div>
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
