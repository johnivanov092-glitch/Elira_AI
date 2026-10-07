---
name: huggingface-mcp
description: "MCP huggingface: модели, датасеты и статьи Hugging Face Hub."
metadata:
  title: Hugging Face Hub
---

# Hugging Face (MCP `huggingface`)

Применяй, чтобы найти модель или датасет, сравнить их по карточкам (размер,
лицензия, загрузки), найти статью или документацию transformers/diffusers и т.п.

## Запуск

`mcp(action='start', server_id='huggingface', query='hub_repo_search hub_repo_details paper_search hf_doc_search')`

## Главные инструменты

- `huggingface__hub_repo_search(query, repo_types?, author?, sort?, limit?)` —
  поиск моделей, датасетов, Spaces (`repo_types`: `model`, `dataset`, `space`).
- `huggingface__hub_repo_details(repo_ids)` — карточки репозиториев
  (`["Qwen/Qwen3-8B"]`).
- `huggingface__paper_search(query, results_limit?)` — статьи по ML.
- `huggingface__hf_doc_search(query, product?)`, `huggingface__hf_doc_fetch(doc_url)`
  — документация HF.
- `huggingface__space_search(query)` — найти Space.

## Ограничения

- Запуск Spaces (`dynamic_space`) и генерация изображений
  (`gr1_z_image_turbo_generate`) — облачный ИИ-инференс; у Elira всё локально,
  не используй.
- Скачивание весов модели — обычными средствами (`run_bash`, `huggingface-cli`)
  только по просьбе: это гигабайты.

Конфиг: запись `huggingface` в `data/mcp_servers.json` (HTTP huggingface.co/mcp).
