"""Adapter that exposes one running MCP server's tools through the
ToolProvider Protocol.

One McpToolProvider instance per server. Tool names are namespaced
as `<server_id>__<original_name>` so two servers can both expose
`search` without colliding inside the agent's tool list.

Conversion rules:

  * MCP inputSchema -> function.parameters: pass through
    unchanged (both use JSON-Schema dialect, both have type/
    properties/required at the top level).
  * MCP tools/call result → tool_meta: MCP returns
        {"content": [{"type": "text", "text": "..."}, ...],
         "isError": bool}
    We flatten text into one blob the LLM can consume, route inline images
    through the existing vision runtime for self-review, and tag the result
    with `mcp_server` for the SSE event.

What this provider exposes to the ToolRegistry:
  * MCP tools only. Resources/prompts are available through McpClient and API
    context routes, not as executable tools.
What we do NOT support yet:
  * Streaming progress notifications during a tool call
  * Server-initiated requests (sampling, roots)
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
from typing import Any

from app.change_executor.policy import (
    creative_batch_contains_arbitrary_code,
    tool_call_is_change,
)
from app.application.tool_providers.mcp_client import McpError
from app.application.tool_providers.mcp_runtime import get_live_client, list_servers


logger = logging.getLogger(__name__)


def _mcp_auto_enable() -> bool:
    """Whether MCP tools should be auto-classified + enabled on discovery.

    MCP servers land in ``data/mcp_servers.json`` because the USER explicitly
    added them, so their tools are trusted-by-configuration here: default ON.
    Kill switch: ELIRA_MCP_AUTO_ENABLE=0 restores the fail-closed default
    (each MCP tool stays blocked until classified via the Tool API). Read live
    so the toggle takes effect on the next MCP sync without a code change.
    """
    return os.getenv("ELIRA_MCP_AUTO_ENABLE", "1").strip().lower() not in {"0", "false", "no", "off"}


# Tool-name prefix delimiter. Two underscores: rare enough in real
# tool names to be a safe sentinel, valid in JSON identifier names
# so most LLM tokenizers handle it well.
_NAMESPACE_DELIM = "__"
_MAX_INLINE_IMAGE_BYTES = 16 * 1024 * 1024
_INLINE_IMAGE_PROMPT = (
    "Describe this MCP-produced image precisely for an agent that is validating "
    "its own work. Report the visible scene or interface, layout, missing or "
    "incorrect elements, clipping, overlap, rendering artifacts, and other "
    "actionable visual defects. Do not guess about content that is not visible."
)
_IMAGE_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}

_BLENDER_BACKUP_CODE = r'''
import bpy, datetime, os, shutil
stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
source = bpy.data.filepath
if source and os.path.isfile(source):
    backup_dir = os.path.join(os.path.dirname(source), ".elira-backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, os.path.basename(source) + "." + stamp + ".blend")
    shutil.copy2(source, backup)
else:
    backup_dir = os.path.join(bpy.app.tempdir, "elira-backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, "unsaved-" + stamp + ".blend")
    bpy.ops.wm.save_as_mainfile(filepath=backup, copy=True)
print("ELIRA_SCENE_BACKUP_OK")
'''.strip()

_UNITY_BACKUP_CODE = r'''
var scene = UnityEditor.SceneManagement.EditorSceneManager.GetActiveScene();
if (!scene.IsValid()) throw new System.Exception("active_scene_invalid");
var dir = "Assets/.elira-backups";
System.IO.Directory.CreateDirectory(dir);
var baseName = string.IsNullOrEmpty(scene.path)
    ? "Untitled"
    : System.IO.Path.GetFileNameWithoutExtension(scene.path);
var stamp = System.DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff");
var backup = dir + "/" + baseName + "." + stamp + ".unity";
if (!UnityEditor.SceneManagement.EditorSceneManager.SaveScene(scene, backup, true))
    throw new System.Exception("scene_backup_failed");
UnityEditor.AssetDatabase.Refresh();
return "ELIRA_SCENE_BACKUP_OK";
'''.strip()

_CREATIVE_BATCH_TO_PROCEDURAL = {
    "blender__batch_edit": "blender__execute_blender_code",
    "unity__batch_execute": "unity__execute_code",
}


def creative_batch_redirect(tool_name: str) -> str:
    procedural = _CREATIVE_BATCH_TO_PROCEDURAL.get(str(tool_name or ""), "")
    if not procedural:
        return ""
    return (
        f"Последовательность мелких batch-вызовов исчерпана. Активируй и вызови "
        f"{procedural} ОТДЕЛЬНО одним идемпотентным процедурным шагом; не вкладывай "
        "произвольный код внутрь batch. Затем проверь screenshot и сделай максимум "
        "одну коррекцию."
    )


def creative_procedural_companion(tool_name: str) -> str:
    return _CREATIVE_BATCH_TO_PROCEDURAL.get(str(tool_name or ""), "")


def creative_workflow_prompt(tool_names: set[str] | list[str] | tuple[str, ...]) -> str:
    names = {str(name) for name in tool_names}
    has_blender = any(name.startswith("blender__") for name in names)
    has_unity = any(name.startswith("unity__") for name in names)
    if not has_blender and not has_unity:
        return ""
    editors = "Blender и Unity" if has_blender and has_unity else ("Blender" if has_blender else "Unity")
    return f"""
