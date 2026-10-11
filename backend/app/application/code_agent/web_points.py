"""Each point of a web answer must come from the page it cites (John 2026-10-11).

A mechanical provenance check, not a judgement of quality: the significant
words and numbers of a point are looked up in the text the model was shown for
the cited page, and the page's publication date is compared with the period
the user asked for ("за неделю", "сегодня"). The answer is never rewritten;
a point that fails gets one correction request, then it is removed (John 2026-10-11).
"""
from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any

_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]{5,}")
_DATE_NUMBER = re.compile(
    r"\b\d{1,2}(?:\s*[–-]\s*\d{1,2})?\s+(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)",
    re.IGNORECASE)
_YEAR = re.compile(r"^(?:19|20)\d\d$")
_STOP = {
    "который", "которая", "которое", "которые", "которых", "также", "после", "через", "будет", "более", "может",
    "этого", "этому", "между", "около", "своих", "своей", "время", "сегодня", "неделю", "недели", "новости",
    "событие", "события", "сводка", "главное", "кроме", "среди", "всего", "своим", "чтобы", "потому", "стало",
    "where", "which", "there", "their", "about", "after", "these", "those", "would", "could", "other", "being",
}


def _stem(word: str) -> str:
    return word.lower().replace("ё", "е")[:5]


def _period_days(request: str) -> int | None:
    text = str(request or "").lower()
    if re.search(r"сегодня|за сутки|\btoday\b", text):
        return 1
    if re.search(r"вчера|\byesterday\b", text):
        return 2
    if re.search(r"недел|\bweek\b|7 дней|семь дней", text):
        return 7
    if re.search(r"месяц|\bmonth\b|30 дней", text):
        return 31
    return None


def point_problems(answer_text: str, page_texts: dict[str, str], page_dates: dict[str, str],
                   user_request: str, today: date | None = None) -> list[dict[str, Any]]:
    """Points (answer lines) whose cited read page does not carry them or is out of period."""
    texts = {url.rstrip("/"): text.lower().replace("ё", "е") for url, text in page_texts.items()}
    dates = {url.rstrip("/"): value for url, value in page_dates.items()}
    days = _period_days(user_request)
    start = (today or date.today()) - timedelta(days=days) if days else None
    problems: list[dict[str, Any]] = []
    for index, line in enumerate(str(answer_text or "").split("\n")):
        cited = [match.group(2).rstrip("/") for match in _LINK.finditer(line)]
        shown = [texts[url] for url in cited if url in texts]
        if not shown:
            continue
        prose = re.sub(r"[*_`#>|]", " ", _LINK.sub(" ", line))
        page = " ".join(shown)
        page_numbers = set(re.findall(r"\d+", page))
        words: dict[str, str] = {}
        for word in _WORD.findall(prose):
            if word.lower() not in _STOP:
                words.setdefault(_stem(word), word)
        missing = [word for stem, word in words.items() if stem not in page]
        numbers = [number for number in re.findall(r"\d+", _DATE_NUMBER.sub(" ", prose))
                   if len(number) >= 2 and not _YEAR.match(number)]
        missing_numbers = [number for number in dict.fromkeys(numbers) if number not in page_numbers]
        unsupported = (len(words) >= 3 and len(missing) * 2 > len(words)) or bool(missing_numbers)
        old = []
        if start is not None:
            for url in cited:
                value = dates.get(url, "")
                try:
                    if value and date.fromisoformat(value) < start:
                        old.append(value)
                except ValueError:
                    continue
        if unsupported or old:
            problems.append({"line": index, "excerpt": prose.strip()[:90],
                             "missing": [*missing_numbers, *missing][:5] if unsupported else [],
                             "old": sorted(set(old))})
    return problems


def describe_point_problems(problems: list[dict[str, Any]], limit: int = 6) -> str:
    parts = []
    for problem in problems[:limit]:
        reasons = []
        if problem["missing"]:
            reasons.append("на странице нет: " + ", ".join(problem["missing"]))
        if problem["old"]:
            reasons.append("статья от " + ", ".join(problem["old"]) + " — раньше запрошенного периода")
        parts.append(f"«{problem['excerpt']}…» — " + "; ".join(reasons))
    return "; ".join(parts)


def _link_key(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/")


def remove_unverified_points(answer_text: str, problems: list[dict[str, Any]],
                             bad_urls: set[str]) -> tuple[str, int]:
    """John 2026-10-11: only verified points reach the chat.

    A line is dropped when its point is not on its cited page or is out of the
    asked period, or when every link it has is unread / a home page. In a kept
    line a bad link is removed. Sections left empty lose their heading. Returns
    the text and how many points and links were removed.
    """
    bad = {_link_key(url) for url in bad_urls}
    drop = {problem["line"] for problem in problems}
    lines = str(answer_text or "").split("\n")
    kept: list[str] = []
    removed = stripped = 0
    for index, line in enumerate(lines):
        links = [match for match in _LINK.finditer(line)]
        broken = [match for match in links if _link_key(match.group(2)) in bad]
        if index in drop or (links and len(broken) == len(links)):
            removed += 1
            continue
        for match in broken:
            line = re.sub(r"(?:,\s*)?" + re.escape(match.group(0)), "", line, count=1)
            stripped += 1
        kept.append(line)
    if not removed:
        return ("\n".join(kept), stripped) if stripped else (answer_text, 0)
    result: list[str] = []
    for index, line in enumerate(kept):
        if line.lstrip().startswith("#"):
            rest = [item for item in kept[index + 1:] if item.strip()]
            if not rest or rest[0].lstrip().startswith("#"):
                continue  # a heading with nothing left under it
        result.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(result)).strip()
    text += f"\n\n_Убрано пунктов: {removed} — не нашлось в прочитанных статьях или вне запрошенного периода._"
    return text, removed + stripped
