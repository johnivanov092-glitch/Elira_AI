"""Run-scoped built-in capability groups selected by the model.

This is prompt composition, not authorization: every tool remains implemented
and executable through the canonical provider/executor path once its schema has
been requested for the current run.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
import re

CORE_BUILTIN_TOOL_ORDER: tuple[str, ...] = (
    "capability_load",
)

CORE_BUILTIN_TOOLS = frozenset(CORE_BUILTIN_TOOL_ORDER)


CAPABILITY_GROUPS: dict[str, frozenset[str]] = {
    "project": frozenset({
        "read_file", "write_file", "edit_file", "glob", "grep",
        "project_map", "todo_update", "delegate_task", "run_bash", "run_server",
    }),
    "runtime": frozenset({"runtime_control"}),
    "web": frozenset({
        "web_search", "web_fetch", "web_query", "http_api", "browser",
    }),
    "desktop": frozenset({"computer"}),
    "resources": frozenset({
        "resource_process", "resource_materialize", "resource_publish",
        "read_image", "file_gen",
    }),
    "memory": frozenset({"recall", "remember"}),
    "math": frozenset({"calc", "unit_convert", "finance_calc", "csv"}),
}


CAPABILITY_GROUP_DESCRIPTIONS: dict[str, str] = {
    "project": "local files, code, shell commands, processes, tests and task planning",
    "runtime": "discover/manage MCP, LSP, SSH, IT Ops, Telegram, Library and user memory",
    "web": "internet search, page reading, HTTP APIs and a JS browser",
    "desktop": "local Windows desktop screenshots, mouse and keyboard control",
    "resources": "attachments, vision, generated DOCX/XLSX/PDF and downloads",
    "memory": "project RAG recall; save durable user facts/corrections only on explicit request, never transient chat preferences",
    "math": "exact calculator, unit conversion, money formulas (invoices/VAT/markup/margin/discounts/loans) and CSV table sums",
}


ALL_BUILTIN_TOOLS = frozenset().union(
    CORE_BUILTIN_TOOLS,
    *CAPABILITY_GROUPS.values(),
)


# Internal domain policies may suggest useful starter schemas. They are not UI
# profiles and never authorize or prohibit a tool. Request evidence signals
# below may add multiple groups when a task crosses domains.
DOMAIN_CAPABILITY_GROUPS: dict[str, frozenset[str]] = {
    "Личный": frozenset({"memory"}),
    "Баланс": frozenset(),
    "Инженерный": frozenset(),  # project/code tools are already in the core
    "Деловой": frozenset({"math"}),
    "Инфраструктура": frozenset({"web"}),
    "Научный": frozenset({"web", "math"}),
    "Медицина": frozenset({"web"}),
}

# Compatibility alias for older imports/tests. The values now describe hidden
# domain policies, not selectable user profiles.
PROFILE_CAPABILITY_GROUPS = DOMAIN_CAPABILITY_GROUPS


_DOWNLOAD_REQUEST_RE = re.compile(
    r"(?:скач\w*|download|дай\s+(?:мне\s+)?файл|"
    r"отдай\s+(?:мне\s+)?файл|файл\w*\s+для\s+скач\w*)",
    re.IGNORECASE,
)
_LINK_REQUEST_RE = re.compile(r"(?:ссылк\w*|\blink\b)", re.IGNORECASE)
_FILE_LINK_REQUEST_RE = re.compile(
    r"(?:ссылк\w*\s+на\s+(?:этот\s+)?файл\b|"
    r"\blink\s+to\s+(?:the\s+)?file\b)",
    re.IGNORECASE,
)
_GENERATED_ARTIFACT_RE = re.compile(
    r"\b(?:создай|создан\w*|сгенер\w*|сформир\w*|подготов\w*|готов\w*|"
    r"create|creat(?:ed|ing)|generat(?:e|ed|ing)|prepare[ds]?)\s+"
    r"[^.!?\n]{0,80}\b(?:pdf|docx|xlsx|pptx|csv|zip|файл\w*|документ\w*|"
    r"отч[её]т\w*|таблиц\w*|презентац\w*|архив\w*|file|document|report)\b",
    re.IGNORECASE,
)
_MODEL_EXTERNAL_ACCESS_DENIAL_RE = re.compile(
    r"(?:у\s+меня\s+(?:нет|отсутствует)\s+(?:прям\w*\s+)?доступ\w*|"
    r"я\s+не\s+(?:имею\s+доступ\w*|могу\s+(?:искать|проверить|получать))|"
    r"\bi\s+(?:do\s+not|don't|cannot|can't)\s+(?:have\s+)?(?:access|browse|search))"
    r"[^.!?\n]{0,160}(?:интернет\w*|веб\w*|актуальн\w*|новост\w*|"
    r"реальн\w*\s+времен\w*|\b(?:internet|web|current|news|real.time)\b)",
    re.IGNORECASE,
)
_EXTERNAL_FAILURE_TOOLS = frozenset({
    "web_fetch", "http_api", "browser", "ssh_run",
    "ssh_run_ps", "itops_mikrotik_inventory", "itops_network_inventory",
})
_EXTERNAL_RUNTIME_PREFIXES = (
    "mcp_", "lsp_", "ssh_", "telegram_", "itops_", "plugin_",
)
_EXTERNAL_FAILURE_RE = re.compile(
    r"(?:mcp|ssh|http|api|routeros|mikrotik|protocol|version|unsupported|"
    r"not\s+found|unknown|connection|timeout|certificate|tls|jinja|template)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RequestCapabilityRoute:
    """Compatibility hints; only the main agent can declare task delivery."""

    domain_policies: tuple[str, ...]
    capability_groups: frozenset[str]
    include_itops: bool
    include_ssh: bool
    download_requested: bool  # Legacy name: a hint, never a completion gate.
    evidence_reasons: tuple[str, ...]
    preflight: dict[str, object] = field(default_factory=dict)


def route_request_capabilities(
    user_message: str,
    *,
    domain_policy: str = "Баланс",
    conversation_history: list[dict[str, object]] | None = None,
) -> RequestCapabilityRoute:
    """Offer compatibility hints; the main agent interprets intent.

    No separate model call or semantic regex router runs here. Qwen receives
    the conversation and chooses tools through the existing capability catalog.
    Legacy domain hints remain accepted for stored callers, never permissions.
    """
    text = str(user_message or "")
    domains = [domain_policy if domain_policy in DOMAIN_CAPABILITY_GROUPS else "Баланс"]
    groups: set[str] = set()
    for domain in domains:
        groups.update(DOMAIN_CAPABILITY_GROUPS.get(domain, ()))

    # A product/source link needs web evidence, not a locally published file.
    # These words can also describe software to build. They may suggest resources,
    # but cannot establish that this chat must receive a downloadable artifact.
    download_requested = bool(
        _DOWNLOAD_REQUEST_RE.search(text)
        or _FILE_LINK_REQUEST_RE.search(text)
        or (_LINK_REQUEST_RE.search(text) and _GENERATED_ARTIFACT_RE.search(text))
    )
    if download_requested:
        groups.add("resources")

    evidence_reasons: list[str] = []
    if any(domain in {"Инфраструктура", "Медицина", "Научный"} for domain in domains):
        evidence_reasons.append("domain_requires_sources")
    if evidence_reasons:
        groups.add("web")

    include_itops = "Инфраструктура" in domains
    return RequestCapabilityRoute(
        domain_policies=tuple(domains),
        capability_groups=normalize_capability_groups(groups),
        include_itops=include_itops,
        include_ssh=include_itops,
        download_requested=download_requested,
        evidence_reasons=tuple(dict.fromkeys(evidence_reasons)),
        preflight={"source": "main_agent"},
    )


def should_escalate_web_after_failure(
    *,
    tool_name: str,
    error: str,
    failure_count: int,
    arguments: dict[str, object] | None = None,
) -> bool:
    """Reveal web evidence after an external or repeated failed attempt."""
    name = str(tool_name or "").strip().lower()
    message = str(error or "")
    if name == "runtime_control":
        operation = str((arguments or {}).get("operation") or "").strip().lower()
        # Local stores remain the source of truth for Library, Memory, Vault,
        # Workflow and Project Corpus errors; Web cannot resolve those states.
        return operation.startswith(_EXTERNAL_RUNTIME_PREFIXES)
    if int(failure_count) >= 2:
        return True
    return name in _EXTERNAL_FAILURE_TOOLS or bool(_EXTERNAL_FAILURE_RE.search(message))


def should_escalate_web_from_answer(answer: str, user_message: str = "") -> bool:
    """Recover an explicit false denial of available web access, once per run.

    General uncertainty is not evidence of a web task. Quoted/code examples
    are data; task-specific evidence requirements remain owned by RunEvidence.
    """
    text = str(answer or "")
    # A verbatim user-supplied quotation remains data even when the model
    # omits its enclosing punctuation in the answer.
    for supplied in re.findall(r'«([^»]+)»|“([^”]+)”|"([^"\n]+)"|`([^`\n]+)`', user_message or ""):
        fragment = next((part for part in supplied if part), "")
        if fragment:
            text = text.replace(fragment, "")
    text = re.sub(r'```[\s\S]*?(?:```|$)|`[^`\n]*`|«[^»]*»|“[^”]*”|"[^"\n]*"', "", text)
    text = re.sub(r"(?m)^\s*>.*$", "", text)
    return bool(_MODEL_EXTERNAL_ACCESS_DENIAL_RE.search(text))


def capability_groups_for_profile(profile_name: str) -> frozenset[str]:
    """Compatibility view of one internal domain policy's starter groups."""
    return DOMAIN_CAPABILITY_GROUPS.get(str(profile_name or "").strip(), frozenset())


def profile_preloads_itops(profile_name: str) -> bool:
    """Compatibility helper for the internal infrastructure policy."""
    return str(profile_name or "").strip() == "Инфраструктура"


def normalize_capability_groups(groups: Collection[str] | None) -> frozenset[str]:
    """Return known normalized group names; stale journal values are ignored."""
    return frozenset(
        name
        for value in groups or ()
        if (name := str(value).strip().lower()) in CAPABILITY_GROUPS
    )


def builtin_tools_for_groups(groups: Collection[str] | None) -> frozenset[str]:
    """Core tool names plus every tool in the selected capability groups."""
    selected = set(CORE_BUILTIN_TOOLS)
    for group in normalize_capability_groups(groups):
        selected.update(CAPABILITY_GROUPS[group])
    return frozenset(selected)


def capability_catalog_text() -> str:
    """Compact model-facing catalog; exact schemas arrive only after loading."""
    return "\n".join(
        f"- {name}: {CAPABILITY_GROUP_DESCRIPTIONS[name]}"
        for name in CAPABILITY_GROUPS
    )
