"""Model-owned reuse decisions and explicit checks through the existing runtime.

No request classifier or executor lives here. RunJournal owns durable state;
the shell runtime executes checks and skill_development owns published packages.
"""
from __future__ import annotations

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


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def task_decide(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    disposition = config.get("disposition")
    if disposition not in {"one_off", "reuse", "develop"}:
        raise ValueError("config.disposition must be one_off, reuse or develop")
    reason = config.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Explain the reuse decision in config.reason")
    name = config.get("skill_name", "")
    if not isinstance(name, str) or (disposition != "one_off" and not name.strip()):
        raise ValueError("reuse/develop needs config.skill_name")
    decision = {
        "disposition": disposition, "reason": reason.strip(), "skill_name": name.strip(),
        "targets": [str(path) for path in _targets(root, config.get("targets", []))],
        "inputs": [str(path) for path in _targets(root, config.get("inputs", []), field="config.inputs")],
    }
    if "delivery" in config:
        delivery = config["delivery"]
        if (not isinstance(delivery, dict) or set(delivery) - {"mode", "targets"}
                or delivery.get("mode") not in ("none", "chat_download")):
            raise ValueError("config.delivery needs mode none|chat_download and targets")
        targets = [str(path) for path in _targets(
            root, delivery.get("targets", []), field="config.delivery.targets",
        )]
        if bool(targets) != (delivery["mode"] == "chat_download"):
            raise ValueError("chat_download needs nonempty targets; none requires empty targets")
        decision["delivery"] = {"mode": delivery["mode"], "targets": targets}
    return {"ok": True, "task_decision": decision}


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


def result_verify(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Run a declared checker; process success alone is not its verdict.

The checker writes fresh JSON {checks: [{name: str, passed: bool}]} to report_path.
Targets must already exist and remain unchanged during verification. The runtime,
not the model's tool arguments, measures their hashes and reads the verdicts.
"""
    from app.application.code_agent.tools._run import tool_run_bash

    command = config.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("config.command must execute a result checker")
    targets = _targets(root, config.get("targets"))
    if not targets:
        raise ValueError("Provide at least one existing result in config.targets")
    report = _path(root, config.get("report_path"), field="config.report_path")
    if report in targets:
        raise ValueError("The check report must be separate from the checked results")
    execution: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    after: dict[str, str] = {}
    report_sha = ""
    problem = ""
    try:
        before = {str(path): file_digest(path) for path in targets}
        old_report = (report.stat().st_mtime_ns, file_digest(report)) if report.is_file() else None
        execution = tool_run_bash(root, command=command)
        for path in targets:
            after[str(path)] = file_digest(path)
        report_sha = file_digest(report)
        if old_report == (report.stat().st_mtime_ns, report_sha):
            raise ValueError("Checker did not write a fresh report")
        raw = json.loads(report.read_text(encoding="utf-8"))
        checks = raw.get("checks") if isinstance(raw, dict) else None
        if not isinstance(checks, list) or not checks or any(
            not isinstance(item, dict) or not isinstance(item.get("name"), str)
            or not item["name"].strip() or type(item.get("passed")) is not bool
            for item in checks
        ):
            checks = []
            raise ValueError("Report needs non-empty checks with name and boolean passed")
        if before != after:
            raise ValueError("Checked results changed during verification")
    except (OSError, ValueError, TypeError) as exc:
        problem = str(exc)
    process_passed = execution.get("ok") is True and execution.get("exit_code") == 0
    status = "unverified" if problem else "passed" if process_passed and all(
        item["passed"] for item in checks
    ) else "failed"
    verification = {
        "kind": "command_check", "status": status, "command": command,
        "exit_code": execution.get("exit_code"), "checks": checks,
        "targets": [{"path": str(path), "sha256": after.get(str(path), "")} for path in targets],
        "report_path": str(report), "report_sha256": report_sha,
    }
    return {"ok": status == "passed", "verification": verification,
            "execution_ok": process_passed, "text": execution.get("text", ""),
            **({"error": problem or execution.get("error") or "result_check_failed"}
               if status != "passed" else {})}


class TaskOutcome:
    """Small run-owned state. Decisions are supplied by Qwen, facts by tools."""

    def __init__(self, snapshot: dict[str, Any] | None = None):
        saved = snapshot or {}
        self.decision = dict(saved.get("decision") or {})
        self.sources = dict(saved.get("sources") or {})
        self.published = dict(saved.get("published") or {})
        self.loaded = dict(saved.get("loaded") or {})
        self.verifications = list(saved.get("verifications") or [])
        self.correction = str(saved.get("correction") or "")
        self.deliveries = {path: dict(receipt) for path, receipt in
                           (saved.get("deliveries") or {}).items() if isinstance(receipt, dict)}
        self.delivery_attempts = list(saved.get("delivery_attempts") or [])

    def snapshot(self) -> dict[str, Any]:
        return {"decision": self.decision, "sources": self.sources,
                "published": self.published, "loaded": self.loaded,
                "verifications": self.verifications,
                "correction": self.correction, "deliveries": self.deliveries,
                "delivery_attempts": self.delivery_attempts}

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
        delivery = self.decision.get("delivery")
        targets = (delivery.get("targets", []) if isinstance(delivery, dict)
                   else self.delivery_attempts)
        return [path for path in dict.fromkeys(targets)
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
        # A file_gen mirror carries its real touched_path. Unmapped artifacts
        # cannot satisfy a local target merely because their basenames match.
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
        if name not in {"resource_publish", "file_gen", "resource_process"}:
            return
        value = output.get("project_path") if name == "resource_publish" else output.get("touched_path")
        resource = output.get("resource")
        resource_id = ""
        if name == "resource_process":
            if (output.get("operation") != "transcribe" or output.get("execution_target") != "local_gpu"
                    or not isinstance(resource, dict) or resource.get("kind") != "document"
                    or not str(resource.get("content_type") or "").startswith("text/plain")):
                return
            resource_id = str(resource.get("resource_id") or "")
            value = None  # ResourceRecord.storage_path never crosses the model boundary.
        if not value and name == "resource_publish":
            return
        try:
            target = str(_path(root, value, field="published target")) if value else ""
            if target and target not in self.delivery_attempts:
                self.delivery_attempts.append(target)
            receipt = {"target": target, "tool": name, "status": "published",
                       **{key: output.get(key) for key in ("sha256", "download_name", "download_url")}}
            if resource_id:
                receipt["resource_id"] = resource_id
            if _publication_current(receipt):
                self.deliveries[target or receipt["download_url"]] = receipt
            else:
                logger.debug("Publication has no current target/store binding: %s", target)
        except (OSError, ValueError, TypeError):
            logger.debug("Publication target could not be bound", exc_info=True)

    def verification_context(self, epoch: int) -> dict[str, Any]:
        """Capture the run-owned inputs and skill before executing its checker."""
        return {"input_version": self.version(epoch), "skill_binding": self.skill_binding()}

    def bind_verification(self, output: dict[str, Any], before: dict[str, Any],
                          epoch: int) -> dict[str, Any]:
        """Keep execution success distinct from a check of now-stale inputs."""
        result = output.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("verification"), dict):
            return output
        from app.application.code_agent.tools._runtime_control_contract import failed, with_text

        verification = {**result["verification"], **before}
        result = {**result, "verification": verification}
        if before != self.verification_context(epoch):
            message = (
                "Inputs or the selected skill changed during result verification. "
                "Recompute the results from the current inputs, then rerun a checker "
                "that leaves those inputs unchanged."
            )
            verification["status"] = "unverified"
            result.update({"ok": False, "error": message})
            failure = failed("result_verify", message, code="verification_inputs_changed")
            return with_text({**{key: value for key, value in failure.items() if key != "text"},
                              "result": result})
        return with_text({**{key: value for key, value in output.items() if key != "text"},
                          "result": result})

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
        result = output.get("result") or {}
        source = output.get("source_path")
        if source and output.get("source_sha256"):
            self.sources[str(source)] = str(output["source_sha256"])
        if (name == "runtime_control" and arguments.get("operation") == "result_verify"
                and isinstance(result, dict) and isinstance(result.get("verification"), dict)):
            verification = {**self.verification_context(input_epoch), **result["verification"]}
            targets = {item.get("path") for item in verification.get("targets", [])}
            self.verifications = [old for old in self.verifications if
                                  {item.get("path") for item in old.get("targets", [])} != targets]
            self.verifications.append(verification)
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
        if name == "runtime_control" and isinstance(result, dict):
            operation = arguments.get("operation")
            if operation == "task_decide" and isinstance(result.get("task_decision"), dict):
                decision = dict(result["task_decision"])
                if "delivery" not in decision and "delivery" in self.decision:
                    decision["delivery"] = self.decision["delivery"]
                self.decision = decision
            elif operation == "skill_publish" and result.get("revision"):
                self.published[result["name"]] = {
                    key: result[key] for key in ("candidate_id", "revision", "sha256")
                }
            elif operation == "skill_load" and isinstance(result.get("skill"), dict):
                skill = result["skill"]
                self.loaded[skill["name"]] = {key: skill.get(key) for key in
                    ("candidate_id", "revision", "sha256", "package_sha256", "directory")}
    def pending(self) -> str:
        if self.sources and not self.decision:
            return (
                "Код создан или исполнен. Зафиксируй решение через runtime_control(task_decide): "
                "config={disposition: one_off|reuse|develop, reason, skill_name, targets:[пути результатов]}. "
                "Выбери по смыслу исходной задачи: повторяемая полезная обработка требует сохраняемой "
                "возможности; одноразовое вычисление может остаться one_off. Для develop выбери имя, "
                "создай/проверь/опубликуй пакет существующим skill lifecycle и примени его к задаче. "
                "Наблюдаемые исходники: " + json.dumps(self.sources, ensure_ascii=False)
            )
        disposition = self.decision.get("disposition")
        name = self.decision.get("skill_name", "")
        if disposition in {"develop", "reuse"}:
            if disposition == "develop" and name not in self.published:
                return f"Решение develop для {name} ещё не сохранено: создай кандидат, skill_check и skill_publish, затем примени и проверь результат исходной задачи."
            if name not in self.loaded:
                return f"Загрузи выбранную возможность {name} через skill_load и примени к исходной задаче."
            loaded = self.loaded[name]
            if disposition == "develop":
                published = self.published[name]
                if (any(loaded.get(key) != published.get(key) for key in ("candidate_id", "revision"))
                        or loaded.get("package_sha256") != published.get("sha256")):
                    return f"Загрузи опубликованную версию {name} через skill_load и примени её к задаче; в контексте ещё предыдущая версия."
            if loaded.get("candidate_id"):
                from app.application.code_agent.skill_development import validated_package

                try:
                    validated_package(name, loaded["candidate_id"], expected=loaded)
                except (OSError, ValueError, RuntimeError) as exc:
                    return f"Сохранённая возможность {name} требует исправления: {exc}"
        return ""

    def context(self, epoch: int) -> str:
        if not self.sources and not self.decision and not self.delivery_attempts and not self.deliveries:
            return ""
        return "[Результат и накопленный опыт текущей задачи]\n" + json.dumps(
            {"decision": self.decision, "sources": self.sources,
             "pending": self.pending(),
             "missing_downloads": self.missing_deliveries(),
             "unpublished_attempts": [path for path in self.delivery_attempts
                 if self.deliveries.get(path, {}).get("status") != "published"],
             "downloads": [{key: item.get(key) for key in ("status", "download_url", "target")}
                           for item in self.deliveries.values()],
             "published": self.published, "loaded": list(self.loaded),
             "checks": [{"status": item.get("status"), "targets": item.get("targets")}
                        for item in self.current_verifications(epoch)]}, ensure_ascii=False, sort_keys=True,
        )

    def current_verifications(self, epoch: int) -> list[dict[str, Any]]:
        """Never revive an earlier pass after changed inputs or a run mutation."""
        version = self.version(epoch)
        return [dict(item) if item.get("input_version") == version
                else {**item, "status": "unverified"} for item in self.verifications]

    def checks_current(self, targets: list[str], epoch: int) -> bool:
        return bool(targets) and not self.unverified_targets(targets, epoch)

    def unverified_targets(self, targets: list[str], epoch: int) -> list[str]:
        latest = {}
        for item in self.current_verifications(epoch):
            for target in item.get("targets", []):
                latest[target["path"]] = item.get("status")
        return [target for target in targets if latest.get(target) != "passed"]

    def version(self, epoch: int) -> str:
        observed = {}
        for value in [*self.decision.get("inputs", []), *self.decision.get("targets", [])]:
            try:
                observed[value] = file_digest(Path(value))
            except OSError:
                observed[value] = None
        version: list[Any] = [epoch, observed]
        binding = self.skill_binding()
        if binding:
            version.append(binding)
        return hashlib.sha256(json.dumps(version, sort_keys=True).encode()).hexdigest()

    def skill_binding(self) -> dict[str, Any]:
        """Bind a check to the selected package, not to the last loaded instruction."""
        name = self.decision.get("skill_name")
        if self.decision.get("disposition") not in {"reuse", "develop"} or not name:
            return {}
        loaded = self.loaded.get(name) or {}
        return {"name": name, "identity": {key: loaded[key] for key in
                ("sha256", "revision", "package_sha256", "candidate_id") if loaded.get(key)}}

    def learning_evidence(self, epoch: int) -> dict[str, Any] | None:
        """Current file-check evidence, never runtime success or model self-assessment.

        This records an observed association, not proof that a selected skill
        caused success. The advisor still leaves selection to Qwen.
        """
        binding = self.skill_binding()
        if not binding or not binding["identity"].get("sha256"):
            return None
        targets = self.decision.get("targets") or []
        input_version = self.version(epoch)
        if not targets or not self.checks_current(targets, epoch):
            return None
        latest: dict[str, dict[str, Any]] = {}
        for item in self.current_verifications(epoch):
            for target in item.get("targets", []):
                latest[target["path"]] = item
        reports: dict[str, str] = {}
        checked: list[dict[str, str]] = []
        try:
            for path in targets:
                receipt = latest[path]
                checks = receipt.get("checks")
                if (receipt.get("status") != "passed" or receipt.get("exit_code") != 0
                        or receipt.get("skill_binding") != binding
                        or not isinstance(checks, list) or not checks
                        or any(not isinstance(check, dict) or check.get("passed") is not True
                               or not isinstance(check.get("name"), str) or not check["name"].strip()
                               for check in checks)):
                    return None
                digest = next(target["sha256"] for target in receipt["targets"] if target["path"] == path)
                if file_digest(Path(path)) != digest:
                    return None
                report_path = receipt["report_path"]
                report_hash = receipt["report_sha256"]
                if not report_hash or file_digest(Path(report_path)) != report_hash:
                    return None
                reports[report_path] = report_hash
                checked.append({"path": path, "sha256": digest})
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            return None
        try:
            inputs = [{"path": path, "sha256": file_digest(Path(path))}
                      for path in self.decision.get("inputs", [])]
        except (OSError, TypeError, ValueError):
            return None
        if self.version(epoch) != input_version:
            return None
        return {"skill_binding": binding, "targets": checked, "inputs": inputs,
                "reports": [{"path": path, "sha256": digest} for path, digest in sorted(reports.items())],
                "input_version": input_version}
