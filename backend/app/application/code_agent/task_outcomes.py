"""Run-owned facts about the task: the user's contract, file publications, answer checks.

No request classifier or executor lives here. RunJournal owns durable state.
The model declares nothing here (task_decide/result_verify were removed on
John's decision 2026-10-07): facts come from tools and from the user's message.
"""
from __future__ import annotations
from elira_common.files import sha256_file as file_digest

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote


logger = logging.getLogger(__name__)


_CODE_SUFFIXES = {".py", ".ps1", ".sh", ".bash", ".bat", ".cmd", ".js", ".mjs", ".cjs",
                  ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".cs", ".cpp", ".c", ".sql"}




def _path(root: Path, value: Any, *, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} needs non-empty local file paths")
    # Single-letter prefixes are Windows drives; colon-only POSIX names are valid.
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]+://", value.lstrip()):
        raise ValueError(
            f"{field} accepts local file paths, not URI/URL values. "
            "Keep source URLs in source evidence or SOURCES.md. For a checked input, "
            "save the response/document as a local file and put its path in config.inputs; "
            "use local output paths for config.targets and config.report_path."
        )
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _targets(root: Path, values: Any, *, field: str = "config.targets") -> list[Path]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list of local file paths")
    return list(dict.fromkeys(_path(root, value, field=field) for value in values))


def _requirements(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list) or len(values) > 200:
        raise ValueError("requirements must be a list of at most 200 explicit requirements")
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("text"), str) or not value["text"].strip():
            raise ValueError("Each requirement needs nonempty text")
        text = value["text"].strip()
        identifier = value.get("id") or hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", identifier):
            raise ValueError("Invalid requirement id")
        mandatory = value.get("mandatory", True)
        if type(mandatory) is not bool:
            raise ValueError("Requirement mandatory must be boolean")
        if identifier in result:
            raise ValueError("Duplicate requirement id")
        result[identifier] = {"id": identifier, "text": text, "mandatory": mandatory}
    return list(result.values())


def _published_file(receipt: dict[str, Any]) -> Path:
    """Resolve only the canonical download store, never a path from a URL."""
    from app.application.code_agent.tools._resources import _safe_download_name
    from app.core.config import GENERATED_DIR

    name = receipt.get("download_name")
    if not isinstance(name, str) or _safe_download_name(name) is None:
        raise ValueError("Invalid published filename")
    if receipt.get("download_url") != f"/api/skills/download/{quote(name, safe='')}":
        raise ValueError("Download URL does not identify the published filename")
    digest = receipt.get("sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Missing publication digest")
    store = GENERATED_DIR.resolve()
    path = (store / name).resolve()
    if path.parent != store:
        raise ValueError("Published file escaped its download store")
    return path


def _publication_current(receipt: dict[str, Any]) -> bool:
    try:
        if file_digest(_published_file(receipt)) != receipt["sha256"]:
            return False
        if receipt.get("resource_id"):
            from app.application.media import resource_store

            record = resource_store.get_record(receipt["resource_id"])
            if record is None or record.sha256 != receipt["sha256"] or (
                file_digest(Path(record.storage_path)) != receipt["sha256"]
            ):
                return False
        if receipt.get("target"):
            target = Path(receipt["target"])
            return target.is_absolute() and file_digest(target) == receipt["sha256"]
        return receipt.get("tool") == "file_gen" or bool(receipt.get("resource_id"))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _download_link_spans(answer: str) -> list[tuple[int, int, str, str]]:
    from app.application.code_agent.answer_contracts import _FENCED_CODE, _INLINE_CODE

    # Preserve offsets while excluding code examples from rendered links.
    hide = lambda match: " " * len(match.group())
    prose = _INLINE_CODE.sub(hide, _FENCED_CODE.sub(hide, answer or ""))
    pattern = (r"\[([^\]\n]*)\]\(<?(/api/skills/download/[^\s<>\)]+)>?"
               r"(?:\s+\"[^\"\n]*\")?\)|<(/api/skills/download/[^\s<>]+)>")
    return [(match.start(), match.end(), match.group(2) or match.group(3),
             match.group(1) or "Ссылка на файл") for match in re.finditer(pattern, prose)]


