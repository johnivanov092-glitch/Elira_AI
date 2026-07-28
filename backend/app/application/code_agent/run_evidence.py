from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


class EvidenceKind(str, Enum):
    OBSERVATION = "observation"
    MUTATION = "mutation"
    VERIFICATION = "verification"
    ARTIFACT = "artifact"
    EXTERNAL_SOURCE = "external_source"


@dataclass(frozen=True)
class EvidenceReceipt:
    kind: EvidenceKind
    tool_name: str
    project_epoch: int
    passed: bool
    target: str = ""


_OBSERVATION_TOOLS = frozenset({
    "browser",
    "glob",
    "http_api",
    "paper_search",
    "path_exists",
    "project_map",
    "read_file",
    "search_files",
    "ssh_exists",
    "ssh_list_hosts",
    "ssh_not_exists",
    "ssh_read",
    "web_fetch",
    "web_query",
})
_VERIFICATION_TOOLS = frozenset({
    "browser",
    "path_exists",
    "run_bash",
    "run_server",
    "ssh_assert_contains",
    "ssh_assert_not_contains",
    "ssh_exists",
    "ssh_not_exists",
    "ssh_port_check",
    "ssh_read",
    "ssh_run",
    "ssh_run_ps",
})
_EXTERNAL_SOURCE_TOOLS = frozenset({
    "browser",
    "http_api",
    "paper_search",
    "web_claim_add",
    "web_fetch",
    "web_query",
})
_GROUNDING_FRAGMENT_LIMIT = 64
_GROUNDING_FRAGMENT_CHARS = 16_000

_EXTERNAL_FACT_INTENT_RE = re.compile(
    r"(?:"
    r"\b(?:интернет\w*|веб\w*|источник\w*|ссылк\w*|официальн\w+\s+"
    r"(?:сайт|данн\w*|реестр\w*))\b|"
    r"\b(?:проверь|проверить|перепроверь|перепроверить|найди|найти|узнай|узнать|"
    r"поищи|поискать)\b.{0,80}\b(?:информац\w*|данн\w*|факт\w*|источник\w*|"
    r"сайт\w*|интернет\w*|веб\w*|новост\w*|цен\w*|курс\w*)\b|"
    r"\b(?:актуальн\w*|последн\w*|текущ\w*|сегодняшн\w*)\b.{0,40}"
    r"\b(?:верси\w*|новост\w*|цен\w*|стоимост\w*|курс\w*|закон\w*|событи\w*)\b|"
    r"\b(?:курс\s+валют\w*|цена\s+(?:акци\w*|товар\w*|нефт\w*)|котировк\w*)\b|"
    r"(?:кто|кем).{0,40}(?:сдела\w*|созда\w*|основа\w*|разработа\w*|"
    r"принадлеж\w*|владе\w*|руковод\w*)|"
    r"\b(?:основател\w*|учредител\w*|владелец|владельц\w*|директор\w*|"
    r"руководител\w*|биограф\w*)\b|"
    r"(?:когда|где).{0,40}(?:создан\w*|основан\w*|родил\w*|произош\w*|выш\w*)|"
    r"\b(?:беременн\w*|плацент\w*|кровотеч\w*|диагноз\w*|лечени\w*|"
    r"лекарств\w*|дозиров\w*|симптом\w*|медицин\w*|юридическ\w*|"
    r"закон\w*|налог\w*|инвестиц\w*|кредит\w*|страхов\w*|"
    r"недвижимост\w*|наводнен\w*|затаплива\w*)\b|"
    r"\b(?:fact[- ]?check|web\s+search|latest\s+(?:version|news|price)|"
    r"current\s+(?:price|rate|law)|official\s+source|"
    r"founder|owner|director|price|medical|legal)\b"
    r")",
    re.IGNORECASE | re.UNICODE,
)
_EXTERNAL_AUTHORITY_CLAIM_RE = re.compile(
    r"(?:проверил\w*.{0,50}(?:официальн\w*|источник\w*|сайт\w*|реестр\w*)|"
    r"по\s+официальн\w+\s+данн\w*|согласно\s+(?:источник\w*|данн\w*|реестр\w*)|"
    r"(?:official|verified)\s+(?:source|data))",
    re.IGNORECASE | re.UNICODE,
)
_EXTERNAL_UNCERTAINTY_RE = re.compile(
    r"(?:не\s+(?:подтвердил\w*|подтвержден\w*|наш[её]л\w*|знаю|удалось\s+"
    r"(?:найти|проверить|подтвердить))|источник\s+не\s+найден|"
    r"не\s+могу\s+(?:достоверно\s+)?подтвердить|requires?\s+verification|"
    r"not\s+(?:verified|confirmed))",
    re.IGNORECASE | re.UNICODE,
)
_LOCAL_FACT_CONTEXT_RE = re.compile(
    r"\b(?:файл\w*|код\w*|класс\w*|функци\w*|компонент\w*|коммит\w*|"
    r"ветк\w*|репозитор\w*|проект\w*|сервер\w*|хост\w*|ssh|папк\w*|"
    r"каталог\w*|лог\w*|баз\w+\s+данн\w*|file|code|class|function|"
    r"commit|repository|project|server|host)\b",
    re.IGNORECASE | re.UNICODE,
)
_EXPLICIT_EXTERNAL_CONTEXT_RE = re.compile(
    r"\b(?:интернет\w*|веб\w*|источник\w*|ссылк\w*|официальн\w*|"
    r"новост\w*|курс\w*|цен\w*|fact[- ]?check|web\s+search|"
    r"official\s+source)\b",
    re.IGNORECASE | re.UNICODE,
)

