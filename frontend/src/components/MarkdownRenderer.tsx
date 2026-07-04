/**
 * MarkdownRenderer.jsx — рендерер Markdown.
 *
 * Оптимизации:
 *   • React.memo — не пере-рендерится если content не изменился
 *   • Regex-паттерны вынесены на уровень модуля (не создаются каждый рендер)
 *   • CopyButton и CodeBlock мемоизированы
 */
import React, { useState, useCallback, type ReactNode } from "react";
import { buildApiUrl, request } from "../api/client";
import { isLocalApiAssetUrl } from "../api/apiUtils";

type InlinePattern = {
  re: RegExp;
  render: (match: RegExpMatchArray, key: string) => ReactNode;
};

type CopyButtonProps = {
  text: string;
};

type CodeBlockProps = {
  code: string;
  language?: string;
};

type MarkdownRendererProps = {
  content?: unknown;
};

// ─── Утилиты (создаются один раз) ──────────────────────────────
const extractFilename = (url: string): string | null => { const p = url.split("/"); const l = p[p.length - 1]; return l && l.includes(".") ? decodeURIComponent(l) : null; };
const isFilename = (s: string): boolean => /\.\w{1,5}$/.test(s);

function doDownload(url: string, label: string) {
  const full = buildApiUrl(url);
  const fname = isFilename(label) ? label : extractFilename(url) || label || "download";
  request<Blob>(full, { responseType: "blob" })
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
const INLINE_PATTERNS: InlinePattern[] = [
  { re: /`([^`]+)`/, render: (m, k) => <code key={k} className="md-inline-code">{m[1]}</code> },
  { re: /\*\*(.+?)\*\*/, render: (m, k) => <strong key={k}>{m[1]}</strong> },
  { re: /~~(.+?)~~/, render: (m, k) => <del key={k} className="md-del">{m[1]}</del> },
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
  // Bare URL (not already inside []() — the link pattern above matches earlier at
  // its "[" so it wins there). Trailing punctuation is left out of the link.
  { re: /(https?:\/\/[^\s<>()\]}"']*[^\s<>()\]}"'.,;:!?])/, render: (m, k) =>
    <a key={k} href={m[1]} target="_blank" rel="noopener noreferrer" className="md-link">{m[1]}</a> },
];

const OUTER_FENCE_RE = /^```(?:markdown|text|md|)\s*\n([\s\S]*?)\n?```\s*$/;
const THINK_TAG_RE = /<think>[\s\S]*?<\/think>/g;
const HR_RE = /^[-*_]{3,}\s*$/;
const HEADING_RE = /^(#{1,4})\s+(.+)/;
const UL_RE = /^\s*[-*+]\s/;
const OL_RE = /^\s*\d+[.)]\s/;
const CODE_FENCE_SPLIT_RE = /(```[\s\S]*?```)/g;
const DOUBLE_NEWLINE_RE = /\n{2,}/;
const BOLD_SECTION_RE = /\*\*[^*\n]{1,80}?:\s*\*\*/g;
const ITALIC_SECTION_RE = /(?<![*\w])\*[^*\n]{1,80}?:\s*\*(?![*\w])/g;
// A block that already contains a Markdown list line (a "-/*/+"" bullet or "N."
// at a line start) is structured content. The flowing-paragraph splitters below
// must NOT touch it: their "\n\n before **Heading:**" rewrite would eat the
// bullet's trailing space and orphan a lone "*" on its own line.
const LIST_LINE_IN_BLOCK_RE = /(?:^|\n)[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+/;
// GFM tables: a "| … |" row, then a "|---|---|" separator, then data rows. The
// custom block renderer had no table branch, so tables fell into the paragraph
// case and collapsed all rows onto one line.
const TABLE_ROW_RE = /^\s*\|.*\|\s*$/;
const TABLE_SEP_RE = /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/;
const splitTableRow = (line: string): string[] =>
  line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

function normalizeInlineEnumerations(block: string): string {
  if (!block || DOUBLE_NEWLINE_RE.test(block)) return block;
  const markers = block.match(/\d+[.)]\s/g) || [];
  if (markers.length < 2) return block;

  let normalized = block;
  normalized = normalized.replace(/:\s+(?=\d+[.)]\s)/g, ":\n");
  normalized = normalized.replace(/([.!?])\s+(?=\d+[.)]\s)/g, "$1\n");
  normalized = normalized.replace(/\s+(?=\d+[.)]\s)/g, "\n");
  return normalized;
}

function normalizeInlineBoldSections(block: string): string {
  // Small LLMs (gemma:2b/3b, qwen:4b, ...) often output a single flowing
  // paragraph with multiple '**Heading:**' markers inline. Renders as a
  // wall of text. When we detect ≥2 such markers inside one paragraph,
  // split into separate paragraphs at each marker.
  if (!block || DOUBLE_NEWLINE_RE.test(block)) return block;
  if (LIST_LINE_IN_BLOCK_RE.test(block)) return block;
  const matches = block.match(BOLD_SECTION_RE) || [];
  if (matches.length < 2) return block;
  // Insert '\n\n' before any bold-section marker that has content before it.
  return block.replace(/\s+(\*\*[^*\n]{1,80}?:\s*\*\*)/g, "\n\n$1");
}

function normalizeInlineItalicSections(block: string): string {
  // After bold-section split: if a paragraph still contains ≥2 inline
  // italic-section markers ('*Subheading:*'), break them onto separate
  // lines so they read as sub-bullets, not run-on prose.
  if (!block) return block;
  if (LIST_LINE_IN_BLOCK_RE.test(block)) return block;
  const matches = block.match(ITALIC_SECTION_RE) || [];
  if (matches.length < 2) return block;
  return block.replace(/([.!?])\s+(\*[^*\n]{1,80}?:\s*\*)/g, "$1\n$2")
              .replace(/(\S)\s+(\*[^*\n]{1,80}?:\s*\*)/g, "$1\n$2");
}

function normalizeStructuredMarkdown(text: unknown): string {
  const parts = String(text || "").split(CODE_FENCE_SPLIT_RE);
  return parts.map((part, index) => {
    if (index % 2 === 1) return part;
    // First pass: split flowing paragraphs that have inline bold sections.
    // Result may contain new '\n\n' boundaries that the outer split below
    // then picks up.
    const pre = part
      .split("\n\n")
      .map(normalizeInlineBoldSections)
      .join("\n\n");
    return pre
      .split("\n\n")
      .map(normalizeInlineItalicSections)
      .map(normalizeInlineEnumerations)
      .join("\n\n");
  }).join("");
}

// ─── Компоненты (мемоизированы) ─────────────────────────────────
const CopyButton = React.memo(function CopyButton({ text }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);
  return <button className="md-copy-btn" onClick={handleCopy} title="Копировать">{copied ? "✓" : "⧉"}</button>;
});

const CodeBlock = React.memo(function CodeBlock({ language, code }: CodeBlockProps) {
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
function parseInline(text: string, keyPrefix = "il"): ReactNode[] {
  if (!text) return [text];
  const parts: ReactNode[] = [];
  let remaining = text;
  let idx = 0;
  while (remaining.length > 0) {
    let earliest: RegExpMatchArray | null = null;
    let earliestIndex = Infinity;
    let matchedPattern: InlinePattern | null = null;
    for (const pat of INLINE_PATTERNS) {
      const match = remaining.match(pat.re);
      const matchIndex = match?.index ?? Infinity;
      if (match && matchIndex < earliestIndex) { earliest = match; earliestIndex = matchIndex; matchedPattern = pat; }
    }
    if (!earliest || !matchedPattern) { parts.push(remaining); break; }
    if (earliestIndex > 0) parts.push(remaining.slice(0, earliestIndex));
    parts.push(matchedPattern.render(earliest, `${keyPrefix}-${idx}`));
    idx++;
    remaining = remaining.slice(earliestIndex + earliest[0].length);
  }
  return parts;
}

function stripOuterCodeFence(text: string): string {
  const trimmed = text.trim();
  const match = trimmed.match(OUTER_FENCE_RE);
  if (match) return match[1];
  return trimmed.replace(THINK_TAG_RE, "").trim();
}

const TASK_RE = /^\[([ xX])\]\s+(.*)$/;
const listIndent = (s: string): number => s.match(/^(\s*)/)?.[1].length ?? 0;
const isListItem = (s: string): boolean => UL_RE.test(s) || OL_RE.test(s);

// Parse a contiguous list from `start`; deeper-indented item lines become a
// nested <ul>/<ol> under the preceding item. GFM task items (- [ ] / - [x])
// render as (disabled) checkboxes. Returns the node + index just past the list.
function parseListBlock(lines: string[], start: number, keyBase: string): { node: ReactNode; next: number } {
  const baseIndent = listIndent(lines[start]);
  const ordered = OL_RE.test(lines[start]);
  const items: ReactNode[] = [];
  let idx = start;
  let n = 0;
  while (idx < lines.length && isListItem(lines[idx]) && listIndent(lines[idx]) === baseIndent) {
    const content = lines[idx].replace(/^\s*(?:[-*+]|\d+[.)])\s/, "");
    idx++;
    let nested: ReactNode = null;
    if (idx < lines.length && isListItem(lines[idx]) && listIndent(lines[idx]) > baseIndent) {
      const sub = parseListBlock(lines, idx, `${keyBase}-${n}`);
      nested = sub.node;
      idx = sub.next;
    }
    const key = `${keyBase}-li-${n}`;
    const task = content.match(TASK_RE);
    if (task) {
      items.push(
        <li key={key} className="md-task">
          <input type="checkbox" checked={task[1].toLowerCase() === "x"} disabled readOnly />{" "}
          {parseInline(task[2], key)}
          {nested}
        </li>,
      );
    } else {
      items.push(<li key={key}>{parseInline(content, key)}{nested}</li>);
    }
    n++;
  }
  const cls = "md-list" + (ordered ? " md-ol" : "");
  const node = ordered
    ? <ol key={keyBase} className={cls}>{items}</ol>
    : <ul key={keyBase} className={cls}>{items}</ul>;
  return { node, next: idx };
}

// Column alignments from a table separator row (":---" left, "---:" right, ":--:" center).
function parseTableAlign(sepLine: string): (("left" | "center" | "right") | null)[] {
  return splitTableRow(sepLine).map((c) => {
    const l = c.startsWith(":");
    const r = c.endsWith(":");
    return l && r ? "center" : r ? "right" : l ? "left" : null;
  });
}

// ─── Главный компонент (React.memo) ────────────────────────────
function MarkdownRendererInner({ content }: MarkdownRendererProps) {
  if (!content) return null;

  const text = normalizeStructuredMarkdown(stripOuterCodeFence(String(content)));
  if (!text) return null;

  const elements: ReactNode[] = [];
  let i = 0;
  const lines = text.split("\n");
  let lineIdx = 0;

  while (lineIdx < lines.length) {
    const line = lines[lineIdx];

    if (line.trimStart().startsWith("```")) {
      const lang = line.trimStart().slice(3).trim();
      const codeLines: string[] = [];
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

    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = [];
      while (lineIdx < lines.length && /^\s*>\s?/.test(lines[lineIdx])) {
        quote.push(lines[lineIdx].replace(/^\s*>\s?/, ""));
        lineIdx++;
      }
      elements.push(
        <blockquote key={`bq-${i}`} className="md-quote">{parseInline(quote.join("\n"), `bq${i}`)}</blockquote>,
      );
      i++; continue;
    }

    const hm = line.match(HEADING_RE);
    if (hm) {
      const Tag = `h${hm[1].length}` as "h1" | "h2" | "h3" | "h4";
      elements.push(<Tag key={`h-${i}`} className={`md-heading md-h${hm[1].length}`}>{parseInline(hm[2], `h${i}`)}</Tag>);
      i++; lineIdx++; continue;
    }

    // Table: a "| … |" row immediately followed by a "|---|---|" separator.
    if (TABLE_ROW_RE.test(line) && lineIdx + 1 < lines.length && TABLE_SEP_RE.test(lines[lineIdx + 1])) {
      const header = splitTableRow(line);
      const ncol = Math.max(1, header.length);
      const aligns = parseTableAlign(lines[lineIdx + 1]);
      lineIdx += 2; // consume the header row + the separator row
      const rows: string[][] = [];
      while (
        lineIdx < lines.length &&
        TABLE_ROW_RE.test(lines[lineIdx]) &&
        !TABLE_SEP_RE.test(lines[lineIdx])
      ) {
        rows.push(splitTableRow(lines[lineIdx]));
        lineIdx++;
      }
      elements.push(
        <div key={`tblw-${i}`} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {header.map((c, ci) => (
                  <th key={ci} style={aligns[ci] ? { textAlign: aligns[ci]! } : undefined}>{parseInline(c, `th-${i}-${ci}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {Array.from({ length: ncol }).map((_, ci) => (
                    <td key={ci} style={aligns[ci] ? { textAlign: aligns[ci]! } : undefined}>{parseInline(r[ci] ?? "", `td-${i}-${ri}-${ci}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      i++; continue;
    }

    if (isListItem(line)) {
      const { node, next } = parseListBlock(lines, lineIdx, `list-${i}`);
      elements.push(node);
      lineIdx = next;
      i++;
      continue;
    }

    if (!line.trim()) { lineIdx++; continue; }

    const paraLines: string[] = [];
    while (
      lineIdx < lines.length && lines[lineIdx].trim() &&
      !lines[lineIdx].trimStart().startsWith("```") &&
      !lines[lineIdx].match(HEADING_RE) &&
      !UL_RE.test(lines[lineIdx]) &&
      !OL_RE.test(lines[lineIdx]) &&
      !HR_RE.test(lines[lineIdx].trim()) &&
      // Stop before a table (header row + "|---|" separator on the next line),
      // even with no blank line above it — otherwise a bold heading like
      // "**192.168.88.1**" directly above the table swallows all its rows as
      // paragraph text and the table never renders.
      !(TABLE_ROW_RE.test(lines[lineIdx]) &&
        lineIdx + 1 < lines.length &&
        TABLE_SEP_RE.test(lines[lineIdx + 1]))
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
