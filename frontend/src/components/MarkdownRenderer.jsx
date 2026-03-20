/**
 * MarkdownRenderer.jsx — рендерер Markdown.
 *
 * Фикс: qwen3 оборачивает ответ в ```markdown ... ``` —
 * теперь это автоматически снимается перед рендерингом.
 */
import { useState, useCallback } from "react";

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);
  return (
    <button className="md-copy-btn" onClick={handleCopy} title="Копировать">
      {copied ? "✓" : "⧉"}
    </button>
  );
}

function CodeBlock({ language, code }) {
  return (
    <div className="md-code-block">
      <div className="md-code-header">
        <span className="md-code-lang">{language || "code"}</span>
        <CopyButton text={code} />
      </div>
      <pre className="md-code-pre"><code>{code}</code></pre>
    </div>
  );
}

function parseInline(text, keyPrefix = "il") {
  if (!text) return [text];
  const parts = [];
  let remaining = text;
  let idx = 0;
  const API = `http://${window.location.hostname}:8000`;
  const isLocalDL = (url) => url.includes("/api/skills/download/") || url.includes("/api/skills/view/") || url.includes("/api/extra/");

  // Извлекает имя файла из URL: /api/skills/download/jarvis_123.docx → jarvis_123.docx
  const extractFilename = (url) => {
    const parts = url.split("/");
    const last = parts[parts.length - 1];
    return last && last.includes(".") ? decodeURIComponent(last) : null;
  };

  // Проверяет что строка похожа на имя файла (содержит расширение)
  const isFilename = (s) => /\.\w{1,5}$/.test(s);

  const doDownload = (url, label) => {
    const full = url.startsWith("http") ? url : `${API}${url}`;
    // Имя файла: если label — настоящее имя файла, используем его; иначе извлекаем из URL
    const fname = isFilename(label) ? label : extractFilename(url) || label || "download";

    // Метод 1: fetch → blob (работает в браузере)
    fetch(full, { mode: "cors" })
      .then(r => { if (!r.ok) throw new Error(r.status); return r.blob(); })
      .then(blob => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { a.remove(); URL.revokeObjectURL(a.href); }, 200);
      })
      .catch(() => {
        // Метод 2: прямая ссылка (fallback для Tauri)
        const a = document.createElement("a");
        a.href = full;
        a.download = fname;
        a.target = "_self";
        document.body.appendChild(a);
        a.click();
        setTimeout(() => a.remove(), 200);
      });
  };

  const patterns = [
    { re: /`([^`]+)`/, render: (m, k) => <code key={k} className="md-inline-code">{m[1]}</code> },
    { re: /\*\*(.+?)\*\*/, render: (m, k) => <strong key={k}>{m[1]}</strong> },
    { re: /\*(.+?)\*/, render: (m, k) => <em key={k}>{m[1]}</em> },
    { re: /!\[([^\]]*)\]\(([^)]+)\)/, render: (m, k) => {
      const src = m[2].startsWith("http") ? m[2] : `${API}${m[2]}`;
      return <img key={k} src={src} alt={m[1]} style={{maxWidth:"100%",borderRadius:8,marginTop:8,marginBottom:8}} loading="lazy" />;
    }},
    { re: /\[([^\]]+)\]\(([^)]+)\)/, render: (m, k) => {
      const url = m[2]; const label = m[1];
      if (isLocalDL(url)) {
        const displayName = isFilename(label) ? label : (extractFilename(url) || label);
        return <button key={k} className="md-link" onClick={() => doDownload(url, label)} style={{background:"rgba(100,200,255,0.08)",border:"1px solid rgba(100,200,255,0.2)",color:"#7dd3fc",cursor:"pointer",borderRadius:6,padding:"3px 10px",font:"inherit",fontSize:"0.9em"}}>📥 {displayName}</button>;
      }
      return <a key={k} href={url} target="_blank" rel="noopener noreferrer" className="md-link">{label}</a>;
    }},
  ];
  while (remaining.length > 0) {
    let earliest = null, earliestIndex = Infinity, matchedPattern = null;
    for (const pat of patterns) {
      const match = remaining.match(pat.re);
      if (match && match.index < earliestIndex) { earliest = match; earliestIndex = match.index; matchedPattern = pat; }
    }
    if (!earliest || !matchedPattern) { parts.push(remaining); break; }
    if (earliestIndex > 0) parts.push(remaining.slice(0, earliestIndex));
    parts.push(matchedPattern.render(earliest, `${keyPrefix}-${idx}`));
    idx++;
    remaining = remaining.slice(earliestIndex + earliest[0].length);
  }
  return parts;
}

/**
 * Снимает внешнюю обёртку ```markdown ... ``` или ```text ... ```
 * которую qwen3 и другие модели часто добавляют.
 */
function stripOuterCodeFence(text) {
  const trimmed = text.trim();
  // Проверяем: начинается с ```markdown или ```text и заканчивается на ```
  const outerFenceRe = /^```(?:markdown|text|md|)\s*\n([\s\S]*?)\n?```\s*$/;
  const match = trimmed.match(outerFenceRe);
  if (match) return match[1];

  // Также снимаем <think>...</think> теги от deepseek-r1
  return trimmed.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
}