_ANSWER_FILE_RE = re.compile(
    r"[\w.\-/\\]+\.(?:py|js|ts|tsx|jsx|json|md|txt|ya?ml|toml|cfg|ini|sh|bash|"
    r"rs|go|java|kt|c|cpp|h|hpp|sql|html|css|scss|env|lock|rsc|conf|service)\b",
    re.IGNORECASE,
)
_ANSWER_DOC_RE = re.compile(r"[\w.\-/\\]+\.(?:docx|xlsx|pdf)\b", re.IGNORECASE)
_DOCGEN_READY_RE = re.compile(
    r"(?:\bвот\b|\bготов(?:о|а|ы)?\b|\bсоздан(?:о|а|ы)?\b|"
    r"\bсгенерирован(?:о|а|ы)?\b|\bподготовлен(?:о|а|ы)?\b|"
    r"\bсохран(?:ен|ён)(?:о|а|ы)?\b|\bскачать\b|"
    r"\b(?:here(?:'s| is)|ready|created|generated|saved|download)\b)",
    re.IGNORECASE,
)
_DOCGEN_EXISTING_RE = re.compile(
    r"(?:\bсуществу\w*\b|\bнаход\w*\b|\bнайден\w*\b|\bимеется\b|"
    r"\bна месте\b|\bпредыдущ\w*\s+прогон\w*\b|\bранее\b|"
    r"\bдо этого\b|\b(?:already existed|previous run|exists|found)\b|"
    r"\bне\s+(?:был[оаи]?\s+)?(?:создан|сгенерирован|подготовлен|сохран[её]н)\w*\b)",
    re.IGNORECASE,
)


def _basename(path: str) -> str:
    return re.split(r"[\\/]", path)[-1]


def _doc_claim_context(answer: str, start: int, end: int) -> str:
    left = 0
    for marker in ("\n", "! ", "? ", ". "):
        pos = answer.rfind(marker, 0, start)
        if pos >= 0:
            left = max(left, pos + len(marker))
    right = len(answer)
    for marker in ("\n", "! ", "? ", ". "):
        pos = answer.find(marker, end)
        if pos >= 0:
            right = min(right, pos)
    return answer[left:right]


def requires_external_source(user_message: str, answer: str = "") -> bool:
    request = user_message or ""
    if (
        _LOCAL_FACT_CONTEXT_RE.search(request)
        and not _EXPLICIT_EXTERNAL_CONTEXT_RE.search(request)
    ):
        return bool(_EXTERNAL_AUTHORITY_CLAIM_RE.search(answer or ""))
    return bool(
        _EXTERNAL_FACT_INTENT_RE.search(request)
        or _EXTERNAL_AUTHORITY_CLAIM_RE.search(answer or "")
    )


def answer_admits_missing_external_source(answer: str) -> bool:
    return bool(_EXTERNAL_UNCERTAINTY_RE.search(answer or ""))