## Процедурная работа в {editors}
- Работай через MCP открытого редактора, не через PowerShell и не через второй runtime.
- Контур обязателен: inspect текущей сцены → изменение → screenshot с vision-проверкой → максимум одна коррекция → явное сохранение сцены/рендера.
- Простую правку делай dedicated tool; несколько однотипных правок объединяй максимум в три последовательных batch-вызова.
- Если нужны циклы, процедурная расстановка, массовое выравнивание или сложная математика — сразу вызывай отдельный procedural tool (`blender__execute_blender_code` / `unity__execute_code`) вместо десятков batch.
- Никогда не вкладывай execute_code/execute_blender_code внутрь batch: runtime это блокирует. Произвольный код должен быть отдельным видимым вызовом с одним approval; runtime перед ним делает резервную копию сцены и после него получает screenshot.
- После vision-описания исправь только конкретный видимый дефект и проверь повторно; максимум одна коррекция, затем честно заверши или сообщи блокер.
""".strip()


def _augment_creative_description(server_id: str, tool_name: str, description: str) -> str:
    qualified = _qualify(server_id, tool_name)
    if qualified in _CREATIVE_BATCH_TO_PROCEDURAL:
        return (
            f"{description}\n\nELIRA WORKFLOW: use at most three consecutive batches. "
            f"For loops/procedural placement switch to "
            f"{_CREATIVE_BATCH_TO_PROCEDURAL[qualified]}. Never nest arbitrary code in a batch."
        ).strip()
    if qualified in _CREATIVE_BATCH_TO_PROCEDURAL.values():
        return (
            f"{description}\n\nELIRA WORKFLOW: call this as a separate visible tool, never "
            "inside a batch. The runtime creates a scene backup and requests a visual post-check."
        ).strip()
    return description


def _raw_mcp_failed(result: dict[str, Any]) -> bool:
    return bool((result or {}).get("isError"))


def _merge_mcp_content(primary: dict[str, Any], postcheck: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary or {})
    content = list(merged.get("content") or [])
    if _raw_mcp_failed(postcheck):
        content.append({
            "type": "text",
            "text": "[visual post-check failed; call the screenshot tool explicitly]",
        })
    else:
        content.extend(list((postcheck or {}).get("content") or []))
    merged["content"] = content
    return merged


def _qualify(server_id: str, tool_name: str) -> str:
    return f"{server_id}{_NAMESPACE_DELIM}{tool_name}"


def _unqualify(qualified: str, server_id: str) -> str:
    prefix = f"{server_id}{_NAMESPACE_DELIM}"
    if qualified.startswith(prefix):
        return qualified[len(prefix):]
    return qualified


def _describe_inline_image(chunk: dict[str, Any], image_index: int) -> str:
    """Validate and describe one MCP ImageContent without exposing its payload."""
    mime_type = str(chunk.get("mimeType") or chunk.get("mime_type") or "").strip().lower()
    suffix = _IMAGE_SUFFIXES.get(mime_type)
    encoded = chunk.get("data")
    if suffix is None or not isinstance(encoded, str) or not encoded:
        return "[MCP image unavailable: invalid image content]"
    # Reject oversized input before allocating decoded bytes. Four base64 chars
    # represent at most three bytes; the allowance covers terminal padding.
    max_encoded = ((_MAX_INLINE_IMAGE_BYTES + 2) // 3) * 4 + 4
    if len(encoded) > max_encoded:
        return "[MCP image unavailable: image exceeds the 16 MiB limit]"
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return "[MCP image unavailable: invalid base64 data]"
    if not image_bytes or len(image_bytes) > _MAX_INLINE_IMAGE_BYTES:
        return "[MCP image unavailable: invalid image size]"

    try:
        from app.infrastructure.llm.vision_ocr import describe_image, is_vision_enabled
    except Exception:  # pragma: no cover - optional runtime import guard
        return "[MCP image received, but vision support is unavailable]"
    if not is_vision_enabled():
        return "[MCP image received, but vision is disabled]"
    try:
        description = describe_image(
            f"mcp-capture-{image_index}{suffix}",
            image_bytes,
            prompt=_INLINE_IMAGE_PROMPT,
        )
    except Exception:  # noqa: BLE001 - provider details must not cross the tool boundary
        description = None
    if not description:
        return "[MCP image received, but vision returned no description]"
    return f"MCP image description:\n{description}"


def _flatten_mcp_result(result: dict[str, Any]) -> dict[str, Any]:
    """Turn `tools/call` content into text, describing inline images via vision."""
    is_error = bool(result.get("isError"))
    parts: list[str] = []
    image_index = 0
    for chunk in result.get("content", []) or []:
        if not isinstance(chunk, dict):
            continue
        kind = chunk.get("type")
        if kind == "text":
            text_val = chunk.get("text")
            if isinstance(text_val, str):
                parts.append(text_val)
        elif kind == "image":
            image_index += 1
            parts.append(_describe_inline_image(chunk, image_index))
        elif kind == "resource":
            parts.append("[MCP binary resource omitted]")
    body = "\n".join(parts) if parts else ""
    if is_error:
        return {
            "ok": False,
            "error": "mcp_tool_failed",
            "text": f"ERROR (mcp): {body or 'unknown error'}",
        }
    return {"ok": True, "text": body}


class McpToolProvider:
    """One MCP server, presented as a ToolProvider.

    Cached state:
      * The qualified-name → original-name map (built once on
        construction by calling tools/list).
      * The pre-converted function schema list.

    If the server isn't currently running, `is_enabled()` returns
    False and the provider contributes nothing to the registry —
    the registry skips it just like a disabled built-in provider.
    """

    def __init__(self, server_id: str) -> None:
        self.name = f"mcp:{server_id}"
        self._server_id = server_id
        self._schemas: list[dict[str, Any]] = []
        self._owned: set[str] = set()
        self._qualified_to_original: dict[str, str] = {}
        self._populated = False

    # ── ToolProvider ────────────────────────────────────────────

    def is_enabled(self) -> bool:
        return get_live_client(self._server_id) is not None

    def get_schemas(self) -> list[dict[str, Any]]:
        # Populate lazily so we can recover after a restart.
        if not self._populated:
            self._refresh_schemas()
        return self._schemas

    def owns(self, tool_name: str) -> bool:
        if not self._populated:
            self._refresh_schemas()
        return tool_name in self._owned

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        client = get_live_client(self._server_id)
        if client is None:
            return {
                "ok": False,
                "error": "mcp_server_not_running",
                "text": f"ERROR: mcp server '{self._server_id}' is not running",
            }
        if tool_name not in self._qualified_to_original:
            # Schemas may have been built when a different set of
            # tools was advertised; try once to refresh.
            self._refresh_schemas()
        original_name = self._qualified_to_original.get(tool_name)
        if original_name is None:
            return {
                "ok": False,
                "error": "mcp_tool_not_found",
                "text": f"ERROR: tool '{tool_name}' not found on server '{self._server_id}'",
            }
        if (
            self._server_id == "unity"
            and original_name == "batch_execute"
            and creative_batch_contains_arbitrary_code(args)
        ):
            return {
                "ok": False,
                "error": "nested_execute_code_forbidden",
                "text": (
                    "ERROR: nested_execute_code_forbidden. Call unity__execute_code "
                    "as a separate visible tool so backup and approval cannot be bypassed."
                ),
                "mcp_server": self._server_id,
            }
        try:
            safe_args = dict(args or {})
            needs_blender_backup = (
                self._server_id == "blender" and original_name == "execute_blender_code"
            )
            unity_action = str(safe_args.get("action") or "").strip().casefold()
            needs_unity_backup = (
                self._server_id == "unity"
                and original_name == "execute_code"
                and unity_action in {"execute", "replay"}
            )
            if needs_blender_backup:
                backup_result = client.call_tool(original_name, {
                    "code": _BLENDER_BACKUP_CODE,
                    "return_screenshot": False,
                })
                if _raw_mcp_failed(backup_result):
                    return {
                        "ok": False,
                        "error": "creative_scene_backup_failed",
                        "text": "ERROR: creative_scene_backup_failed; procedural code was not executed.",
                        "mcp_server": self._server_id,
                    }
                safe_args["return_screenshot"] = True
            elif needs_unity_backup:
                backup_result = client.call_tool("execute_code", {
                    "action": "execute",
                    "code": _UNITY_BACKUP_CODE,
                    "safety_checks": True,
                })
                if _raw_mcp_failed(backup_result):
                    return {
                        "ok": False,
                        "error": "creative_scene_backup_failed",
                        "text": "ERROR: creative_scene_backup_failed; procedural code was not executed.",
                        "mcp_server": self._server_id,
                    }

            raw_result = client.call_tool(original_name, safe_args)
            if needs_unity_backup and not _raw_mcp_failed(raw_result):
                screenshot = client.call_tool("manage_camera", {
                    "action": "screenshot",
                    "capture_source": "scene_view",
                    "include_image": True,
                    "screenshot_file_name": "elira-procedural-review.png",
                })
                raw_result = _merge_mcp_content(raw_result, screenshot)
        except McpError as exc:
            return {"ok": False, "error": "mcp_call_failed", "text": f"ERROR: {exc}"}
        except Exception as exc:
            logger.exception("mcp dispatch %s failed", tool_name)
            return {"ok": False, "error": "mcp_call_failed", "text": f"ERROR: {exc}"}
        meta = _flatten_mcp_result(raw_result)
        meta["mcp_server"] = self._server_id
        if meta.get("ok") and tool_call_is_change(tool_name, safe_args):
            # Trusted provider-level mutation proof for non-filesystem editors.
            # The agent's generic progress tracker consumes this instead of a fake
            # touched_path, so scene edits count as progress without pretending a
            # project file path was written.
            meta["state_changed"] = True
        return meta

    # ── Internals ───────────────────────────────────────────────

    def _refresh_schemas(self) -> None:
        client = get_live_client(self._server_id)
        if client is None:
            self._schemas = []
            self._owned = set()
            self._qualified_to_original = {}
            self._populated = True
            return
        try:
            tools = client.list_tools()
        except McpError as exc:
            logger.warning("mcp tools/list on %r failed: %s", self._server_id, exc)
            self._schemas = []
            self._owned = set()
            self._qualified_to_original = {}
            self._populated = True
            return

        schemas: list[dict[str, Any]] = []
        mapping: dict[str, str] = {}
        for tool in tools:
            original_name = tool.get("name")
            if not isinstance(original_name, str) or not original_name:
                continue
            qualified = _qualify(self._server_id, original_name)
            description = _augment_creative_description(
                self._server_id,
                original_name,
                str(tool.get("description") or ""),
            )
            input_schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
            schemas.append({
                "type": "function",
                "function": {
                    "name": qualified,
                    "description": (
                        f"[mcp:{self._server_id}] {description}".strip()
                        if description
                        else f"[mcp:{self._server_id}] (no description)"
                    ),
                    "parameters": input_schema if isinstance(input_schema, dict) else {"type": "object"},
                },
            })
            mapping[qualified] = original_name

        self._schemas = schemas
        self._owned = set(mapping.keys())
        self._qualified_to_original = mapping
        self._populated = True


def _mcp_noop_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Placeholder handler for an MCP ToolSpec row.

    MCP dispatch goes through McpToolProvider, never through the registry handler;
    this exists only so the registry has a callable bound to the spec.
    """
    return {"ok": False, "text": "ERROR: MCP tool — dispatch via MCP provider, not tool_registry"}


