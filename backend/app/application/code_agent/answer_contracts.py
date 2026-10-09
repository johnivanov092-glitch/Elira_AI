"""Deterministic checks for narrowly stated answer-format requirements.

This is not a factual judge. Quote limits are read only from the user's request;
citation coverage retains provenance-only meaning.
Unrecognised/ambiguous wording establishes no inferred semantic contract.
"""
from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Any


_FENCED_CODE = re.compile(r"```[^\n]*\n.*?(?:```|\Z)|~~~[^\n]*\n.*?(?:~~~|\Z)", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_PAIRED_QUOTE = re.compile(r'"([^"\n]+)"|«([^»]+)»|“([^”]+)”')
_LIMIT = re.compile(
    r"\b(?:цитат[ауые](?:-предложение)?|quote|quotation)(?:\s+длиной)?\s*(?:[:—-]\s*)?"
    r"(?:до|не\s+более|максимум|up\s+to|at\s+most|no\s+more\s+than)\s+"
    r"([1-9]\d{0,3})\s+(?:слов(?:а|о)?|words?)\b",
    re.IGNORECASE,
)
_LABEL = re.compile(
    r"(?:цитата|quote|quotation)\s*"
    r"(?:\((\d+)\s+(?:слов(?:а|о)?|words?)\))?\s*:\s*$",
    re.IGNORECASE,
)
_BLOCK = re.compile(r"(?:^[ \t]{0,3}>[^\n]*(?:\n|$))+", re.MULTILINE)
_WORD = re.compile(r"\w+(?:['’\-]\w+)*", re.UNICODE)
_NEGATED_DIRECTIVE = re.compile(
    r"\b(?:не\s+(?:дай|давай|приводи|ограничивай|нужна|нужны|нужно|требуется)|"
    r"(?:don't|do\s+not)\s+(?:give|include|provide|limit))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuoteWordLimitViolation:
    quote_index: int
    word_count: int
    max_words: int
    claimed_words: int | None = None


def infer_quote_word_limit(user_message: str) -> int | None:
    """Recognise e.g. ``цитату до 15 слов`` / ``quote at most 15 words``.

    Quoted examples and code do not establish a contract. Different limits
    in one request are ambiguous and are deliberately left to the model.
    """
    text = _INLINE_CODE.sub("", _FENCED_CODE.sub("", user_message or ""))
    text = _BLOCK.sub("", text)
    text = _PAIRED_QUOTE.sub("", text).replace("**", "").replace("__", "")
    limits = set()
    for match in _LIMIT.finditer(text):
        clause_prefix = re.split(r"[.!?;\n,]", text[:match.start()])[-1]
        if not _NEGATED_DIRECTIVE.search(clause_prefix):
            limits.add(int(match.group(1)))
    return next(iter(limits)) if len(limits) == 1 else None


def _label_match(prefix: str) -> re.Match[str] | None:
    # Spaces preserve digit offsets in the original Markdown for normalization.
    return _LABEL.search(prefix.replace("**", "  ").replace("__", "  ").rstrip())


def _claimed_words(prefix: str) -> int | None:
    label = _label_match(prefix)
    return int(label.group(1)) if label and label.group(1) else None


def _quote_spans(answer_text: str) -> list[tuple[int, int, str, int | None]]:
    # Preserve offsets while hiding fenced code, including blockquote examples.
    text = _FENCED_CODE.sub(lambda match: " " * len(match.group()), answer_text)
    quotes = []
    for block in _BLOCK.finditer(text):
        body = re.sub(r"^[ \t]{0,3}>[ \t]?", "", block.group(), flags=re.MULTILINE)
        quotes.append((block.start(), block.end(), body, _claimed_words(text[:block.start()])))
    paired_text = _INLINE_CODE.sub(lambda match: " " * len(match.group()), text)
    for quote in _PAIRED_QUOTE.finditer(paired_text):
        if any(start <= quote.start() < end for start, end, _, _ in quotes):
            continue
        start, end = next(
            quote.span(index) for index, part in enumerate(quote.groups(), 1)
            if part is not None
        )
        body = text[start:end]
        quotes.append((quote.start(), quote.end(), body, _claimed_words(text[:quote.start()])))
    return sorted(quotes)


def _quote_word_count(body: str) -> int:
    body = re.sub(r"\[\[source:[^\]\n]+\]\]", "", body)
    body = re.sub(r"\[([^\]]*)\]\([^\n)]*\)", r"\1", body)
    return len(_WORD.findall(body))


_QUOTE_REQUEST = re.compile(r"цитат|дословн|\bquot|\bverbatim", re.IGNORECASE)
_NAME_MAX_WORDS = 6


def explicit_quote_request(user_message: str) -> bool:
    """Whether the user asked for quotations (then every quoted span must bind)."""
    text = _INLINE_CODE.sub("", _FENCED_CODE.sub("", user_message or ""))
    return bool(_QUOTE_REQUEST.search(text))


def is_name_like_quote(body: str) -> bool:
    """Russian typography puts names and titles in «…»; they are not quotations.

    A short single-line span without sentence-final punctuation is treated as
    a name. Real quotations are sentences or longer fragments.
    """
    body = (body or "").strip()
    return (bool(body) and "\n" not in body and not re.search(r"[.!?…]$", body)
            and _quote_word_count(body) <= _NAME_MAX_WORDS)


def normalize_quote_word_counts(answer_text: str) -> str:
    """Correct only numeric count labels immediately attached to quotations.

    Exact quote words, links and all other formatting remain unchanged. This
    does not shorten overlong quotations or certify their factual support.
    """
    replacements = []
    for start, _, body, claimed in _quote_spans(answer_text):
        if claimed is None:
            continue
        count = _quote_word_count(body)
        if count == claimed:
            continue
        label = _label_match(answer_text[:start])
        if label and label.group(1):
            replacements.append((*label.span(1), str(count)))
    for start, end, count in reversed(replacements):
        answer_text = answer_text[:start] + count + answer_text[end:]
    return answer_text


def quote_word_limit_violations(
    answer_text: str, *, max_words: int | None,
) -> tuple[QuoteWordLimitViolation, ...]:
    """Check delimited quotations; never truncate or rewrite the answer.

    Unicode words separated by punctuation count separately; internal
    apostrophes/hyphens/underscores stay in the word. Citation markers and link
    destinations are metadata, not quoted words. This checks recognised quote
    spans, not whether the answer fulfils every semantic request.
    """
    if max_words is None:
        return ()
    if type(max_words) is not int or max_words < 1:
        raise ValueError("max_words must be a positive integer or None")
    violations = []
    for index, (_, _, body, claimed) in enumerate(_quote_spans(answer_text), 1):
        count = _quote_word_count(body)
        if count > max_words or (claimed is not None and claimed != count):
            violations.append(QuoteWordLimitViolation(index, count, max_words, claimed))
    return tuple(violations)


def quote_word_limit_correction(violations: tuple[QuoteWordLimitViolation, ...]) -> str:
    """A bounded format correction using counted numbers, never source text."""
    if not violations:
        return ""
    facts = "; ".join(
        f"цитата {item.quote_index}: {item.word_count} слов, максимум {item.max_words}"
        + (f", указано {item.claimed_words}" if item.claimed_words is not None else "")
        for item in violations
    )
    return (
        "[Проверка явного ограничения цитаты] " + facts + ". "
        "Исправь только итоговый ответ: выбери из уже прочитанного источника "
        "дословный непрерывный фрагмент существенно короче лимита: это верхняя "
        "граница, а не требуемая длина. Целое предложение цитировать необязательно. "
        "Цитата может подтверждать одну мысль; остальное объясни вне цитаты. "
        "Сохрани смысл и ссылку, не перефразируй текст внутри цитаты. "
        "Если пользователь не просил указать число слов, не добавляй такую подпись. "
        "Дополнительный поиск ради подсчёта слов не нужен."
    )