def tool_provides_external_source(tool_name: str, text_result: str) -> bool:
    tool = str(tool_name or "").strip()
    if not str(text_result or "").strip():
        return False
    if tool in _EXTERNAL_SOURCE_TOOLS:
        return True
    return tool.startswith("playwright__") and tool.rsplit("__", 1)[-1] in {
        "browser_snapshot",
        "browser_network_requests",
    }


def external_source_backstop() -> str:
    return (
        "Не могу подтвердить фактический ответ: в этом прогоне не был успешно "
        "прочитан внешний источник. Я не буду выдавать сведения по памяти за "
        "проверенные. Нужен успешный `web_search` → `web_fetch` либо честный ответ "
        "«источник не найден / не подтверждено»."
    )


def ungrounded_file_claims(
    answer: str,
    messages: Iterable[dict[str, Any]],
    established_facts: Iterable[str],
    observed_fragments: Iterable[str] = (),
) -> list[str]:
    claimed = {_basename(m.group(0)).lower() for m in _ANSWER_FILE_RE.finditer(answer or "")}
    if not claimed:
        return []
    parts = [str(item) for item in established_facts]
    parts.extend(str(item) for item in observed_fragments)
    for message in list(messages)[1:]:
        if isinstance(message, dict) and message.get("role") in ("tool", "user", "system"):
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
    haystack = " ".join(parts).lower()
    return sorted({name for name in claimed if name not in haystack})


def unbacked_document_claims(answer: str, generated_documents: Iterable[str]) -> list[str]:
    claimed: set[str] = set()
    for match in _ANSWER_DOC_RE.finditer(answer or ""):
        context = _doc_claim_context(answer, match.start(), match.end())
        if _DOCGEN_READY_RE.search(context) and not _DOCGEN_EXISTING_RE.search(context):
            claimed.add(_basename(match.group(0)).lower())
    produced = {_basename(path).lower() for path in generated_documents}
    return sorted(claimed - produced)


