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


def _support(prose: str, page: str) -> tuple[bool, list[str]]:
    """Whether the page carries the point: (unsupported, what is missing)."""
    page_numbers = set(re.findall(r"\d+", page))
    # A Russian point about an English page (and vice versa) cannot share its
    # wording: only words in the page's script count; names and numbers decide.
    cyrillic = len(re.findall(r"[а-яё]", page))
    latin = len(re.findall(r"[a-z]", page))
    page_script = "cyrillic" if cyrillic >= latin else "latin"
    words: dict[str, str] = {}
    for word in _WORD.findall(prose):
        script = "cyrillic" if re.match(r"[А-Яа-яЁё]", word) else "latin"
        if word.lower() not in _STOP and script == page_script:
            words.setdefault(_stem(word), word)
    missing = [word for stem, word in words.items() if stem not in page]
    numbers = list(dict.fromkeys(number for number in re.findall(r"\d+", _DATE_NUMBER.sub(" ", prose))
                                 if len(number) >= 2 and not _YEAR.match(number)))
    missing_numbers = [number for number in numbers if number not in page_numbers]
    # The point's own figures: unsupported when none of them is on the page
    # (an auxiliary setting such as 256K may come from elsewhere on the page).
    unsupported = ((len(words) >= 3 and len(missing) * 2 > len(words))
                   or bool(numbers and len(missing_numbers) == len(numbers)))
    return unsupported, [*missing_numbers, *missing][:5]


def point_problems(answer_text: str, page_texts: dict[str, str], page_dates: dict[str, str],
                   user_request: str, today: date | None = None) -> list[dict[str, Any]]:
    """Points (answer lines) whose cited read page does not carry them or is out of period.

    A point carried by one of its pages keeps it; its other links that do not
    carry the point are listed as ``weak`` (removed from the answer, no retry).
    """
    texts = {url.rstrip("/"): text.lower().replace("ё", "е") for url, text in page_texts.items()}
    dates = {url.rstrip("/"): value for url, value in page_dates.items()}
    days = _period_days(user_request)
    start = (today or date.today()) - timedelta(days=days) if days else None
    problems: list[dict[str, Any]] = []
    for index, line in enumerate(str(answer_text or "").split("\n")):
        cited = [match.group(2).rstrip("/") for match in _LINK.finditer(line)]
        shown = [url for url in dict.fromkeys(cited) if url in texts]
        if not shown:
            continue
        prose = re.sub(r"[*_`#>|]", " ", _LINK.sub(" ", line))
        unsupported, missing = _support(prose, " ".join(texts[url] for url in shown))
        weak = [] if unsupported or len(shown) < 2 else [url for url in shown if _support(prose, texts[url])[0]]
        old = []
        if start is not None:
            for url in cited:
                value = dates.get(url, "")
                try:
                    if value and date.fromisoformat(value) < start:
                        old.append(value)
                except ValueError:
                    continue
        if unsupported or old or weak:
            problems.append({"line": index, "excerpt": prose.strip()[:90],
                             "missing": missing if unsupported else [], "old": sorted(set(old)),
                             "weak": weak})
    return problems


def blocking_problems(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Problems that remove a point (and earn the one correction); weak extra links do not."""
    return [problem for problem in problems if problem["missing"] or problem["old"]]


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


_LABELS = re.compile(r"\[((?:[SL]\d{1,4})(?:\s*[,;]\s*[SL]\d{1,4})*)\]")


def resolve_web_labels(answer_text: str, labels: dict[str, str], numbered_pages: list[tuple[str, str]]) -> str:
    """[S9] / [L3] in an answer: the number of the read page, or a link to the unread one."""
    if not labels:
        return answer_text
    numbers = {url.rstrip("/"): number for number, (url, _) in enumerate(numbered_pages, 1) if url}

    def replace(match: re.Match[str]) -> str:
        parts = []
        for name in re.split(r"\s*[,;]\s*", match.group(1)):
            url = labels.get(name.upper())
            if url:
                number = numbers.get(url.rstrip("/"))
                parts.append(f"[{number}]" if number else f"[{name.upper()}]({url})")
        return ", ".join(parts) if parts else match.group(0)
    return _LABELS.sub(replace, answer_text)


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
    drop = {problem["line"] for problem in blocking_problems(problems)}
    weak = {problem["line"]: {_link_key(url) for url in problem.get("weak", ())} for problem in problems}
    lines = str(answer_text or "").split("\n")
    kept: list[str] = []
    removed = stripped = 0
    for index, line in enumerate(lines):
        links = [match for match in _LINK.finditer(line)]
        broken = [match for match in links
                  if _link_key(match.group(2)) in bad or _link_key(match.group(2)) in weak.get(index, ())]
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
            if not rest or rest[0].lstrip().startswith("#") or re.fullmatch(r"\s*([-*_])\1{2,}\s*", rest[0]):
                continue  # a heading with nothing left under it (next heading, a rule or the end)
        result.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(result)).strip()
    text += f"\n\n_Убрано пунктов: {removed} — не нашлось в прочитанных статьях или вне запрошенного периода._"
    return text, removed + stripped
