"""Deterministic checks for narrowly stated answer-format requirements.

This is not a factual judge. Quote limits are read only from the user's request;
citation coverage and source-first rendering retain provenance-only meaning.
Unrecognised/ambiguous wording establishes no inferred semantic contract.
"""
from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
import json
import re
from typing import Any, Literal
from urllib.parse import urldefrag

from app.application.web_evidence.receipts import valid_source


_LITERAL_RENDER_UNSAFE = re.compile(r"```|</?think>")
_SOURCE_PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t]*(?:\r?\n)+")
_SOURCE_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


@dataclass(frozen=True)
class SourceFirstAnswer:
    """Quote membership only; neither entailment nor translation is certified."""

    status: Literal["ready", "degraded", "need_more", "invalid"]
    text: str
    issues: tuple[str, ...]
    needs_more_reading: bool


def source_first_response_format(source_ids: Collection[str]) -> dict[str, Any]:
    """Constrain the ordinary writing turn's format and current source handles."""
    if not source_ids or any(not isinstance(identifier, str) or re.fullmatch(
            r"[a-zA-Z0-9_-]{1,80}", identifier) is None for identifier in source_ids):
        raise ValueError("source-first writing requires valid presented source IDs")
    identifiers = sorted(set(source_ids))
    return {"type": "json_schema", "json_schema": {
        "name": "read_source_answer", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "facts": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "source_id": {"type": "string", "enum": identifiers},
                        "quote": {"type": "string"},
                        "translation": {"type": "string"},
                    },
                    "required": ["source_id", "quote", "translation"],
                }},
                "needs_more_reading": {"type": "boolean"},
            },
            "required": ["facts", "needs_more_reading"],
        },
    }}


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError("nonfinite_json_value")


def _source_quote_span(quote: str, excerpt: str) -> tuple[str, str] | None:
    """Retain complete source lines, or paragraph/sentence context for fragments."""
    words = quote.split()
    if not words:
        return None
    match = re.search(r"\s+".join(re.escape(word) for word in words), excerpt)
    if match is None:
        return None
    line_start = excerpt.rfind("\n", 0, match.start()) + 1
    line_end = excerpt.find("\n", match.end())
    if line_end < 0:
        line_end = len(excerpt)
    begins_line = not excerpt[line_start:match.start()].strip()
    if begins_line and not excerpt[match.end():line_end].strip():
        # Complete literal lines may be headlines, dates or reference blocks;
        # their lack of sentence punctuation must not pull in the entire feed.
        return match.group(), match.group()
    start, end = 0, len(excerpt)
    if _SOURCE_PARAGRAPH_BREAK.search(excerpt):
        for boundary in _SOURCE_PARAGRAPH_BREAK.finditer(excerpt):
            if boundary.end() <= match.start():
                start = boundary.end()
            elif boundary.start() >= match.end():
                end = boundary.start()
                break
    else:
        if begins_line:
            start = match.start()
        # A single newline may wrap a condition within the same sentence.
        # Decimal points do not match because the next character is a digit.
        for boundary in _SOURCE_SENTENCE_END.finditer(excerpt):
            if boundary.end() <= match.start():
                start = max(start, boundary.end())
            elif boundary.end() >= match.end():
                end = boundary.end()
                break
    return match.group(), excerpt[start:end].strip()


