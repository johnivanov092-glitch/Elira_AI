# Agent Boundary Review

## Target

Elira uses one UI shell with two isolated runtime modes:

- Chat Agent: read-only workspace for chats, project/document analysis, uploads,
  memory, and generated artifacts.
- Code Agent: full coding executor with sessions, indexing, shell, file edits,
  sandbox, approvals, SSH, MCP, and tool loops.

## Comparison With claw-code-main

`D:\Elira_AI_Packs\claw-code-main` is useful as an architectural reference, not
as code to port. The important patterns are:

- explicit runtime loop;
- session object as the unit of execution;
- tool registry behind a permission policy;
- system prompt builder tied to the selected workspace;
- isolated workspace context passed into the agent runtime.

The matching Elira issue was that ordinary chat could drift into shared planner,
global project state, smart/RAG memory, Agent OS, multi-agent orchestration, and
generic tool/runtime paths. That made "Chat" and "Code" look like UI tabs while
sharing too much runtime behavior underneath.

## Refactor Boundary

Primary Chat Agent API:

- `/api/chat-agent/*`
- state DB: `data/chat_agent.db`
- default workspace: `data/chat_agent_workspace/`
- project source: Chat sidebar Projects only
- memory/history/settings: Chat Agent tables only

Primary Code Agent API:

- `/api/code-agent/*`
- code workspace root remains owned by `CodeWorkspaceShell`
- code sessions, indexer, sandbox, approvals, MCP, SSH, and tool registry remain
  Code Agent only

Legacy `/api/chat/*` remains compatibility surface. The normal Chat UI must not
call it for the stable Chat Agent path.

## Rules

- Chat Agent may list/read/search the selected Chat project and uploaded
  documents.
- Chat Agent may create document/artifact outputs only inside its own workspace.
- Chat Agent must not expose shell, code write/edit, sandbox, SSH, MCP, Code
  Agent tool registry, `tool_search`, or Code Agent project state.
- Empty Chat projects are reported as empty; Chat Agent does not suggest code
  scaffolding unless explicitly asked.
- Code Agent remains the only runtime that can modify projects or execute
  commands.