export default function MarkdownRenderer({ content }) {
  if (!content) return null;

  // Снимаем внешнюю markdown-обёртку
  const text = stripOuterCodeFence(String(content));
  if (!text) return null;

  const elements = [];
  let i = 0;
  const lines = text.split("\n");
  let lineIdx = 0;

  while (lineIdx < lines.length) {
    const line = lines[lineIdx];

    // Блоки кода (внутренние — настоящий код)
    if (line.trimStart().startsWith("```")) {
      const lang = line.trimStart().slice(3).trim();
      const codeLines = [];
      lineIdx++;
      while (lineIdx < lines.length && !lines[lineIdx].trimStart().startsWith("```")) {
        codeLines.push(lines[lineIdx]);
        lineIdx++;
      }
      lineIdx++;
      elements.push(<CodeBlock key={`cb-${i}`} language={lang} code={codeLines.join("\n")} />);
      i++;
      continue;
    }

    // Горизонтальная линия
    if (/^[-*_]{3,}\s*$/.test(line.trim())) {
      elements.push(<hr key={`hr-${i}`} className="md-hr" />);
      i++; lineIdx++;
      continue;
    }

    // Заголовки
    const hm = line.match(/^(#{1,4})\s+(.+)/);
    if (hm) {
      const Tag = `h${hm[1].length}`;
      elements.push(<Tag key={`h-${i}`} className={`md-heading md-h${hm[1].length}`}>{parseInline(hm[2], `h${i}`)}</Tag>);
      i++; lineIdx++;
      continue;
    }

    // Маркированные списки
    if (/^\s*[-*+]\s/.test(line)) {
      const items = [];
      while (lineIdx < lines.length && /^\s*[-*+]\s/.test(lines[lineIdx])) {
        items.push(<li key={`li-${i}-${items.length}`}>{parseInline(lines[lineIdx].replace(/^\s*[-*+]\s/, ""), `li${i}${items.length}`)}</li>);
        lineIdx++;
      }
      elements.push(<ul key={`ul-${i}`} className="md-list">{items}</ul>);
      i++;
      continue;
    }

    // Нумерованные списки
    if (/^\s*\d+[.)]\s/.test(line)) {
      const items = [];
      while (lineIdx < lines.length && /^\s*\d+[.)]\s/.test(lines[lineIdx])) {
        items.push(<li key={`oli-${i}-${items.length}`}>{parseInline(lines[lineIdx].replace(/^\s*\d+[.)]\s/, ""), `oli${i}${items.length}`)}</li>);
        lineIdx++;
      }
      elements.push(<ol key={`ol-${i}`} className="md-list md-ol">{items}</ol>);
      i++;
      continue;
    }

    // Пустая строка
    if (!line.trim()) { lineIdx++; continue; }

    // Параграф
    const paraLines = [];
    while (
      lineIdx < lines.length && lines[lineIdx].trim() &&
      !lines[lineIdx].trimStart().startsWith("```") &&
      !lines[lineIdx].match(/^#{1,4}\s/) &&
      !/^\s*[-*+]\s/.test(lines[lineIdx]) &&
      !/^\s*\d+[.)]\s/.test(lines[lineIdx]) &&
      !/^[-*_]{3,}\s*$/.test(lines[lineIdx].trim())
    ) {
      paraLines.push(lines[lineIdx]);
      lineIdx++;
    }
    if (paraLines.length) {
      elements.push(<p key={`p-${i}`} className="md-paragraph">{parseInline(paraLines.join("\n"), `p${i}`)}</p>);
      i++;
    }
  }

  return <div className="md-root">{elements}</div>;
}
