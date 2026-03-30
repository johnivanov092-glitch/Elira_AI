/**
 * MarkdownRenderer.jsx — рендерер Markdown.
 *
 * Оптимизации:
 *   • React.memo — не пере-рендерится если content не изменился
 *   • Regex-паттерны вынесены на уровень модуля (не создаются каждый рендер)
 *   • CopyButton и CodeBlock мемоизированы
 */
import React, { useState, useCallback, useMemo } from "react";
import { buildApiUrl, request } from "../api/client";
import { isLocalApiAssetUrl } from "../api/ide";

// ─── Утилиты (создаются один раз) ──────────────────────────────
const extractFilename = (url) => { const p = url.split("/"); const l = p[p.length - 1]; return l && l.includes(".") ? decodeURIComponent(l) : null; };
const isFilename = (s) => /\.\w{1,5}$/.test(s);

function doDownload(url, label) {
  const full = buildApiUrl(url);
  const fname = isFilename(label) ? label : extractFilename(url) || label || "download";
  request(full, { responseType: "blob" })
    .then(blob => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { a.remove(); URL.revokeObjectURL(a.href); }, 200);
    })
    .catch(() => {
      const a = document.createElement("a");
      a.href = full; a.download = fname; a.target = "_self";
      document.body.appendChild(a);
      a.click();
      setTimeout(() => a.remove(), 200);
    });
}

