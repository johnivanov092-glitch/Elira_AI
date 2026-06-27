# AI Inference Server

Elira offloads only model inference to a dedicated LAN server. The Tauri UI,
backend, tools, memory, and approvals stay on the main PC; the server exposes
OpenAI-compatible chat and embedding endpoints over the LAN.

This page is a self-contained summary for working inside the `Elira_AI` repo.
The authoritative operational docs (hardware, ROCm, Docker ops, model
selection, network/security, troubleshooting) live in the sibling repo
`Elira_AI_Server` (same parent folder), e.g. `../Elira_AI_Server/README.md` and
`../Elira_AI_Server/Server/ACCESS.md`. Do not duplicate that operational detail
here, and do not store keys, passwords, or tokens here.

## Host

- LAN IP: `192.168.88.15` (LAN-only; no public exposure)
- OS / stack: Ubuntu + ROCm + Docker
- GPU: AMD Radeon AI Pro R9700, 32 GB VRAM
- CPU / RAM: Ryzen 9 5900X / 32 GB

## Services

Four containers; the GPU services use `ghcr.io/ggml-org/llama.cpp:server-rocm`,
embeddings use `ghcr.io/ggml-org/llama.cpp:server` (CPU), OCR uses PaddleOCR (CPU):

| Service | Container | Endpoint | OpenAI model | Backing model (current, swappable) | Compute |
|---------|-----------|----------|--------------|------------------------------------|---------|
| Chat / completions | `elira-llama-server` | `http://192.168.88.15:8000/v1` | `local-model` | Qwen3.6-35B-A3B (Q4_K_XL), 128K ctx | GPU (ROCm) |
| Vision (multimodal) | `elira-llama-vision` | `http://192.168.88.15:8004/v1` | `vision-model` | MiniCPM-V 4.6 (Q5_K_M) + mmproj-f16 | GPU (ROCm) |
| Embeddings (RAG) | `elira-llama-embed` | `http://192.168.88.15:8001/v1` | `local-embed` | Qwen3-Embedding-0.6B GGUF, dim 1024 | CPU |
| OCR | `elira-ocr` | `http://192.168.88.15:8002/ocr` | — | PaddleOCR | CPU |

- Monitoring (Netdata): `http://192.168.88.15:19999`
- Backing models change on swaps; `../Elira_AI_Server/Server/ACCESS.md` is the
  source of truth for the currently-served model.

## How Elira connects

- Provider client: `backend/app/infrastructure/llm/openai_compatible.py`
- Provider names in app state / metrics: `llama_server`, `local_embed_server`
- Configure `backend/.env.local` (never commit):

```env
LLAMA_SERVER_ENABLED=true
LLAMA_SERVER_BASE_URL=http://192.168.88.15:8000/v1
LLAMA_SERVER_MODEL=local-model
LLAMA_SERVER_API_KEY=local
LLAMA_SERVER_TIMEOUT_SECONDS=600
LLAMA_SERVER_CONTEXT_WINDOW=32768

LOCAL_EMBED_ENABLED=true
LOCAL_EMBED_BASE_URL=http://192.168.88.15:8001/v1
LOCAL_EMBED_MODEL=local-embed
LOCAL_EMBED_API_KEY=local
LOCAL_EMBED_TIMEOUT_SECONDS=30
```

## Access (SSH)

Two separate accounts / aliases; key material lives outside both repos:

- `ai-server` -> user `claude` (automation)
- `ai-server-codex` -> user `aiadmin` (admin / maintenance)

Full access inventory and smoke tests: `../Elira_AI_Server/Server/ACCESS.md`.

## Smoke test (chat)

```powershell
$body = @{model='local-model'; messages=@(@{role='user'; content='Return OK'}); max_tokens=4} | ConvertTo-Json -Depth 6
Invoke-WebRequest -UseBasicParsing -Uri http://192.168.88.15:8000/v1/chat/completions -Method POST -Headers @{Authorization='Bearer local'} -ContentType 'application/json' -Body $body
```

## Rules

- LAN-only unless a reverse proxy + TLS are added in front; the in-app bearer
  token gate (`backend/app/core/auth.py`) already protects non-loopback callers.
- Stage 1: do not move the Elira backend onto the server; it stays
  inference-only.
- Server hardware / ROCm / Docker operations belong to the `Elira_AI_Server`
  repo, not here.
