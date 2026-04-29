from __future__ import annotations

import re
from typing import Any


PROVENANCE_QUESTION_RE = re.compile(
    r"(?iu)("
    r"откуда\s+ты\s+зна(ешь|ёшь)|откуда\s+информац|откуда\s+данные|"
    r"где\s+ты\s+это\s+нашла|покажи\s+источники|дай\s+источники|"
    r"дай\s+ссылки|покажи\s+ссылки|источники\??|sources?\??|where\s+did\s+you\s+get|"
    r"show\s+sources|give\s+sources|give\s+links"
    r")"
)

PERSONAL_NAME_QUESTION_RE = re.compile(
    r"(?iu)^\s*(?:как\s+меня\s+зовут|ты\s+знаешь\s+как\s+меня\s+зовут|what\s+is\s+my\s+name|do\s+you\s+know\s+my\s+name)\s*\??\s*$"
)
FIRST_PERSON_NAME_RE = re.compile(r"(?iu)\bменя\s+зовут\s+([^.!?\n]+)")

RAW_MARKER_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?\[(?:fact|memory|source)\]\s*")
RAG_MARKER_LINE_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:rag(?:\s+alpha\s+memory)?|rag memory)\s*$")
TECHNICAL_HEADER_RE = re.compile(
    r"(?im)^\s*(Relevant user memory|Internal reference facts|Internal user facts|Context notes|Freshness status|Temporal mode)\s*:?\s*$"
)
TECHNICAL_TOKEN_RE = re.compile(
    r"(?iu)\b("
    r"relevant user memory|internal reference facts|internal user facts|"
    r"context notes|memory context|rag memory|from rag|freshness status|temporal mode"
    r")\b"
)

SOURCE_LINE_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:источник(?:и)?|sources?|ссылки)\s*:.*$")
INLINE_SOURCE_CITATION_RE = re.compile(r"\s*\((https?://[^)\s]+)\)")
BARE_URL_RE = re.compile(r"https?://[^\s)]+")
MARKDOWN_SOURCE_LINK_RE = re.compile(r"(?iu)(источник(?:и)?|sources?)\s*:\s*(?:\[[^\]]+\]\(https?://[^)]+\)|https?://\S+)")
WHITESPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:])")
MULTISPACE_RE = re.compile(r"[ \t]{2,}")

SOURCE_LEAD_REPLACEMENTS = (
    (
        re.compile(
            r"(?iu)\b(?:из моей памяти|в моей памяти(?: находится| есть)?|по данным из памяти|"
            r"как я получила эту информацию|я знаю это из памяти|я это знаю из памяти|"
            r"из rag|from rag|rag memory)[^:]{0,120}:\s*[\"«“]?"
        ),
        "",
    ),
    (re.compile(r"(?iu)\bя помню,?\s+что\s+"), ""),
    (re.compile(r"(?iu)\bиз памяти[,:\s-]*"), ""),
    (re.compile(r"(?iu)\bfrom memory[,:\s-]*"), ""),
)

UNWANTED_SOURCE_SENTENCE_RE = re.compile(
    r"(?iu)\b("
    r"из моей памяти|в моей памяти|как я получила эту информацию|"
    r"это подтверждено несколькими источниками|включая данные, которые мы сейчас рассматриваем|"
    r"данные, которые мы сейчас рассматриваем|по актуальным данным|я проверила это по актуальным данным|"
    r"relevant user memory|internal reference facts|internal user facts|"
    r"context notes|memory context|rag memory|from rag|freshness status|temporal mode"
    r")\b"
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def is_provenance_question(user_input: str) -> bool:
    return bool(PROVENANCE_QUESTION_RE.search(user_input or ""))


def _normalize_whitespace(text: str) -> str:
    text = WHITESPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = MULTISPACE_RE.sub(" ", text)
    return text.strip()


def _strip_raw_markers(text: str) -> str:
    cleaned = RAW_MARKER_RE.sub("", text or "")
    cleaned = RAG_MARKER_LINE_RE.sub("", cleaned)
    cleaned = TECHNICAL_HEADER_RE.sub("", cleaned)
    cleaned = TECHNICAL_TOKEN_RE.sub("", cleaned)
    cleaned = cleaned.replace("[fact]", "").replace("[FACT]", "")
    return _normalize_whitespace(cleaned)


def _rewrite_natural_provenance(text: str) -> str:
    rewritten = text
    rewritten = re.sub(r"(?iu)\bиз моей памяти\b", "Ты раньше просил это запомнить", rewritten)
    rewritten = re.sub(r"(?iu)\bв моей памяти(?: находится| есть)?\b", "Я это помню", rewritten)
    rewritten = re.sub(r"(?iu)\bкак я получила эту информацию\b", "Ты раньше просил это запомнить", rewritten)
    rewritten = re.sub(r"(?iu)\bя помню,?\s+что\s+", "", rewritten)
    rewritten = re.sub(r"(?iu)\bэто подтверждено несколькими источниками\b", "Я это проверила", rewritten)
    rewritten = MARKDOWN_SOURCE_LINK_RE.sub(r"\1: проверенные источники", rewritten)
    rewritten = INLINE_SOURCE_CITATION_RE.sub("", rewritten)
    return _normalize_whitespace(rewritten)


def _strip_technical_source_phrases(text: str) -> str:
    cleaned = text
    for pattern, replacement in SOURCE_LEAD_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)

    cleaned = SOURCE_LINE_RE.sub("", cleaned)
    cleaned = MARKDOWN_SOURCE_LINK_RE.sub("", cleaned)
    cleaned = INLINE_SOURCE_CITATION_RE.sub("", cleaned)
    cleaned = BARE_URL_RE.sub("", cleaned)

    sentences = SENTENCE_SPLIT_RE.split(cleaned)
    kept: list[str] = []
    for sentence in sentences:
        normalized = sentence.strip()
        if not normalized:
            continue
        if UNWANTED_SOURCE_SENTENCE_RE.search(normalized):
            continue
        kept.append(normalized)

    if kept:
        cleaned = " ".join(kept)
    return _normalize_whitespace(cleaned)


def _rewrite_direct_personal_facts(user_input: str, text: str) -> str:
    if not PERSONAL_NAME_QUESTION_RE.search(user_input or ""):
        return text

    match = FIRST_PERSON_NAME_RE.search(text or "")
    if not match:
        return text

    name = match.group(1).strip().strip('"«»“”')
    name = re.sub(r"\s+", " ", name)
    if not name:
        return text
    return f"Тебя зовут {name}."


def guard_provenance_response(user_input: str, answer_text: str) -> dict[str, Any]:
    original = (answer_text or "").strip()
    provenance_question = is_provenance_question(user_input)
    if not original:
        return {
            "text": original,
            "changed": False,
            "reason": None,
            "provenance_question": provenance_question,
        }

    cleaned = _strip_raw_markers(original)
    if provenance_question:
        rewritten = _rewrite_natural_provenance(cleaned)
        reason = "natural_provenance_reply"
    else:
        rewritten = _strip_technical_source_phrases(cleaned)
        rewritten = _rewrite_direct_personal_facts(user_input, rewritten)
        reason = "internal_source_hidden"

    if not rewritten:
        rewritten = cleaned or original

    return {
        "text": rewritten,
        "changed": rewritten != original,
        "reason": reason if rewritten != original else None,
        "provenance_question": provenance_question,
    }
