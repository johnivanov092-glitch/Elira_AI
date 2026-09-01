# AI Inference Server

Elira uses a dedicated LAN host for model inference. The same physical host also
runs independently managed LAN integrations such as SearXNG and Home Assistant;
they do not expand the inference server's ownership into a second agent runtime.
The Tauri UI, backend, orchestration, tools, memory, and approvals stay on the
main PC.

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

Current service topology:

| Service | Runtime | Endpoint | Backing model / contract | Compute |
|---------|---------|----------|--------------------------|---------|
| Chat / completions | `elira-llama-server` | `http://192.168.88.15:8000/v1` | `local-model`: Qwen3.8-27B Q6_K, ctx 131072 | GPU (ROCm) |
| Embeddings (RAG) | `elira-llama-embed` | `http://192.168.88.15:8001/v1` | Qwen3-Embedding-0.6B Q8, dim 1024, ctx 4096 | CPU |
| OCR | `elira-ocr` | `http://192.168.88.15:8002/ocr` | PaddleOCR multipart API | CPU |
| Search | `elira-searxng` | `http://192.168.88.15:8003` | SearXNG JSON search API | CPU |
| Vision | `elira-llama-vision` | `http://192.168.88.15:8004/v1` | MiniCPM-V 4.6 Q5 + F16 projector, ctx 8192 | GPU (ROCm) |
| TTS | systemd service | `http://192.168.88.15:8005` | Silero v4_ru HTTP API | CPU |
| STT | `elira-stt` | `http://192.168.88.15:8006` | Faster Whisper Large v3 HTTP API | CPU |

LAN integrations on the same host are separate from model inference:

| Integration | Runtime | Endpoint |
|-------------|---------|----------|
| Home Assistant | `homeassistant` | `http://192.168.88.15:8123` |
| Home Assistant MCP | `hass-mcp` | `http://192.168.88.15:8124` |

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
LLAMA_SERVER_CONTEXT_WINDOW=131072

LOCAL_EMBED_ENABLED=true
LOCAL_EMBED_BASE_URL=http://192.168.88.15:8001/v1
LOCAL_EMBED_MODEL=local-embed
LOCAL_EMBED_API_KEY=local
LOCAL_EMBED_TIMEOUT_SECONDS=30

OCR_URL=http://192.168.88.15:8002/ocr
SEARXNG_URL=http://192.168.88.15:8003
VISION_BASE_URL=http://192.168.88.15:8004/v1
ELIRA_TTS_URL=http://192.168.88.15:8005
ELIRA_STT_URL=http://192.168.88.15:8006
```

For chat, `LLAMA_SERVER_TIMEOUT_SECONDS` is the connection-establishment
timeout. Once connected, Elira uses no HTTP read/generation deadline; explicit
Workflow Stop closes the cancellable response. The live server properties remain
the source of truth for the effective context window after a model swap.

## Access (SSH)

Two separate accounts / aliases; key material lives outside both repos:

- `ai-server` -> user `claude` (automation)
- `ai-server-codex` -> user `aiadmin` (admin / maintenance)

Full access inventory and smoke tests: `../Elira_AI_Server/Server/ACCESS.md`.

## Smoke test

The repository smoke reads `backend/.env` and `backend/.env.local`, checks all
ports 8000–8006, validates the 1024-dimensional embedding contract, and performs
one bounded chat completion:

```powershell
backend\.venv\Scripts\python.exe scripts\smoke_agent_endpoints.py
```

Single chat probe, when needed:

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