class RunEvidence:
    """Run-local evidence ledger.

    Receipts are accepted only from executed tool results. A confirmed mutation
    advances ``project_epoch``; verification receipts prove only the epoch at
    which they were captured, so a later edit invalidates an earlier green run.
    """

    def __init__(self) -> None:
        self._project_epoch = 0
        self._receipts: list[EvidenceReceipt] = []
        self._generated_documents: set[str] = set()
        self._remote_hosts: set[str] = set()
        self._grounding_fragments: list[str] = []

    @property
    def project_epoch(self) -> int:
        return self._project_epoch

    @property
    def has_mutations(self) -> bool:
        return bool(self.receipts_of_kind(EvidenceKind.MUTATION))

    @property
    def has_current_verification(self) -> bool:
        return any(
            receipt.kind is EvidenceKind.VERIFICATION
            and receipt.project_epoch == self._project_epoch
            for receipt in self._receipts
        )

    @property
    def has_current_passing_verification(self) -> bool:
        return any(
            receipt.kind is EvidenceKind.VERIFICATION
            and receipt.project_epoch == self._project_epoch
            and receipt.passed
            for receipt in self._receipts
        )

    @property
    def has_external_source(self) -> bool:
        return bool(self.receipts_of_kind(EvidenceKind.EXTERNAL_SOURCE))

    @property
    def remote_hosts(self) -> tuple[str, ...]:
        return tuple(sorted(self._remote_hosts))

    @property
    def generated_documents(self) -> tuple[str, ...]:
        return tuple(sorted(self._generated_documents))

    def receipts_of_kind(self, kind: EvidenceKind) -> tuple[EvidenceReceipt, ...]:
        return tuple(receipt for receipt in self._receipts if receipt.kind is kind)

    @staticmethod
    def requires_external_source(user_message: str, answer: str = "") -> bool:
        return requires_external_source(user_message, answer)

    @staticmethod
    def is_verification_tool(tool_name: str) -> bool:
        tool = str(tool_name or "").strip()
        return tool in _VERIFICATION_TOOLS or tool.startswith("playwright__")

    @staticmethod
    def answer_admits_missing_external_source(answer: str) -> bool:
        return answer_admits_missing_external_source(answer)

    def ungrounded_file_claims(
        self,
        answer: str,
        messages: Iterable[dict[str, Any]],
        established_facts: Iterable[str],
    ) -> list[str]:
        return ungrounded_file_claims(
            answer,
            messages,
            established_facts,
            self._grounding_fragments,
        )

    def unbacked_document_claims(self, answer: str) -> list[str]:
        return unbacked_document_claims(answer, self._generated_documents)

    def record_tool_result(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        execution_status: str,
        output: dict[str, Any],
        text_result: str,
        state_changed: bool,
    ) -> None:
        tool = str(tool_name or "").strip()
        if execution_status != "ok":
            return

        provider_ok = output.get("ok") is not False
        target = self._target(arguments, output)
        passed = self._passed(output)

        if provider_ok and state_changed:
            self._project_epoch += 1
            self._receipts.append(EvidenceReceipt(
                EvidenceKind.MUTATION,
                tool,
                self._project_epoch,
                True,
                target,
            ))

        if provider_ok and self._is_observation_tool(tool):
            self._receipts.append(EvidenceReceipt(
                EvidenceKind.OBSERVATION,
                tool,
                self._project_epoch,
                passed,
                target,
            ))
            self._remember_grounding(arguments, text_result)

        if self._is_verification_tool(tool, output):
            self._receipts.append(EvidenceReceipt(
                EvidenceKind.VERIFICATION,
                tool,
                self._project_epoch,
                passed,
                target,
            ))

        if (
            provider_ok
            and tool == "file_gen"
            and str(output.get("download_name") or "").strip()
        ):
            name = str(output["download_name"]).strip()
            self._generated_documents.add(name)
            self._receipts.append(EvidenceReceipt(
                EvidenceKind.ARTIFACT,
                tool,
                self._project_epoch,
                True,
                _basename(name),
            ))

        if provider_ok and tool_provides_external_source(tool, text_result):
            self._receipts.append(EvidenceReceipt(
                EvidenceKind.EXTERNAL_SOURCE,
                tool,
                self._project_epoch,
                True,
                target,
            ))

        if provider_ok and tool.startswith("ssh_"):
            host = str(arguments.get("host") or "").strip()
            if host:
                self._remote_hosts.add(host)

    def summary(self) -> dict[str, int | bool]:
        return {
            "project_epoch": self._project_epoch,
            "mutations": len(self.receipts_of_kind(EvidenceKind.MUTATION)),
            "observations": len(self.receipts_of_kind(EvidenceKind.OBSERVATION)),
            "verifications": len(self.receipts_of_kind(EvidenceKind.VERIFICATION)),
            "artifacts": len(self.receipts_of_kind(EvidenceKind.ARTIFACT)),
            "external_sources": len(self.receipts_of_kind(EvidenceKind.EXTERNAL_SOURCE)),
            "current_verification": self.has_current_verification,
            "current_passing_verification": self.has_current_passing_verification,
        }

    @staticmethod
    def _is_observation_tool(tool: str) -> bool:
        return tool in _OBSERVATION_TOOLS or tool.startswith("playwright__")

    @staticmethod
    def _is_verification_tool(tool: str, output: dict[str, Any]) -> bool:
        return output.get("verifier") is True or RunEvidence.is_verification_tool(tool)

    @staticmethod
    def _passed(output: dict[str, Any]) -> bool:
        if output.get("ok") is False:
            return False
        exit_code = output.get("exit_code")
        if exit_code is not None:
            try:
                return int(exit_code) == 0
            except (TypeError, ValueError):
                return False
        return True

    @staticmethod
    def _target(arguments: dict[str, Any], output: dict[str, Any]) -> str:
        for key in ("touched_path", "project_path", "download_name"):
            value = str(output.get(key) or "").strip()
            if value:
                return value
        for key in ("path", "host", "url", "command"):
            value = str(arguments.get(key) or "").strip()
            if value:
                return value[:512]
        return ""

    def _remember_grounding(self, arguments: dict[str, Any], text_result: str) -> None:
        fragments = [
            str(arguments.get(key) or "").strip()
            for key in ("path", "host", "url", "query", "pattern")
        ]
        fragments.append(str(text_result or "")[:_GROUNDING_FRAGMENT_CHARS])
        joined = "\n".join(fragment for fragment in fragments if fragment)
        if not joined:
            return
        self._grounding_fragments.append(joined)
        if len(self._grounding_fragments) > _GROUNDING_FRAGMENT_LIMIT:
            del self._grounding_fragments[:-_GROUNDING_FRAGMENT_LIMIT]