// ─── Inline regex patterns (создаются один раз на уровне модуля) ───
const INLINE_PATTERNS = [
  { re: /`([^`]+)`/, render: (m, k) => <code key={k} className="md-inline-code">{m[1]}</code> },
  { re: /\*\*(.+?)\*\*/, render: (m, k) => <strong key={k}>{m[1]}</strong> },
  { re: /\*(.+?)\*/, render: (m, k) => <em key={k}>{m[1]}</em> },
  { re: /!\[([^\]]*)\]\(([^)]+)\)/, render: (m, k) => {
    const src = buildApiUrl(m[2]);
    return <img key={k} src={src} alt={m[1]} className="md-image" loading="lazy" />;
  }},
  { re: /\[([^\]]+)\]\(([^)]+)\)/, render: (m, k) => {
    const url = m[2]; const label = m[1];
    if (isLocalApiAssetUrl(url)) {
      const displayName = isFilename(label) ? label : (extractFilename(url) || label);
      return <button key={k} className="md-link md-download-btn" onClick={() => doDownload(url, label)}>📥 {displayName}</button>;
    }
    return <a key={k} href={url} target="_blank" rel="noopener noreferrer" className="md-link">{label}</a>;
  }},
];

const OUTER_FENCE_RE = /^```(?:markdown|text|md|)\s*\n([\s\S]*?)\n?```\s*$/;
const THINK_TAG_RE = /<think>[\s\S]*?<\/think>/g;
const HR_RE = /^[-*_]{3,}\s*$/;
const HEADING_RE = /^(#{1,4})\s+(.+)/;
const UL_RE = /^\s*[-*+]\s/;
const OL_RE = /^\s*\d+[.)]\s/;
const CODE_FENCE_SPLIT_RE = /(```[\s\S]*?```)/g;
const DOUBLE_NEWLINE_RE = /\n{2,}/;

function normalizeInlineEnumerations(block) {
  if (!block || DOUBLE_NEWLINE_RE.test(block)) return block;
  const markers = block.match(/\d+[.)]\s/g) || [];
  if (markers.length < 2) return block;

  let normalized = block;
  normalized = normalized.replace(/:\s+(?=\d+[.)]\s)/g, ":\n");
  normalized = normalized.replace(/([.!?])\s+(?=\d+[.)]\s)/g, "$1\n");
  normalized = normalized.replace(/\s+(?=\d+[.)]\s)/g, "\n");
  return normalized;
}

function normalizeStructuredMarkdown(text) {
  const parts = String(text || "").split(CODE_FENCE_SPLIT_RE);
  return parts.map((part, index) => {
    if (index % 2 === 1) return part;
    return part
      .split("\n\n")
      .map(normalizeInlineEnumerations)
      .join("\n\n");
  }).join("");
}

// ─── Компоненты (мемоизированы) ─────────────────────────────────
const CopyButton = React.memo(function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);
  return <button className="md-copy-btn" onClick={handleCopy} title="Копировать">{copied ? "✓" : "⧉"}</button>;
});

const CodeBlock = React.memo(function CodeBlock({ language, code }) {
  return (
    <div className="md-code-block">
      <div className="md-code-header">
        <span className="md-code-lang">{language || "code"}</span>
        <CopyButton text={code} />
      </div>
      <pre className="md-code-pre"><code>{code}</code></pre>
    </div>
  );
});

// ─── Inline parser ──────────────────────────────────────────────
function parseInline(text, keyPrefix = "il") {
  if (!text) return [text];
  const parts = [];
  let remaining = text;
  let idx = 0;
  while (remaining.length > 0) {
    let earliest = null, earliestIndex = Infinity, matchedPattern = null;
    for (const pat of INLINE_PATTERNS) {
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

function stripOuterCodeFence(text) {
  const trimmed = text.trim();
  const match = trimmed.match(OUTER_FENCE_RE);
  if (match) return match[1];
  return trimmed.replace(THINK_TAG_RE, "").trim();
}

// ─── Главный компонент (React.memo) ────────────────────────────
function MarkdownRendererInner({ content }) {
  if (!content) return null;

  const text = normalizeStructuredMarkdown(stripOuterCodeFence(String(content)));
  if (!text) return null;

  const elements = [];
  let i = 0;
  const lines = text.split("\n");
  let lineIdx = 0;

  while (lineIdx < lines.length) {
    const line = lines[lineIdx];

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
      i++; continue;
    }

    if (HR_RE.test(line.trim())) {
      elements.push(<hr key={`hr-${i}`} className="md-hr" />);
      i++; lineIdx++; continue;
    }

    const hm = line.match(HEADING_RE);
    if (hm) {
      const Tag = `h${hm[1].length}`;
      elements.push(<Tag key={`h-${i}`} className={`md-heading md-h${hm[1].length}`}>{parseInline(hm[2], `h${i}`)}</Tag>);
      i++; lineIdx++; continue;
    }

    if (UL_RE.test(line)) {
      const items = [];
      while (lineIdx < lines.length && UL_RE.test(lines[lineIdx])) {
        items.push(<li key={`li-${i}-${items.length}`}>{parseInline(lines[lineIdx].replace(/^\s*[-*+]\s/, ""), `li${i}${items.length}`)}</li>);
        lineIdx++;
      }
      elements.push(<ul key={`ul-${i}`} className="md-list">{items}</ul>);
      i++; continue;
    }

    if (OL_RE.test(line)) {
      const items = [];
      while (lineIdx < lines.length && OL_RE.test(lines[lineIdx])) {
        items.push(<li key={`oli-${i}-${items.length}`}>{parseInline(lines[lineIdx].replace(/^\s*\d+[.)]\s/, ""), `oli${i}${items.length}`)}</li>);
        lineIdx++;
      }
      elements.push(<ol key={`ol-${i}`} className="md-list md-ol">{items}</ol>);
      i++; continue;
    }

    if (!line.trim()) { lineIdx++; continue; }

    const paraLines = [];
    while (
      lineIdx < lines.length && lines[lineIdx].trim() &&
      !lines[lineIdx].trimStart().startsWith("```") &&
      !lines[lineIdx].match(HEADING_RE) &&
      !UL_RE.test(lines[lineIdx]) &&
      !OL_RE.test(lines[lineIdx]) &&
      !HR_RE.test(lines[lineIdx].trim())
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

export default React.memo(MarkdownRendererInner);
