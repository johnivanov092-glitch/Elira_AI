"""Script check for authored prose, excluding source-bound structured names."""
from __future__ import annotations

import re
from typing import Any, Iterable

from app.application.code_agent.answer_contracts import _FENCED_CODE, _INLINE_CODE, _quote_spans


_LIST = re.compile(r"^(\s*(?:[-+*]|\d+[.)])\s+)(.*)$")
_DATE = re.compile(r"^\d{1,2}(?:[-–]\d{1,2})?\s+[А-Яа-яЁёA-Za-z.]+\s+[—–]\s+")
_BOLD_LABEL = re.compile(r"^(\*\*[^*\n]+\*\*|__[^_\n]+__)\s*(?:[—–]|-(?=\s)|:)")
_TABLE_CELL = re.compile(r"^(\s*\|)([^|\n]*)(\|.*)$")
_LINK = re.compile(r"\[[^\]\n]+\]\(<?https?://[^\s<>\)]+>?\)")
_NAME_CHARS = re.compile(r"[A-Za-z0-9\s'’:/&+.\-\_]+")
_NAME_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?(?:[._-][A-Za-z0-9]+)*")
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*")
_VERSION = re.compile(r"v?\d+(?:\.\d+)*(?:[a-z]\d+)?")
_NAME_TOKEN = re.compile(r"[A-Z][A-Za-z0-9]*(?:['’]s)?(?:[._-][A-Za-z0-9]+)*|[0-9]+")
_GLUE = {"of", "the", "and"}


def answer_language_matches(
    answer: str, language: str, presented_sources: Iterable[dict[str, Any]],
) -> bool:
    """Check script predominance, not semantic language or source truth.

    Foreign names are exempt only in Markdown data-label positions and when
    their exact bounded text occurs in a verified, currently presented excerpt.
    Ordinary prose is never exempted merely because it appears in a source.
    """
    if language not in {"ru", "en"}:
        return False
    text = answer
    for start, end, _, _ in reversed(_quote_spans(answer)):
        text = text[:start] + text[end:]
    text = _INLINE_CODE.sub("", _FENCED_CODE.sub("", text))
    text = _LINK.sub("", text)
    text = re.sub(r"\[\[source:[^\]\n]+\]\]|https?://\S+", "", text)

    if language == "ru":
        excerpts = [" ".join(source["quote"].split()) for source in presented_sources
                    if source.get("presented") is True and source.get("quote_verified") is True
                    and source.get("status") == "excerpt" and source.get("tool") in {"web_fetch", "web_query"}
                    and isinstance(source.get("quote"), str)]

        def mask_name(raw: str) -> str:
            name = raw.strip()
            if ((name.startswith("**") and name.endswith("**"))
                    or (name.startswith("__") and name.endswith("__"))):
                name = name[2:-2].strip()
            if not 0 < len(name) <= 200 or _NAME_CHARS.fullmatch(name) is None:
                return raw
            tokens = _NAME_WORD.findall(name)
            if not tokens or re.fullmatch(r"[\s:/&+\-]*", _NAME_WORD.sub("", name)) is None:
                return raw
            identifier = _IDENTIFIER.fullmatch(name) is not None
            versioned_identifier = (_IDENTIFIER.fullmatch(tokens[0]) is not None
                                    and all(_VERSION.fullmatch(token) for token in tokens[1:]))
            title = (any(token[0].isupper() for token in tokens)
                     and all(token in _GLUE or _NAME_TOKEN.fullmatch(token) for token in tokens))
            if not (identifier or versioned_identifier or title):
                return raw
            pattern = r"(?<!\w)" + re.escape(" ".join(name.split())) + r"(?!\w)"
            return " " if any(re.search(pattern, excerpt) for excerpt in excerpts) else raw

        lines = []
        for line in text.splitlines():
            item = _LIST.match(line)
            cell = _TABLE_CELL.match(line)
            if item:
                body = item[2]
                paren = body.find("(")
                bold = _BOLD_LABEL.match(body)
                if bold:
                    body = mask_name(bold[1]) + body[bold.end(1):]
                elif paren > 0:
                    label = body[:paren]
                    date = _DATE.match(label)
                    prefix = label[:date.end()] if date else ""
                    label = label[date.end():] if date else label
                    body = prefix + mask_name(label) + body[paren:]
                line = item[1] + body
            elif cell:
                line = cell[1] + mask_name(cell[2]) + cell[3]
            lines.append(line)
        text = "\n".join(lines)

    script = r"[А-Яа-яЁё]" if language == "ru" else r"[A-Za-z]"
    return len(re.findall(script, text)) > sum(char.isalpha() for char in text) / 2