def parse_source_first_answer(
    raw: str, *, presented_sources: Collection[Mapping[str, Any]],
    quote_word_limit: int | None = None,
) -> SourceFirstAnswer:
    """Parse an ordinary writer's quote selection against its current excerpts.

    The caller supplies only sources presented in the latest model context.
    Invalid JSON/schema rejects the envelope; an unavailable quote drops only
    that fact. Complete source-line blocks stay literal. Other selections expand
    to their read paragraphs, or complete sentences when extraction has no
    blank-line structure; a source-line start bounds preceding metadata. These
    structural units retain local text without certifying semantic support.
    Explicit word limits apply before
    literal rendering; an overlong span is omitted, never clipped.
    Expanded selections discard their model-authored translations.
    Non-Latin/mixed quotes keep only the original; optional Latin-text
    translations remain visible beside the original and are not authenticated.
    """
    if quote_word_limit is not None and (type(quote_word_limit) is not int or quote_word_limit < 1):
        raise ValueError("quote_word_limit must be a positive integer or None")
    try:
        if not isinstance(raw, str):
            raise ValueError("invalid_json_type")
        payload = json.loads(raw, object_pairs_hook=_unique_json_object,
                             parse_constant=_reject_json_constant)
    except (ValueError, TypeError, RecursionError) as error:
        issue = str(error) if str(error) in {
            "duplicate_json_key", "nonfinite_json_value", "invalid_json_type",
        } else "invalid_json"
        return SourceFirstAnswer("invalid", "", (issue,), False)
    if (not isinstance(payload, dict) or set(payload) != {"facts", "needs_more_reading"}
            or not isinstance(payload["facts"], list)
            or type(payload["needs_more_reading"]) is not bool):
        return SourceFirstAnswer("invalid", "", ("invalid_answer_schema",), False)
    facts = payload["facts"]
    for index, fact in enumerate(facts, 1):
        if (not isinstance(fact, dict) or set(fact) != {"source_id", "quote", "translation"}
                or not all(isinstance(value, str) for value in fact.values())
                or re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", fact["source_id"]) is None):
            return SourceFirstAnswer("invalid", "", (f"fact:{index}:invalid_schema",), False)

    sources = {
        source["id"]: source for source in presented_sources
        if isinstance(source, Mapping) and valid_source(dict(source))
        and source.get("status") == "excerpt" and source.get("quote_verified") is True
    }
    blocks: list[str] = []
    issues: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, fact in enumerate(facts, 1):
        source_id = fact["source_id"]
        source = sources.get(source_id)
        selection = _source_quote_span(fact["quote"], source["quote"]) if source else None
        if selection is None:
            issues.append(f"fact:{index}:quote_not_in_presented_source")
            continue
        selected, span = selection
        expanded = span != selected
        if expanded:
            issues.append(f"fact:{index}:quote_expanded")
        if _LITERAL_RENDER_UNSAFE.search(span):
            # The UI strips think tags even across fenced blocks; reject either
            # tag so another fact cannot complete a pair around literal text.
            issues.append(f"fact:{index}:unrenderable_quote")
            continue
        if quote_word_limit is not None and _quote_word_count(span) > quote_word_limit:
            issues.append(f"fact:{index}:quote_word_limit_exceeded")
            continue
        identity = (source_id, span)
        if identity in seen:
            issues.append(f"fact:{index}:duplicate_quote")
            continue
        seen.add(identity)
        translation = fact["translation"].strip()
        quote = f"```text\n{span}\n```\n\n[[source:{source_id}]]"
        if translation:
            # Script is used only to suppress rewriting non-Latin source text;
            # it cannot establish that a source is English or a translation faithful.
            latin_only = all(not char.isalpha() or char.isascii() for char in span)
            if expanded:
                issues.append(f"fact:{index}:translation_ignored_after_expansion")
            elif not latin_only or not any(char.isalpha() for char in span):
                issues.append(f"fact:{index}:translation_ignored")
            elif _LITERAL_RENDER_UNSAFE.search(translation):
                issues.append(f"fact:{index}:unrenderable_translation")
            elif quote_word_limit is not None and _quote_word_count(translation) > quote_word_limit:
                issues.append(f"fact:{index}:translation_quote_word_limit_exceeded")
            else:
                quote = f"Перевод цитаты:\n\n```text\n{translation}\n```\n\nОригинал:\n\n{quote}"
        blocks.append(quote)

    needs_more = payload["needs_more_reading"]
    if not blocks:
        return SourceFirstAnswer("need_more" if needs_more else "invalid", "",
                                 tuple(issues or ["no_supported_facts"]), needs_more)
    status = "degraded" if issues or needs_more else "ready"
    return SourceFirstAnswer(status, "Из прочитанных источников:\n\n" + "\n\n".join(blocks),
                             tuple(issues), needs_more)


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


def explicit_web_answer_constraints(request: str) -> dict[str, Any]:
    """Recognise narrow, direct limits; model-provided quotes cannot grant them.

    Unrecognised prose is not converted to a numerical budget. This shares the
    format-check owner's deliberately limited scope, not semantic acceptance.
    """
    prose = _BLOCK.sub("", _INLINE_CODE.sub("", _FENCED_CODE.sub("", request or "")))
    numbers = {word: value for value, words in enumerate((
        "ноль нуля zero", "один одного одной одну one", "два двух две two",
        "три трёх трех three", "четыре четырёх четырех four", "пять пяти five",
        "шесть шести six", "семь семи seven", "восемь восьми eight", "девять девяти nine", "десять десяти ten",
    )) for word in words.split()}
    number = r"(\d{1,5}|" + "|".join(numbers) + r")"
    units = {
        "max_search_queries": r"(?:поисков\w*\s+запрос\w*|search\s+quer(?:y|ies))",
        "max_read_urls": r"(?:страниц\w*|pages?|urls?)",
        "max_chars": r"(?:символ\w*|characters?|chars?)",
    }
    result: dict[str, Any] = {}

    def negated(prefix: str) -> bool:
        return bool(re.search(r"\b(?:не\s+(?:используй|делай|ограничивай|включи|включай|добавь|добавляй|напиши|пиши)"
                              r"|(?:do\s+not|don't)\s+(?:use|include|contain|limit))\b", prefix, re.I))

    for clause in re.split(r"[.!?;\n]", _PAIRED_QUOTE.sub("", prose)):
        directive = re.search(r"\b(?:не\s+(?:более|длиннее)|максимум|только|достаточно|используй|сделай|"
                              r"at\s+most|no\s+more\s+than|only|use)\s+", clause, re.I)
        if not directive or negated(clause[:directive.end()]):
            continue
        limit_text = clause[directive.end():]
        for field, unit in units.items():
            values = set()
            for match in re.finditer(number + r"\s+" + unit + r"\b", limit_text, re.I):
                raw = match[1].casefold()
                values.add(int(raw) if raw.isdecimal() else numbers[raw])
            if len(values) == 1:
                # A later direct clarification replaces an earlier stated cap.
                result[field] = values.pop()
    literals = []
    for match in _PAIRED_QUOTE.finditer(prose):
        prefix = re.split(r"[.!?;\n]", prose[max(0, match.start() - 100):match.start()])[-1]
        if not negated(prefix) and re.search(r"(?:включи|добавь|напиши|содержать|include|contain)\s+"
                     r"(?:(?:буквально|точно|слово|строку|фразу|exactly|the\s+words?)\s+)*$", prefix, re.I):
            literals.append(next(group for group in match.groups() if group is not None))
    if literals:
        result["contains"] = literals
    tool_clause = re.search(r"(?:используй\s+только|use\s+only|only\s+use)\s+"
                            r"((?:web_search|web_fetch|web_query|web_sitemap)\b[^.!?;\n]*)", prose, re.I)
    if tool_clause and not negated(re.split(r"[.!?;\n]", prose[:tool_clause.end()])[-1]):
        result["allowed"] = re.findall(r"\b(?:web_search|web_fetch|web_query|web_sitemap)\b", tool_clause[1])
    return result


def explicit_web_check_requested(request: str) -> bool:
    """Only direct source-check instructions, excluding examples and negations."""
    prose = _PAIRED_QUOTE.sub("", _BLOCK.sub("", _INLINE_CODE.sub("", _FENCED_CODE.sub("", request or ""))))
    if re.search(r"\b(?:без\s+интернет\w*|без\s+сети|не\s+(?:используй|ищи|проверяй|надо|нужно)"
                 r"[^.!?;\n]{0,50}(?:интернет\w*|веб|сеть)|(?:without|offline|don't|do\s+not)"
                 r"[^.!?;\n]{0,50}(?:web|internet|online))\b", prose, re.I):
        return False
    local_documents = bool(re.search(r"\b(?:локальн\w*|приложенн\w*|приложен|README|репозитор\w*|"
                                     r"документаци\w*\s+проекта|local|attached|repository)\b|docs/", prose, re.I))
    for clause in re.split(r"[.!?;\n]", prose):
        if re.search(r"\b(?:не\s+(?:надо|нужно|проверяй|ищи|читай|используй)|don't|do\s+not|without)\b", clause, re.I):
            continue
        if local_documents and not re.search(r"\b(?:интернет\w*|веб|сайт\w*|web|online|internet)\b", clause, re.I):
            continue
        if re.search(r"\b(?:проверь|перепроверь|сверь|найди|поищи|посмотри|изучи|прочитай|"
                     r"check|verify|search|find|read|look\s+up)\b.{0,120}\b"
                     r"(?:интернет\w*|веб\w*|сайт\w*|внешн\w+\s+источник\w*|"
                     r"официальн\w+\s+документаци\w*|web|online|internet|official\s+documentation)\b", clause, re.I):
            return True
    return False


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


@dataclass(frozen=True)
class WebSourceCitationViolation:
    block_index: int
    url: str
    reason: str


_MARKDOWN_WEB_LINK_START = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*(<?https?://)", re.IGNORECASE)
_MARKDOWN_LINK_END = re.compile(r"[ \t]*(?:\"[^\"\n]*\"|'[^'\n]*')?[ \t]*\)")
_DISCOVERY_ONLY = re.compile(
    r"^\s*(?:[-+*]|\d+[.)])?\s*"
    r"(?:(?:(?:другой|новый|этот|изменённый|измененный)\s+запрос\s+наш[её]л\s+)?"
    r"(?:найден[аоы]?\s+)?(?:документаци[яю]|ссылки|источники?|страницы|материалы)"
    r"(?:\s+для\s+проверки)?(?:\s*\(не\s+прочитаны\))?"
    r"|(?:(?:the\s+)?(?:other|new|changed|another)\s+query\s+found\s+)?"
    r"(?:(?:found|discovered)\s+)?(?:documentation|sources?|links|pages|materials)"
    r"(?:\s+for\s+verification)?)"
    r"(?:[.:;,\s()-]*(?:(?:содержимое(?:\s+страницы)?|текст(?:\s+страницы)?|страниц[аы])"
    r"\s+(?:пока\s+|ещ[её]\s+)?не\s+(?:проверен[аоы]?|прочитан[аоы]?)"
    r"|(?:page\s+)?(?:contents?|text)\s+(?:has\s+)?not\s+(?:been\s+)?(?:read|verified)))?"
    r"[.:;,\s()-]*$", re.IGNORECASE,
)


def _markdown_web_links(text: str) -> Iterator[tuple[int, int, str]]:
    """Read inline Markdown targets, including balanced URL parentheses."""
    for match in _MARKDOWN_WEB_LINK_START.finditer(text):
        start = match.start(1)
        angled = text[start] == "<"
        start += int(angled)
        end = start
        depth = 0
        while end < len(text) and not text[end].isspace():
            char = text[end]
            if angled and char == ">":
                break
            if not angled:
                if char == "\\" and end + 1 < len(text) and text[end + 1] in "()":
                    end += 2
                    continue
                if char == ")":
                    if depth == 0:
                        break
                    depth -= 1
                elif char == "(":
                    depth += 1
            end += 1
        if angled and (end == len(text) or text[end] != ">"):
            continue
        closing = _MARKDOWN_LINK_END.match(text, end + int(angled))
        if closing is not None:
            yield match.start(), closing.end(), re.sub(r"\\([()])", r"\1", text[start:end])


def _web_source_url_key(url: str) -> str:
    try:
        return urldefrag(url)[0]
    except ValueError:
        # Malformed model output is an unknown citation, not a parser crash.
        return url


def web_source_citation_violations(
    answer_text: str, *, read_source_urls: Collection[str], known_source_urls: Collection[str],
) -> tuple[WebSourceCitationViolation, ...]:
    """Reject unread Markdown citations, without pretending to check entailment.

    A neutral pointer to a discovered page may remain when its block contains
    no content claim. Adding an unread-source disclaimer to a factual claim
    does not turn that claim into a discovery-only pointer.
    """
    read = {_web_source_url_key(url) for url in read_source_urls}
    known = {_web_source_url_key(url) for url in known_source_urls}
    problems = []
    for index, block in enumerate(_citation_blocks(answer_text or ""), 1):
        prose = _INLINE_CODE.sub("", block)
        links = list(_markdown_web_links(prose))
        pointer = prose
        for start, end, _ in reversed(links):
            pointer = pointer[:start] + pointer[end:]
        pointer = pointer.replace("**", "").replace("__", "")
        discovery_only = bool(_DISCOVERY_ONLY.fullmatch(pointer))
        seen = set()
        for _, _, url in links:
            target = _web_source_url_key(url)
            if target in read or target in seen:
                continue
            seen.add(target)
            if discovery_only and target in known:
                continue
            problems.append(WebSourceCitationViolation(
                index, url, "unread_source" if target in known else "unknown_source",
            ))
    return tuple(problems)


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