def sync_mcp_tool_specs(providers: list["McpToolProvider"]) -> None:
    """Mirror running MCP servers' tools into the Tool Registry, fail-closed.

    Each MCP tool gets a DB ToolSpec. It is registered fail-closed (forbidden +
    disabled + policy_classified=0), then — unless ELIRA_MCP_AUTO_ENABLE=0 — the
    post-register sweep flips still-unclassified live rows to enabled +
    policy_classified + permission='require_approval' (MCP servers are user-added,
    so trusted-by-configuration; require_approval keeps the approval prompt in ask
    mode, bypass runs straight through). Re-sync preserves admin policy: the sweep
    only touches unclassified rows, so a MANUAL disable (which keeps
    policy_classified=1) is never re-enabled. MCP-source specs whose tool is no
    longer advertised by any running server are DISABLED (stale → off, never
    deleted, so classification/audit survives a transient outage).

    Defensive throughout: a registry or network hiccup must never break provider
    construction or the agent loop.
    """
    try:
        import app.application.tool_registry.runtime as _tr
    except Exception:
        return

    live: set[str] = set()
    for provider in providers:
        try:
            schemas = provider.get_schemas()
        except Exception:
            continue
        for schema in schemas:
            fn = schema.get("function") if isinstance(schema, dict) else None
            if not isinstance(fn, dict):
                continue
            qname = fn.get("name")
            if not isinstance(qname, str) or not qname:
                continue
            params = fn.get("parameters")
            live.add(qname)
            try:
                _tr.register_dynamic_tool(
                    qname,
                    _mcp_noop_handler,
                    display_name=qname,
                    description=str(fn.get("description") or ""),
                    category="mcp",
                    parameters_schema=params if isinstance(params, dict) else {},
                    source="mcp",
                    side_effect=True,
                    scopes=[],
                    timeout_seconds=60,
                )
            except Exception as exc:
                logger.warning("mcp spec sync for %r failed: %s", qname, exc)

    auto_enable = _mcp_auto_enable()
    try:
        for tool in _tr.list_tools_with_schemas(source="mcp", enabled_only=False):
            tname = tool.get("name")
            if not tname:
                continue
            if tname not in live:
                # No running server advertises this tool anymore → stale, disable
                # it (never delete — classification/audit survives a transient
                # server outage and re-enables on next sync).
                if tool.get("enabled"):
                    _tr.update_tool(tname, {"enabled": False})
                continue
            # Live MCP tool still at the fail-closed default (unclassified) →
            # auto-enable it: classify + enable + require_approval (still gated
            # by the approval prompt in ask mode; runs straight through in
            # bypass). We flip ONLY still-unclassified rows, so a later MANUAL
            # disable (which keeps policy_classified=1) is never clobbered.
            if auto_enable and not tool.get("policy_classified"):
                _tr.update_tool(tname, {
                    "enabled": True,
                    "permission": "require_approval",
                    "policy_classified": True,
                })
    except Exception as exc:
        logger.warning("mcp spec sweep failed: %s", exc)


def build_mcp_providers() -> list[McpToolProvider]:
    """Construct a provider per **running** MCP server.

    Called once at agent_loop start. Servers that aren't running
    are skipped here so the registry doesn't get providers that
    contribute zero schemas — that's just noise."""
    providers: list[McpToolProvider] = []
    for spec in list_servers():
        if spec.get("status") != "running":
            continue
        providers.append(McpToolProvider(spec["id"]))
    # P9.2-FIXUP: mirror discovered MCP tools into the registry fail-closed so the
    # unified kernel can enforce policy on them (forbidden/disabled/unclassified by
    # default; admin classification required to run).
    sync_mcp_tool_specs(providers)
    return providers