class TaskOutcome:
    """Small run-owned state. Facts come from tools and from the user's message."""

    def __init__(self, snapshot: dict[str, Any] | None = None):
        saved = snapshot or {}
        self.sources = dict(saved.get("sources") or {})
        self.deliveries = {path: dict(receipt) for path, receipt in
                           (saved.get("deliveries") or {}).items() if isinstance(receipt, dict)}
        self.delivery_attempts = list(saved.get("delivery_attempts") or [])
        self.contract = dict(saved.get("contract") or {})
        # A prior candidate answer is diagnostic only, never restored as a pass.
        self.answer_verification: dict[str, Any] = {}

    @property
    def artifact_contract_seen(self) -> bool:
        """A file was actually published or a publication was attempted in this run."""
        return bool(self.delivery_attempts or self.deliveries)

    def snapshot(self) -> dict[str, Any]:
        return {"sources": self.sources,
                "deliveries": self.deliveries,
                "delivery_attempts": self.delivery_attempts, "contract": self.contract,
                "answer_verification": self.answer_verification}

    def set_contract(self, goal: str, requirements: list[dict[str, Any]]) -> None:
        requirements = _requirements(requirements)
        if goal == self.contract.get("goal", "") and requirements == self.contract.get("requirements", []):
            return
        self.contract = {**self.contract, "goal": goal, "requirements": requirements,
                         "revision": int(self.contract.get("revision", 0)) + 1}

    def apply_user_clarification(self, text: str) -> None:
        if not text.strip():
            return
        self.contract = {**self.contract, "revision": int(self.contract.get("revision", 0)) + 1,
                         "clarifications": [*self.contract.get("clarifications", []), text]}

    def verify_answer(self, answer: str, evidence: Any, epoch: int, *,
                      persistence_policy: dict[str, Any] | None = None,
                      user_request: str | None = None) -> dict[str, Any]:
        # Domain-specific source checks are performed by the mutable skill.
        # Preserve the journal contract without inventing a core attestation.
        self.answer_verification = {}
        return {}


    def missing_requirements(self, epoch: int, criteria_rows: list[dict] | None = None, *,
                             answer_verification: dict | None = None, answer: str | None = None) -> list[dict]:
        """Mandatory requirements the runtime did not confirm, plus unmet user conditions."""
        requirements = {item["id"]: item for item in self.contract.get("requirements", [])}
        statuses = {item["requirement_id"]: item.get("status") for item in criteria_rows or []
                    if item.get("requirement_id") in requirements
                    and item.get("text") == requirements[item["requirement_id"]]["text"]}
        missing = [{**item, "status": statuses.get(item["id"], "unconfirmed")}
                   for item in self.contract.get("requirements", [])
                   if item.get("mandatory", True) and statuses.get(item["id"]) != "confirmed"]
        receipt = answer_verification or {}
        if (answer is not None and receipt == self.answer_verification
                and receipt.get("answer_sha256") == hashlib.sha256(answer.encode("utf-8")).hexdigest()
                and receipt.get("input_version") == self.version(epoch)):
            missing.extend({"id": "user:" + check["field"], "text": "Условие пользователя: "
                            + check["field"] + " = " + json.dumps(check["value"], ensure_ascii=False),
                            "mandatory": True, "status": "unconfirmed"}
                           for check in receipt.get("user_constraint_checks", []) if not check["passed"])
        return missing


    def refresh_deliveries(self) -> bool:
        """Invalidate observed drift durably; only a new publication restores it."""
        changed = False
        for path, receipt in self.deliveries.items():
            if receipt.get("status") == "published" and (
                (receipt.get("target") or receipt.get("download_url")) != path
                or not _publication_current(receipt)
            ):
                receipt["status"] = "stale"
                logger.info("Delivery receipt is stale for %s", path)
                changed = True
        return changed

    def missing_deliveries(self) -> list[str]:
        self.refresh_deliveries()
        return [path for path in dict.fromkeys(self.delivery_attempts)
                if self.deliveries.get(path, {}).get("status") != "published"]

    def unbacked_download_links(self, answer: str) -> list[str]:
        """Check emitted download links, without interpreting request intent."""
        self.refresh_deliveries()
        backed = {item.get("download_url") for item in self.deliveries.values()
                  if item.get("status") == "published"}
        # Explicit Markdown/autolink destinations are delivery actions. A route
        # shown as code or described in prose is not a promised download.
        links = [url for _start, _end, url, _label in _download_link_spans(answer)]
        return list(dict.fromkeys(url for url in links if url not in backed))

    @staticmethod
    def mark_unbacked_download_links(answer: str, links: list[str]) -> str:
        """Keep useful prose while replacing unverified links with plain text."""
        for start, end, url, label in reversed(_download_link_spans(answer)):
            if url in links:
                answer = answer[:start] + label + " (публикация не подтверждена)" + answer[end:]
        return answer

    def _observe_publication(self, name: str, output: dict[str, Any], root: Path) -> None:
        # Unmapped artifacts cannot satisfy a local target merely because
        # their basenames match. Restored historical receipts remain readable.
        if name == "resource_materialize":
            for published in list(self.deliveries.values()):
                if (published.get("status") == "published" and published.get("resource_id")
                        and published["resource_id"] == output.get("resource_id")
                        and published.get("sha256") == output.get("sha256")):
                    try:
                        target = str(_path(root, output.get("project_path"), field="materialized target"))
                        receipt = {**published, "target": target}
                        if _publication_current(receipt):
                            self.deliveries[target] = receipt
                    except (OSError, ValueError, TypeError):
                        logger.debug("Materialized publication could not be bound", exc_info=True)
            return
        if name != "resource_publish":
            return
        value = output.get("project_path")
        if not value:
            return
        try:
            target = str(_path(root, value, field="published target")) if value else ""
            if target and target not in self.delivery_attempts:
                self.delivery_attempts.append(target)
            receipt = {"target": target, "tool": name, "status": "published",
                       **{key: output.get(key) for key in ("sha256", "download_name", "download_url")}}
            if _publication_current(receipt):
                self.deliveries[target or receipt["download_url"]] = receipt
            else:
                logger.debug("Publication has no current target/store binding: %s", target)
        except (OSError, ValueError, TypeError):
            logger.debug("Publication target could not be bound", exc_info=True)


    def observe(self, name: str, arguments: dict[str, Any], output: dict[str, Any],
                *, project_root: Path | None = None, input_epoch: int = 0,
                execution_status: str = "ok") -> None:
        self.refresh_deliveries()
        if name == "resource_publish" and project_root is not None:
            try:
                target = str(_path(project_root, arguments.get("project_path"), field="publication target"))
                if target not in self.delivery_attempts:
                    self.delivery_attempts.append(target)
            except (OSError, ValueError, TypeError):
                logger.debug("Publication attempt has no valid local target", exc_info=True)
        source = output.get("source_path")
        if source and output.get("source_sha256"):
            self.sources[str(source)] = str(output["source_sha256"])
        if output.get("ok") is not True:
            return
        if execution_status == "ok" and project_root is not None:
            self._observe_publication(name, output, project_root)
        if name in {"write_file", "edit_file"} and output.get("touched_path"):
            path = Path(output["touched_path"])
            if not path.is_absolute() and project_root is not None:
                path = (project_root / path).resolve()
            if path.is_absolute() and path.suffix.lower() in _CODE_SUFFIXES:
                try:
                    self.sources[str(path)] = file_digest(path)
                except OSError:
                    pass  # The tool receipt still records the mutation; no guessed hash.

    def context(self, epoch: int) -> str:
        """File publications of this run: what was published and what is still missing."""
        if not (self.delivery_attempts or self.deliveries):
            return ""
        return "[Выдача файлов текущей задачи]\n" + json.dumps(
            {"missing_downloads": self.missing_deliveries(),
             "downloads": [{key: item.get(key) for key in ("status", "download_url", "target")}
                           for item in self.deliveries.values()]},
            ensure_ascii=False, sort_keys=True,
        )

    def version(self, epoch: int) -> str:
        """Identity of the answer-check inputs: the code epoch and the user's contract."""
        version: list[Any] = [epoch, self.contract] if self.contract else [epoch]
        return hashlib.sha256(json.dumps(version, sort_keys=True).encode()).hexdigest()
