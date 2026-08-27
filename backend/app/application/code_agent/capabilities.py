"""Run-scoped built-in capability groups selected by the model.

This is prompt composition, not authorization: every tool remains implemented
and executable through the canonical provider/executor path once its schema has
been requested for the current run.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import re


CORE_BUILTIN_TOOL_ORDER: tuple[str, ...] = (
    "capability_load",
    "runtime_control",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "path_exists",
    "project_map",
    "todo_update",
    "delegate_task",
    "run_bash",
    "run_server",
)

CORE_BUILTIN_TOOLS = frozenset(CORE_BUILTIN_TOOL_ORDER)


CAPABILITY_GROUPS: dict[str, frozenset[str]] = {
    "web": frozenset({
        "web_search", "web_fetch", "web_query", "web_sitemap", "http_api",
        "browser", "screenshot",
    }),
    "desktop": frozenset({"computer"}),
    "resources": frozenset({
        "resource_process", "resource_remote_process", "resource_materialize",
        "resource_publish", "read_image", "ocr_file", "file_gen",
    }),
    "data": frozenset({
        "sandbox_run", "sandbox_reset", "translator", "regex", "csv",
        "converter", "sql", "encrypt", "archiver",
    }),
    "memory": frozenset({"recall", "remember"}),
    "operations": frozenset({"reconcile_server_facts", "webhook"}),
}


CAPABILITY_GROUP_DESCRIPTIONS: dict[str, str] = {
    "web": "internet search, page reading, HTTP APIs, JS browser and URL screenshots",
    "desktop": "local Windows desktop screenshots, mouse and keyboard control",
    "resources": "attachments, OCR/vision, generated DOCX/XLSX/PDF and downloads",
    "data": "sandboxed code, regex, CSV, conversion, SQLite, encryption and archives",
    "memory": "project RAG recall and saving durable user facts/corrections",
    "operations": "inference-server facts and webhooks",
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
    "Деловой": frozenset(),
    "Инфраструктура": frozenset({"web"}),
    "Научный": frozenset({"web", "data"}),
    "Медицина": frozenset({"web"}),
}

# Compatibility alias for older imports/tests. The values now describe hidden
# domain policies, not selectable user profiles.
PROFILE_CAPABILITY_GROUPS = DOMAIN_CAPABILITY_GROUPS


_DOWNLOAD_REQUEST_RE = re.compile(
    r"(?:скач\w*|download|дай\s+(?:мне\s+)?(?:ссылк\w*|файл)|"
    r"отдай\s+(?:мне\s+)?файл|файл\w*\s+для\s+скач\w*)",
    re.IGNORECASE,
)
_RESOURCE_REQUEST_RE = re.compile(
    r"(?:\b(?:pdf|docx|xlsx|pptx|csv|zip)\b|документ\w*|презентац\w*|"
    r"таблиц\w*|архив\w*|изображени\w*|картинк\w*|скриншот\w*|ocr|"
    r"распозна\w*\s+текст|сгенер\w*\s+файл)",
    re.IGNORECASE,
)
_DATA_REQUEST_RE = re.compile(
    r"(?:\b(?:csv|xlsx|sql|sqlite|regex|jsonl)\b|таблиц\w*|датасет\w*|"
    r"конверт\w*|зашифр\w*|распак\w*|архив\w*)",
    re.IGNORECASE,
)
_WEB_EVIDENCE_RE = re.compile(
    r"(?:https?://|\bwww\.|интернет\w*|веб[ -]?поиск|web\s*search|"
    r"документац\w*|официальн\w*\s+сайт|актуальн\w*|последн\w*\s+верси|"
    r"сегодня|сейчас\s+(?:стоит|действует|работает)|совместим\w*|"
    r"\b(?:cve|advisory|release notes|latest)\b|"
    r"незнаком\w*|не\s+понима\w*|не\s+получа\w*|неизвестн\w*\s+ошиб)",
    re.IGNORECASE,
)
_EXTERNAL_TECH_RE = re.compile(
    r"(?:\b(?:mcp|routeros|mikromcp|mikrotik|openapi|sdk|api|oauth|tls|"
    r"windows|linux|qwen|llama\.cpp|fastapi|react|vite|npm|pip)\b|"
    r"верси\w*|протокол\w*|интеграц\w*)",
    re.IGNORECASE,
)
_FINANCE_SECURITY_RE = re.compile(
    r"(?:\b(?:cve|cvss|exploit|vulnerabilit|security|advisory|zero[ -]?day|"
    r"курс|валют|акци|облигац|крипт|биткоин|финанс|инвестиц|процентн\w*\s+ставк)\b|"
    r"уязвим\w*|безопасност\w*|бирж\w*|котировк\w*)",
    re.IGNORECASE,
)
_MODEL_UNCERTAINTY_RE = re.compile(
    r"(?:\b(?:не\s+знаю|не\s+уверен|нет\s+данных|неизвестно|"
    r"не\s+получилось|не\s+удалось|не\s+могу\s+определить|"
    r"информация\s+не\s+найдена|cannot\s+determine|unknown|no\s+data)\b)",
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
    """Deterministic preflight result for one Elira request."""

    domain_policies: tuple[str, ...]
    capability_groups: frozenset[str]
    include_itops: bool
    include_ssh: bool
    download_requested: bool
    evidence_reasons: tuple[str, ...]


def route_request_capabilities(
    user_message: str,
    *,
    domain_policy: str = "Баланс",
    conversation_history: list[dict[str, object]] | None = None,
) -> RequestCapabilityRoute:
    """Select starter schemas from the task, never from a UI profile lock.

    The model can still load more groups later. Web is activated up-front when
    the task needs current/external evidence, and after failures through
    ``should_escalate_web_after_failure``.
    """
    from app.application.chat.local_chat import classify_domain_policies

    text = str(user_message or "")
    domains = list(classify_domain_policies(text, conversation_history))
    if domain_policy and domain_policy not in domains and domain_policy != "Баланс":
        domains.append(domain_policy)

    groups: set[str] = set()
    for domain in domains:
        groups.update(DOMAIN_CAPABILITY_GROUPS.get(domain, ()))

    download_requested = bool(_DOWNLOAD_REQUEST_RE.search(text))
    if download_requested or _RESOURCE_REQUEST_RE.search(text):
        groups.add("resources")
    if _DATA_REQUEST_RE.search(text):
        groups.add("data")

    evidence_reasons: list[str] = []
    if _WEB_EVIDENCE_RE.search(text):
        evidence_reasons.append("request_requires_current_or_external_evidence")
    if _EXTERNAL_TECH_RE.search(text) and re.search(
        r"(?:совместим|верси|ошиб|не\s+работ|не\s+получ|как\s+подключ|настро)",
        text,
        re.IGNORECASE,
    ):
        evidence_reasons.append("external_technology_contract")
    if _FINANCE_SECURITY_RE.search(text):
        evidence_reasons.append("finance_or_security_requires_current_sources")
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


def should_escalate_web_from_answer(answer: str) -> bool:
    """Detect an unresolved/uncertain draft before it reaches the user."""
    return bool(_MODEL_UNCERTAINTY_RE.search(str(answer or "")))


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
