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
  const patterns = [
    { re: /`([^`]+)`/, render: (m, k) => <code key={k} className="md-inline-code">{m[1]}</code> },
    { re: /\*\*(.+?)\*\*/, render: (m, k) => <strong key={k}>{m[1]}</strong> },
    { re: /\*(.+?)\*/, render: (m, k) => <em key={k}>{m[1]}</em> },
    { re: /!\[([^\]]*)\]\(([^)]+)\)/, render: (m, k) => <img key={k} src={m[2]} alt={m[1]} style={{maxWidth:"100%",borderRadius:8,marginTop:8,marginBottom:8}} loading="lazy" /> },
    { re: /\[([^\]]+)\]\(([^)]+)\)/, render: (m, k) => <a key={k} href={m[2]} target="_blank" rel="noopener noreferrer" className="md-link">{m[1]}</a> },
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
