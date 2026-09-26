"""Bounded operator list rendering, retaining existing filtering/sort semantics."""
from html import escape
from urllib.parse import urlencode

PAGE_SIZE = 50


def operator_url(status, search, page_number=1):
    return "/operator?" + urlencode({"status": status, "q": search, "page": page_number})


def paginate(items, raw_page, highlight_id=None):
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    try:
        page_number = max(1, min(int(raw_page), total_pages))
    except (TypeError, ValueError):
        page_number = 1
    if highlight_id and str(highlight_id).isdigit():
        for index, item in enumerate(items):
            if str(item.id) == str(highlight_id):
                page_number = index // PAGE_SIZE + 1
                break
    start = (page_number - 1) * PAGE_SIZE
    return items[start:start + PAGE_SIZE], page_number, total_pages, total


def navigation(page_number, total_pages, total, status, search):
    start = (page_number - 1) * PAGE_SIZE + 1 if total else 0
    end = min(page_number * PAGE_SIZE, total)
    parts = [f'<nav aria-label="Страницы работ" style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:16px 0;">',
             f'<span style="margin-right:10px;color:#667085;">Работы {start}–{end} из {total} · Страница {page_number} из {total_pages}</span>']

    def link(number, label):
        return f'<a class="btn-light" href="{escape(operator_url(status, search, number), quote=True)}">{label}</a>'

    if page_number > 1:
        parts.append(link(page_number - 1, "← Назад"))
    shown = sorted({1, total_pages, *range(max(1, page_number - 2), min(total_pages, page_number + 2) + 1)})
    previous = 0
    for number in shown:
        if previous and number > previous + 1:
            parts.append('<span aria-hidden="true">…</span>')
        if number == page_number:
            parts.append(f'<span class="btn-light btn-active" aria-current="page">{number}</span>')
        else:
            parts.append(link(number, str(number)))
        previous = number
    if page_number < total_pages:
        parts.append(link(page_number + 1, "Далее →"))
    parts.append('</nav>')
    return "".join(parts)
