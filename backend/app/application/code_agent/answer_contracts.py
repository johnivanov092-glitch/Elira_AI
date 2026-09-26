"""Deterministic checks for narrowly stated answer-format requirements.

This is not a factual or quotation-authenticity judge. Quote limits are read
only from the user's request; citation coverage retains provenance-only meaning.
Unrecognised/ambiguous wording establishes no inferred semantic contract.
"""
from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Any


_FENCED_CODE = re.compile(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_PAIRED_QUOTE = re.compile(r'"([^"\n]+)"|«([^»]+)»|“([^”]+)”')
_LIMIT = re.compile(
    r"\b(?:цитат[ауые]|quote|quotation)\s*(?:[:—-]\s*)?"
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


@dataclass(frozen=True)
class WebCadenceCitationViolation:
    block_index: int
    quantity: str
    reason: str = "missing_reference"


_NUMBER_WORDS = {
    word: str(number)
    for number, words in {
        1: "one один одна одно одну одной одного",
        2: "two два две двух",
        3: "three три трех трёх",
        4: "four четыре четырех четырёх",
        5: "five пять пяти",
        6: "six шесть шести",
        7: "seven семь семи",
        8: "eight восемь восьми",
        9: "nine девять девяти",
        10: "ten десять десяти",
        11: "eleven одиннадцать одиннадцати",
        12: "twelve двенадцать двенадцати",
    }.items()
    for word in words.split()
}
_CADENCE_NUMBER = (
    r"(?:\d+(?:[.,]\d+)?(?:\s*[–—-]\s*\d+(?:[.,]\d+)?)?|"
    + "|".join(_NUMBER_WORDS) + ")"
)
_CADENCE_UNIT = (
    r"(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?|"
    r"секунд\w*|минут\w*|час\w*|день|дн(?:я|ей)|недел\w*|месяц\w*|год(?:а|ов)?|лет)"
)
_CADENCE_DURATION = re.compile(
    rf"(?<![\w.])(?P<number>{_CADENCE_NUMBER})\s+(?P<unit>{_CADENCE_UNIT})\b", re.IGNORECASE,
)
_CADENCE_RATE = re.compile(
    rf"(?<![\w.])(?P<number>{_CADENCE_NUMBER})\s+"
    r"(?:updates?|releases?|versions?|обновлен\w*|релиз\w*|версии?|выпуск\w*)\s+"
    rf"(?:per|a|an|в)\s+(?P<unit>{_CADENCE_UNIT})\b", re.IGNORECASE,
)
_UPDATE_CONTEXT = re.compile(
    r"\b(?:update[sd]?|release[sd]?|versions?|обновл\w*|релиз\w*|верси\w*|выпуск\w*)\b",
    re.IGNORECASE,
)
_CADENCE_CONTEXT = re.compile(
    r"\b(?:every|each|cadence|frequency|cycle|interval|period|"
    r"кажд\w*|раз\s+в|период\w*|частот\w*|цикл\w*|интервал\w*)\b",
    re.IGNORECASE,
)
_WEB_REFERENCE = re.compile(r"https?://[^\s<>\]\)]+", re.IGNORECASE)
_SOURCE_REFERENCE = re.compile(r"\[\[source:([a-zA-Z0-9_-]{1,80})\]\]")
_LIST_START = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_HEADING_START = re.compile(r"^\s*#{1,6}(?:\s+|$)")


def _citation_blocks(text: str) -> Iterator[str]:
    """Keep wrapped paragraphs/items together, but never join adjacent items."""
    text = _FENCED_CODE.sub(lambda match: "\n" * match.group().count("\n"), text)
    pending: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        boundary = (
            not stripped or bool(_LIST_START.match(line))
            or bool(_HEADING_START.match(line)) or stripped.startswith(("|", ">"))
        )
        if boundary and pending:
            yield "\n".join(pending)
            pending = []
        if stripped:
            pending.append(line)
    if pending:
        yield "\n".join(pending)


def _cadence_quantity_key(match: re.Match[str]) -> tuple[str, str, str]:
    number = match.group("number").lower()
    if number in _NUMBER_WORDS:
        number = _NUMBER_WORDS[number]
    else:
        number = "-".join(
            format(Decimal(part.strip().replace(",", ".")).normalize(), "f")
            for part in re.split(r"[–—-]", number)
        )
    unit = match.group("unit").lower()
    for canonical, prefixes in (
        ("second", ("second", "секунд")), ("minute", ("minute", "минут")),
        ("hour", ("hour", "час")), ("day", ("day", "день", "дня", "дней")),
        ("week", ("week", "недел")), ("month", ("month", "месяц")),
        ("year", ("year", "год", "лет")),
    ):
        if unit.startswith(prefixes):
            unit = canonical
            break
    return ("rate" if match.re is _CADENCE_RATE else "duration", number, unit)


def web_cadence_citation_violations(
    answer_text: str, *, matched_source_ids: Collection[str],
    read_source_urls: Collection[str],
    read_sources: Collection[Mapping[str, Any]] | None = None,
) -> tuple[WebCadenceCitationViolation, ...]:
    """Find numeric update periods lacking a read reference in the same block.

    The caller activates this only for externally grounded requests with actual
    presented web excerpts. With ``read_sources`` supplied, a cited excerpt must
    also contain the same quantity and unit (including RU/EN number words).
    This detects missing evidence, never proves entailment or the same subject.
    Omitting ``read_sources`` retains the reference-coverage-only contract.
    Version numbers, dates, arbitrary measurements and historical windows are
    intentionally outside this narrow contract.
    """
    matched_ids = set(matched_source_ids)
    read_urls = set(read_source_urls)
    violations = []
    for index, block in enumerate(_citation_blocks(answer_text or ""), 1):
        prose = _INLINE_CODE.sub("", block)
        # Inline numeric values are formatting, while actual code (including
        # citation examples) stays outside this contract.
        visible = _INLINE_CODE.sub(
            lambda match: match.group()[1:-1] if re.fullmatch(
                rf"{_CADENCE_NUMBER}(?:\s+{_CADENCE_UNIT})?", match.group()[1:-1], re.IGNORECASE,
            ) else "", block,
        )
        visible = _WEB_REFERENCE.sub("", visible).replace("**", "").replace("__", "")
        rate = _CADENCE_RATE.search(visible)
        quantity = rate
        if not quantity and _UPDATE_CONTEXT.search(visible):
            for duration in _CADENCE_DURATION.finditer(visible):
                # A later mention of "period" must not turn an earlier
                # historical window ("over the last 4 weeks") into cadence.
                prefix = re.split(r"[;.!?\n]", visible[:duration.start()])[-1][-160:]
                if _CADENCE_CONTEXT.search(prefix):
                    quantity = duration
                    break
        if not quantity:
            continue
        cited_ids = matched_ids.intersection(_SOURCE_REFERENCE.findall(prose))
        urls = {match.group().rstrip(".,;:!?") for match in _WEB_REFERENCE.finditer(prose)}
        cited_urls = read_urls.intersection(urls)
        if cited_ids or cited_urls:
            if read_sources is None:
                continue
            expected = _cadence_quantity_key(quantity)
            cited_quotes = [
                source["quote"] for source in read_sources
                if isinstance(source.get("quote"), str)
                and (source.get("id") in cited_ids or source.get("url") in cited_urls)
            ]
            if any(
                _cadence_quantity_key(candidate) == expected
                for quote in cited_quotes
                for pattern in (_CADENCE_DURATION, _CADENCE_RATE)
                for candidate in pattern.finditer(quote)
            ):
                continue
            reason = "missing_quantity"
        else:
            reason = "missing_reference"
        violations.append(WebCadenceCitationViolation(index, quantity.group(), reason))
    return tuple(violations)


def web_cadence_citation_correction(
    violations: tuple[WebCadenceCitationViolation, ...],
) -> str:
    """Request one targeted citation correction, not a general answer review."""
    if not violations:
        return ""
    details = "; ".join(
        f"блок {item.block_index}: {item.quantity} — "
        + ("в процитированных выдержках не найдена эта величина с этой единицей"
           if item.reason == "missing_quantity" else "нет ссылки на прочитанный источник")
        for item in violations[:8]
    )
    return (
        "[Проверка ссылки для периода обновлений] " + details + ". "
        "Проверь число, единицу и тот же объект/канал в конкретном фрагменте: "
        "период соседнего канала или исторический период не подтверждает текущий. "
        "Если подтверждение уже прочитано, поставь его точный [[source:id]] или "
        "URL рядом с утверждением. Не добавляй ссылку только ради прохождения "
        "проверки. Если период не подтверждён и не нужен для ответа на вопрос, "
        "убери эту необязательную деталь и ответь по подтверждённым данным. "
        "Если пользователь спрашивал сам период, прочитай соответствующий "
        "актуальный источник или явно сообщи, что период не подтверждён. "
        "Сохрани остальные подтверждённые сведения; новый поиск не нужен, "
        "когда достаточно убрать неподтверждённую деталь."
    )
