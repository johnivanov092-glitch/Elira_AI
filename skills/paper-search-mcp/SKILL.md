---
name: paper-search-mcp
description: "MCP paper_search: научные статьи — arXiv, Semantic Scholar, PubMed, DOI."
metadata:
  title: Поиск научных статей
---

# Научные статьи (MCP `paper_search`)

Применяй для научного обзора: найти статьи по теме, проверить DOI, прочитать
открытый текст статьи.

Обрати внимание: id сервера `paper_search` (с подчёркиванием), навык —
`paper-search-mcp`.

## Запуск

У сервера ~57 инструментов (по три на каждую базу), поэтому всегда передавай
`query` с нужными:
`mcp(action='start', server_id='paper_search', query='search_papers search_arxiv read_arxiv_paper get_crossref_paper_by_doi')`
— медицина: `search_pubmed search_europepmc read_pubmed_paper`.

## Главные инструменты

- `paper_search__search_papers(query, ...)` — общий поиск по нескольким базам.
- `paper_search__search_arxiv(query, max_results?)`,
  `paper_search__search_semantic(...)`, `paper_search__search_openalex(...)`,
  `paper_search__search_pubmed(...)`, `paper_search__search_crossref(...)`.
- `paper_search__get_crossref_paper_by_doi(doi)` — метаданные по DOI.
- `paper_search__read_arxiv_paper(paper_id)` и другие `read_*_paper` — текст
  открытой статьи.
- `paper_search__download_with_fallback(...)` — скачать PDF из открытых источников.

## Порядок

Поиск (запрос по-английски) → отбор по названию, году, цитированию → чтение
текста нужных статей → ответ со ссылками (DOI или arXiv id). Выводы — из
прочитанного текста, не из одного заголовка.

## Ограничения

`download_scihub` и Sci-Hub в `download_with_fallback` не используй — только
открытые источники.

Конфиг: запись `paper_search` в `data/mcp_servers.json`.
