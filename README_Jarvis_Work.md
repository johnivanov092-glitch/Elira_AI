# Jarvis_Work — Personal AI Agent Platform

> A local-first, self-hosted AI workspace built as a real engineering project — not a demo chatbot.

![Python](https://img.shields.io/badge/Python-78%25-3776AB?style=flat-square&logo=python&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-React%2FVite-F7DF1E?style=flat-square&logo=javascript&logoColor=black)
![Rust](https://img.shields.io/badge/Rust-Tauri-000000?style=flat-square&logo=rust&logoColor=white)
![Ollama](https://img.shields.io/badge/Inference-Ollama-black?style=flat-square)
![Status](https://img.shields.io/badge/Status-Active%20Development-brightgreen?style=flat-square)

---

## What Is This?

Jarvis_Work is a local AI agent platform I built to combine **LLM inference, memory, tool execution, automation, and a usable interface** into a single working system — running entirely on my own hardware, without sending data to any cloud.

The goal was not to wrap an API in a chat window. The goal was to build something I actually use every day.

---

## Core Stack

| Layer | Technology |
|---|---|
| **Backend** | Python · FastAPI · SQLite · service orchestration |
| **Frontend** | JavaScript · React · Vite |
| **Desktop** | Tauri · Rust |
| **Inference** | Ollama · local model workflows |
| **Features** | Streaming responses · persistent memory · file operations · tool execution · plugins |

---

## Architecture Overview

```
┌─────────────────────────────────────────┐
│            Desktop (Tauri/Rust)         │
│  ┌─────────────────────────────────┐    │
│  │     Frontend (React / Vite)     │    │
│  └──────────────┬──────────────────┘    │
│                 │ HTTP / WebSocket      │
│  ┌──────────────▼──────────────────┐    │
│  │     Backend (Python / FastAPI)  │    │
│  │   ┌──────────┐  ┌────────────┐  │    │
│  │   │  Memory  │  │   Tools    │  │    │
│  │   │ (SQLite) │  │ (Plugins)  │  │    │
│  │   └──────────┘  └────────────┘  │    │
│  └──────────────┬──────────────────┘    │
│                 │                       │
│  ┌──────────────▼──────────────────┐    │
│  │    Inference (Ollama / local)   │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

---

## Key Features

- **Local-first inference** — Ollama backend, runs fully offline, no API keys required
- **Persistent memory** — SQLite-based conversation and context storage across sessions
- **Streaming responses** — real-time token streaming in the UI
- **Tool execution** — pluggable tool system for file operations, search, and custom actions
- **Plugin support** — extensible architecture for adding new capabilities
- **Desktop packaging** — Tauri/Rust wrapper for native desktop experience

---

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
- [Ollama](https://ollama.ai) installed and running
- Rust toolchain (for Tauri desktop build)

### Quick Start (Backend + Frontend)

```bash
# Clone the repository
git clone https://github.com/johnivanov092-glitch/Jarvis_Work.git
cd Jarvis_Work

# Start backend
cd backend
pip install -r requirements.txt
python main.py

# Start frontend (separate terminal)
cd frontend
npm install
npm run dev
```

### Desktop Build (Tauri)

```bash
# From root
npm install
npm run tauri dev    # development
npm run tauri build  # production binary
```

### Windows Quick Launch

```bash
# From repo root — starts backend + frontend together
Jarvis.bat

# Mobile-friendly variant
Jarvis_Mobile.bat
```

---

## Project Structure

```
Jarvis_Work/
├── backend/          # Python / FastAPI — API, memory, tool orchestration
├── frontend/         # React / Vite — UI, streaming, plugin views
├── src-tauri/        # Rust / Tauri — desktop shell, native APIs
├── scripts/          # Utility and build scripts
├── data/             # Local data, SQLite databases
├── docs/             # Project documentation
├── Jarvis.bat        # Windows launcher
└── build_exe.bat     # Standalone executable build
```

---

## Why I Built This

I wanted hands-on experience with **local AI infrastructure** — not just prompt engineering, but the full stack: model serving, orchestration, backend architecture, frontend integration, desktop packaging, and runtime control.

This project runs in my daily workflow. It handles real tasks. That forces production-minded thinking around reliability, extensibility, and performance — rather than just experimenting with prompts.

It directly informs my work in **infrastructure automation and AI operations**, and demonstrates capability that goes beyond classic systems administration.

---

## Relevance to Engineering Roles

This project covers:

- Local model serving and runtime control (Ollama)
- Backend service architecture (FastAPI, SQLite, async Python)
- Frontend integration with streaming (React, Vite, WebSocket)
- Desktop application packaging (Tauri, Rust)
- Tool orchestration and plugin system design
- Privacy-first, self-hosted deployment patterns

---

## Status

Active development. Core features working. Architecture evolving.

---

## Author

**Evgeny Ivanov** — Infrastructure & Automation Engineer  
Almaty, Kazakhstan · [LinkedIn](https://linkedin.com/in/evgeny-ivanov-infra) · [GitHub](https://github.com/johnivanov092-glitch)

> Open to remote international roles in infrastructure, platform engineering, automation, and AI infrastructure.
