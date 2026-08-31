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
        "bom_validate", "converter", "sql", "encrypt", "archiver",
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
_LOCAL_TABULAR_READ_RE = re.compile(
    r"\b(?:pd\.|pandas\.)?(?:read_excel|read_csv)\s*\(|"
    r"\b(?:openpyxl\.)?load_workbook\s*\(",
    re.IGNORECASE,
)
_LOCAL_CATALOG_ABSENCE_RE = re.compile(
    r"(?:\b(?:прайс|каталог|таблиц)\w*\b[^\n.!?]{0,180}"
    r"\b(?:нет|не\s+(?:наш\w*|найд\w*)|отсутств\w*)\b|"
    r"\b(?:нет|не\s+(?:наш\w*|найд\w*)|отсутств\w*)\b"
    r"[^\n.!?]{0,180}\b(?:прайс|каталог|таблиц)\w*\b|"
    r"\b(?:price\s*list|catalog|spreadsheet)\b[^\n.!?]{0,180}"
    r"\b(?:no|not\s+found|absent)\b|"
    r"\b(?:no|not\s+found|absent)\b[^\n.!?]{0,180}"
    r"\b(?:price\s*list|catalog|spreadsheet)\b)",
    re.IGNORECASE | re.UNICODE,
)
_BOM_SOURCE_RE = re.compile(
    r"(?:\b(?:xlsx|xlsm|csv|price\s*list|catalog)\b|прайс\w*|каталог\w*)",
    re.IGNORECASE,
)
_BOM_DELIVERABLE_RE = re.compile(
    r"(?:\bBOM\b|спецификац\w*|коммерческ\w*\s+предлож\w*|"
    r"(?:собер|сборк)\w*[^\n.!?]{0,100}(?:компьютер|пк)|"
    r"(?:компьютер|пк)[^\n.!?]{0,100}(?:собер|сборк)\w*)",
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
    if requires_bom_validation(text):
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


def is_local_tabular_catalog_probe(
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> bool:
    """Detect ad-hoc reads of local tabular data through the Python sandbox.

    Writing a workbook is deliberately excluded: the completion guard is only
    relevant when the model queried local source data before claiming absence.
    """
    if str(tool_name or "").strip().lower() != "sandbox_run":
        return False
    code = str((arguments or {}).get("code") or "")
    return bool(_LOCAL_TABULAR_READ_RE.search(code))


def should_require_local_catalog_search(
    answer: str,
    *,
    local_tabular_probe_seen: bool,
    library_search_seen: bool,
) -> bool:
    """Block one unsupported local-catalog absence conclusion.

    The caller owns the one-shot retry flag. Keeping that state out of this
    predicate makes the matching rule deterministic and easy to regression-test.
    """
    if not local_tabular_probe_seen or library_search_seen:
        return False
    return bool(_LOCAL_CATALOG_ABSENCE_RE.search(str(answer or "")))


def requires_bom_validation(user_message: str) -> bool:
    """Whether a local-catalog request must use deterministic BOM arithmetic."""
    text = str(user_message or "")
    return bool(_BOM_SOURCE_RE.search(text) and _BOM_DELIVERABLE_RE.search(text))


def should_require_web_catalog_fallback(
    user_message: str,
    answer: str,
    *,
    library_search_seen: bool,
    external_source_seen: bool,
) -> bool:
    """Require Web evidence when a mandatory BOM item remains absent locally."""
    if not requires_bom_validation(user_message):
        return False
    if not library_search_seen or external_source_seen:
        return False
    return bool(_LOCAL_CATALOG_ABSENCE_RE.search(str(answer or "")))


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
