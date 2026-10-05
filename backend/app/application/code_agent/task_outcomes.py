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
from urllib.parse import quote, urlsplit, urldefrag


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


def _requirements(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list) or len(values) > 200:
        raise ValueError("requirements must be a list of at most 200 explicit requirements")
    result: dict[str, dict[str, Any]] = {}
    verification_errors = []
    for index, value in enumerate(values):
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
        if "verification" in value:
            try:
                result[identifier]["verification"] = _answer_checks(
                    value["verification"], path=f"config.requirements[{index}].verification")
            except ValueError as exc:
                verification_errors.append(str(exc))
        if "source_clarification" in value:
            index = value["source_clarification"]
            if type(index) is not int or index < 1:
                raise ValueError("source_clarification must identify an accepted user clarification (1-based)")
            result[identifier]["source_clarification"] = index
    if verification_errors:
        raise ValueError("Invalid requirement verification: " + " | ".join(verification_errors))
    return list(result.values())


def _answer_checks(value: Any, *, path: str = "verification") -> dict[str, Any]:
    """Explicit mechanical checks; never infer coverage from requirement prose."""
    fields = {
        "web_search": ({"kind", "query", "url"}, set()),
        "source_read": ({"kind", "url"}, {"contains"}),
        "answer_format": ({"kind"}, {"contains", "max_chars", "language", "markdown_url", "user_quote"}),
        "cited_quote": ({"kind", "url", "count", "max_words"}, set()),
        "tool_policy": ({"kind", "allowed"}, {"max_search_queries", "max_read_urls", "user_quote"}),
        "no_persistence": ({"kind"}, {"user_quote"}),
    }

    def names(keys: set) -> str:
        labels = [key if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", key)
                  else "<non-schema field name>" for key in keys]
        labels.sort()
        return ", ".join(labels[:12]) + (f", ... ({len(labels)} fields)" if len(labels) > 12 else "")

    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object; required field: checks (list of 1..20 typed check objects)")
    errors = []
    if set(value) != {"checks"}:
        missing = {"checks"} - set(value)
        unknown = set(value) - {"checks"}
        errors.append(f"{path}: " + "; ".join(
            [*( ["missing fields: " + names(missing)] if missing else []),
             *( ["unknown fields: " + names(unknown)] if unknown else []),
             "required field: checks (list of 1..20 typed check objects)"]))
    checks = value.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= 20:
        errors.append(f"{path}.checks must be a list of 1..20 typed check objects")
        raise ValueError("Invalid answer verification: " + " | ".join(errors))
    result = []
    for index, check in enumerate(checks):
        check_path = f"{path}.checks[{index}]"
        if not isinstance(check, dict):
            errors.append(f"{check_path} must be an object; kind must be a string enum: " + names(set(fields)))
            continue
        kind = check.get("kind")
        if "kind" not in check:
            errors.append(f"{check_path}: missing fields: kind; required field: kind (string enum: "
                          + names(set(fields)) + ")")
            continue
        if not isinstance(kind, str) or kind not in fields:
            errors.append(f"{check_path}.kind must be a string enum: " + names(set(fields)))
            continue
        prefix = f"{check_path} (kind={kind})"
        required, optional = fields[kind]
        missing, unknown = required - set(check), set(check) - required - optional
        if missing or unknown:
            types = {"kind": "string enum", "url": "HTTP(S) URL string", "markdown_url": "HTTP(S) URL string",
                     "query": "nonempty string", "language": "string enum ru|en", "contains": "nonempty string list",
                     "allowed": "nonempty string list", "max_chars": "integer 1..100000", "count": "integer 1..100000",
                     "max_words": "integer 1..100000", "max_search_queries": "integer 1..100000",
                     "max_read_urls": "integer 0..100000", "user_quote": "exact direct-user constraint string"}
            if kind == "source_read":
                types["contains"] = "nonempty literal string"
            errors.append(prefix + ": " + "; ".join(
                [*( ["missing fields: " + names(missing)] if missing else []),
                 *( ["unknown fields: " + names(unknown)] if unknown else []),
                 "required fields: " + names(required), "optional fields: " + (names(optional) or "none"),
                 "field types: " + ", ".join(f"{key}={types[key]}" for key in sorted(required | optional))]))
        for key in ("url", "markdown_url", "query"):
            if key in check and (not isinstance(check[key], str) or not check[key].strip()
                    or len(check[key]) > 4096):
                expected = "HTTP(S) URL string" if key != "query" else "nonempty string"
                errors.append(f"{prefix}.{key} must be a {expected} of at most 4096 characters")
        if "user_quote" in check and (not isinstance(check["user_quote"], str)
                or not check["user_quote"].strip() or len(check["user_quote"]) > 16000):
            errors.append(f"{prefix}.user_quote must be a nonempty direct-user constraint string, at most 16000 characters")
        if kind == "answer_format" and not (set(check) & {"contains", "max_chars", "language", "markdown_url"}):
            errors.append(f"{prefix} needs at least one format field")
        for key in ("url", "markdown_url"):
            if isinstance(check.get(key), str):
                try:
                    parsed = urlsplit(check[key])
                    valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                except ValueError:
                    valid_url = False
                if not valid_url:
                    errors.append(f"{prefix}.{key} must be an HTTP(S) URL string with a hostname")
        for key in ("max_chars", "count", "max_words", "max_search_queries", "max_read_urls"):
            minimum = 0 if key in {"max_search_queries", "max_read_urls"} else 1
            if key in check and (type(check[key]) is not int or not minimum <= check[key] <= 100_000):
                errors.append(f"{prefix}.{key} must be an integer {minimum}..100000 (boolean is not an integer)")
        if kind == "answer_format" and "language" in check and (not isinstance(check["language"], str)
                or check["language"] not in {"ru", "en"}):
            errors.append(f"{prefix}.language must be a string enum ru|en")
        contains = check.get("contains")
        if kind == "source_read" and "contains" in check:
            if not isinstance(contains, str) or not contains.strip() or len(contains) > 4000:
                errors.append(f"{prefix}.contains must be a nonempty literal string of at most 4000 characters")
        for key in ("allowed", "contains"):
            if key in check and not (key == "contains" and kind == "source_read"):
                items = check[key]
                minimum = 0 if key == "contains" else 1
                if (not isinstance(items, list) or not minimum <= len(items) <= 32
                        or any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in items)):
                    errors.append(f"{prefix}.{key} must be a list of {minimum}..32 nonempty strings of at most 4000 characters")
        result.append(dict(check))
    if errors:
        raise ValueError("Invalid answer verification: " + " | ".join(errors))
    return {"checks": result}


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
    if "requirements" in config:
        decision["requirements"] = _requirements(config["requirements"])
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
            or ("requirement_id" in item and (not isinstance(item["requirement_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", item["requirement_id"])))
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
        "execution_ok": process_passed,
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
        self.contract = dict(saved.get("contract") or {})
        self.learning_history = list(saved.get("learning_history") or [])
        # A prior candidate answer is diagnostic only, never restored as a pass.
        self.answer_verification: dict[str, Any] = {}
        self.artifact_contract_seen = bool(saved.get("artifact_contract_seen") or self.decision.get("targets")
            or self.decision.get("inputs") or self.decision.get("delivery", {}).get("mode") == "chat_download")

    def snapshot(self) -> dict[str, Any]:
        return {"decision": self.decision, "sources": self.sources,
                "published": self.published, "loaded": self.loaded,
                "verifications": self.verifications,
                "correction": self.correction, "deliveries": self.deliveries,
                "delivery_attempts": self.delivery_attempts, "contract": self.contract,
                "learning_history": self.learning_history,
                "answer_verification": self.answer_verification,
                "artifact_contract_seen": self.artifact_contract_seen}

    def set_contract(self, goal: str, requirements: list[dict[str, Any]]) -> None:
        requirements = _requirements(requirements)
        if goal == self.contract.get("goal", "") and requirements == self.contract.get("requirements", []):
            return
        self.contract = {**self.contract, "goal": goal, "requirements": requirements,
                         "revision": int(self.contract.get("revision", 0)) + 1}
        self.correction = ""

    def apply_user_clarification(self, text: str) -> None:
        if not text.strip():
            return
        self.contract = {**self.contract, "revision": int(self.contract.get("revision", 0)) + 1,
                         "clarifications": [*self.contract.get("clarifications", []), text],
                         "declaration_pending": True}
        self.correction = ""

    def _bound_checks(self, receipt: dict[str, Any]) -> list[dict[str, Any]]:
        requirements = self.contract.get("requirements", [])
        names = {" ".join(item["text"].split()).casefold(): item["id"] for item in requirements}
        identifiers = {item["id"] for item in requirements}
        checks = []
        for raw in receipt.get("checks", []):
            item = dict(raw)
            identifier = item.get("requirement_id") or names.get(" ".join(str(item.get("name", "")).split()).casefold())
            if identifier in identifiers:
                item["requirement_id"] = identifier
            else:
                item.pop("requirement_id", None)
            checks.append(item)
        return checks

    def verify_answer(self, answer: str, evidence: Any, epoch: int, *,
                      persistence_policy: dict[str, Any] | None = None,
                      user_request: str | None = None) -> dict[str, Any]:
        """Check the actual chat candidate against executed, current-run facts.

        These checks cover only the explicitly declared predicates. They do not
        certify semantic correctness, file artifacts, delivery or reusable skills.
        """
        from app.application.code_agent.answer_contracts import (
            _FENCED_CODE, _INLINE_CODE, _quote_spans, _quote_word_count, explicit_web_answer_constraints,
            explicit_quote_request,
        )
        from app.application.web_evidence.receipts import valid_source
        from app.application.code_agent.answer_language import answer_language_matches
        from app.application.code_agent.loop_helpers import web_cache_write_allowed, task_persistence_policy

        self.answer_verification = {}
        operations = evidence.tool_operations
        key = lambda item: (item["tool_name"] + ":" + item["operation"]
                            if item["tool_name"] == "runtime_control" else item["tool_name"])
        readonly = {"web_search", "web_fetch", "web_query", "web_sitemap", "capability_load",
                    "runtime_control:task_decide"}
        if (self.artifact_contract_seen or self.verifications
                or self.decision.get("disposition") not in {None, "one_off"}
                or (not self.decision and user_request is None) or self.decision.get("inputs")
                or self.decision.get("targets") or self.sources or self.loaded or self.published
                or self.delivery_attempts or self.deliveries or epoch != 0 or evidence.has_mutations
                or self.decision.get("delivery", {}).get("mode", "none") != "none"
                or not evidence.operations_complete or not operations
                or any(key(item) not in readonly or item["state_changed"]
                       or (item.get("store") and (item["tool_name"] != "web_fetch"
                                                  or not web_cache_write_allowed(persistence_policy)))
                       for item in operations)):
            return {}
        sources = {item["id"]: item for item in evidence.sources if valid_source(item)}
        presented = [item for item in evidence.presented_sources
                     if item.get("quote_verified") is True and item["tool"] in {"web_fetch", "web_query"}]
        searches = evidence.web_operations
        def same_url(first: str, second: str) -> bool:
            try:
                return urldefrag(first)[0] == urldefrag(second)[0]
            except (TypeError, ValueError):
                return False
        quotes = _quote_spans(answer)
        bindings = evidence.quote_bindings(answer)

        user_constraints = explicit_web_answer_constraints(user_request) if user_request is not None else None
        requested_policy = task_persistence_policy(user_request or "")
        forbidden_channels = [channel for channel in ("rag", "direct_memory", "learning")
                              if requested_policy.get(channel) is False]
        if user_request is None or (isinstance(persistence_policy, dict) and all(
            persistence_policy.get(channel) is False for channel in ("rag", "direct_memory", "learning")
        )):
            forbidden_channels = ["rag", "direct_memory", "learning"]

        def effective(check: dict[str, Any], field: str) -> Any:
            return check.get(field) if user_constraints is None else user_constraints.get(field)

        def ignored_fields(check: dict[str, Any]) -> list[str]:
            if user_constraints is None:
                return []
            if check["kind"] == "answer_format":
                return [key for key in ("contains", "max_chars") if key in check and check[key] != effective(check, key)]
            if check["kind"] == "tool_policy":
                return [key for key in ("allowed", "max_search_queries", "max_read_urls")
                        if key in check and check[key] != effective(check, key)]
            if check["kind"] == "no_persistence" and not forbidden_channels:
                return ["no_persistence"]
            if check["kind"] == "cited_quote" and not explicit_quote_request(user_request or ""):
                return ["cited_quote"]
            return []

        def passed(check: dict[str, Any], *, only: str | None = None) -> bool:
            kind = check["kind"]
            def constraint(field: str) -> Any:
                return effective(check, field) if only is None or only == field else None
            if kind == "web_search":
                for item in searches:
                    query_sources = item.get("query_source_ids")
                    ids = (query_sources.get(check["query"], []) if query_sources is not None
                           else item["source_ids"] if item["queries"] == [check["query"]] else [])
                    if any(source_id in sources and sources[source_id]["tool"] == "web_search"
                           and sources[source_id]["status"] == "discovered"
                           and same_url(sources[source_id]["url"], check["url"])
                           for source_id in ids):
                        return True
                return False
            if kind == "source_read":
                return any(same_url(item["url"], check["url"])
                           and check.get("contains", "") in item["quote"] for item in presented)
            if kind == "answer_format":
                prose = _FENCED_CODE.sub("", answer)
                links = re.findall(r"\[[^\]\n]+\]\(<?(https?://[^\s<>\)]+)>?\)", _INLINE_CODE.sub("", prose))
                cap = constraint("max_chars")
                return ((cap is None or len(answer) <= cap)
                        and ("language" not in check or answer_language_matches(answer, check["language"], presented))
                        and all(text in prose for text in constraint("contains") or [])
                        and ("markdown_url" not in check or any(same_url(url, check["markdown_url"]) for url in links)))
            if kind == "cited_quote":
                if user_request is not None and not explicit_quote_request(user_request):
                    return True
                return (len(quotes) == check["count"] and len(bindings) == len(quotes)
                        and all(0 < _quote_word_count(body) <= check["max_words"] for _, _, body, _ in quotes)
                        and all(item["status"] == "matched" and any(
                            source_id in sources and same_url(sources[source_id]["url"], check["url"])
                            for source_id in item["source_ids"]) for item in bindings))
            if kind == "tool_policy":
                search_count = sum(item["query_count"] for item in operations)
                read_count = sum(len(item["read_urls"]) for item in operations)
                allowed = constraint("allowed")
                query_cap, read_cap = constraint("max_search_queries"), constraint("max_read_urls")
                orchestration = {"runtime_control:task_decide", "capability_load"} if user_constraints is not None else set()
                return ((allowed is None or all(key(item) in set(allowed) | orchestration for item in operations))
                        and (query_cap is None or search_count <= query_cap)
                        and (read_cap is None or read_count <= read_cap))
            if kind == "no_persistence":
                if not forbidden_channels:
                    return True
                return isinstance(persistence_policy, dict) and all(
                    persistence_policy.get(channel) is False for channel in forbidden_channels)
            return False

        rows = []
        for requirement in self.contract.get("requirements", []):
            declaration = requirement.get("verification")
            if not declaration:
                continue
            predicates = [{"kind": check["kind"], "passed": passed(check),
                           **({"unrequested_constraints": ignored_fields(check)} if ignored_fields(check) else {})}
                          for check in declaration["checks"]]
            rows.append({"requirement_id": requirement["id"], "name": requirement["text"],
                         "passed": all(item["passed"] for item in predicates), "predicates": predicates})
        # Direct-user conditions are independent of the model's declaration.
        user_checks = []
        if user_constraints is not None:
            for kind, fields in (
                ("answer_format", {"contains", "max_chars"}),
                ("tool_policy", {"allowed", "max_search_queries", "max_read_urls"}),
            ):
                for field in sorted(fields & user_constraints.keys()):
                    value = user_constraints[field]
                    user_checks.append({"kind": kind, "field": field, "value": value,
                                        "passed": passed({"kind": kind}, only=field)})
            if forbidden_channels:
                user_checks.append({"kind": "no_persistence", "field": "no_persistence", "value": forbidden_channels,
                                    "passed": passed({"kind": "no_persistence"})})
        self.answer_verification = {
            "kind": "answer_evidence", "input_version": self.version(epoch),
            "contract_revision": self.contract.get("revision", 0), "input_epoch": epoch,
            "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(), "checks": rows,
            "persistence_policy": ({channel: persistence_policy.get(channel)
                                    for channel in ("rag", "direct_memory", "learning")}
                                   if isinstance(persistence_policy, dict) else None),
            **({"user_constraints": user_constraints, "user_constraint_checks": user_checks}
               if user_constraints is not None else {}),
            "status": "passed" if rows and all(item["passed"] for item in [*rows, *user_checks]) else "unconfirmed",
        }
        return dict(self.answer_verification)

    def missing_requirements(self, epoch: int, criteria_rows: list[dict] | None = None, *,
                             answer_verification: dict | None = None, answer: str | None = None) -> list[dict]:
        requirements = {item["id"]: item for item in self.contract.get("requirements", [])}
        statuses = {item["requirement_id"]: item.get("status") for item in criteria_rows or []
                    if item.get("requirement_id") in requirements
                    and item.get("text") == requirements[item["requirement_id"]]["text"]}
        for receipt in self.current_verifications(epoch):
            for check in self._bound_checks(receipt):
                if check.get("requirement_id"):
                    statuses[check["requirement_id"]] = ("confirmed" if check.get("passed") is True
                        and receipt.get("status") in {"passed", "failed"} and receipt.get("exit_code") == 0
                        else "failed" if check.get("passed") is False
                        and receipt.get("exit_code") == 0 and receipt.get("status") == "failed" else "unconfirmed")
        receipt = answer_verification or {}
        if (answer is not None and receipt == self.answer_verification
                and receipt.get("kind") == "answer_evidence" and receipt.get("input_version") == self.version(epoch)
                and receipt.get("contract_revision") == self.contract.get("revision", 0)
                and receipt.get("answer_sha256") == hashlib.sha256(answer.encode("utf-8")).hexdigest()):
            for check in receipt.get("checks", []):
                identifier = check.get("requirement_id")
                if identifier in requirements and statuses.get(identifier) != "failed":
                    statuses[identifier] = "confirmed" if check.get("passed") is True else "unconfirmed"
        missing = [{**item, "status": statuses.get(item["id"], "unconfirmed")}
                for item in self.contract.get("requirements", [])
                if item.get("mandatory", True) and statuses.get(item["id"]) != "confirmed"]
        if (answer is not None and receipt == self.answer_verification
                and receipt.get("answer_sha256") == hashlib.sha256(answer.encode("utf-8")).hexdigest()
                and receipt.get("input_version") == self.version(epoch)):
            covered_kinds = {check["kind"] for requirement in missing
                             for check in (requirement.get("verification") or {}).get("checks", [])}
            missing.extend({"id": "user:" + check["field"], "text": "Условие пользователя: "
                + check["field"] + " = " + json.dumps(check["value"], ensure_ascii=False),
                "mandatory": True, "status": "unconfirmed"}
                for check in receipt.get("user_constraint_checks", [])
                if not check["passed"] and check["kind"] not in covered_kinds)
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

    def bind_decision(self, output: dict[str, Any]) -> dict[str, Any]:
        result = output.get("result")
        decision = result.get("task_decision") if isinstance(result, dict) else None
        if not isinstance(decision, dict) or "requirements" not in decision:
            return output
        existing = {item["id"]: item for item in self.contract.get("requirements", [])}
        bound_requirements = []
        for item in _requirements(decision["requirements"]):
            previous = existing.get(item["id"])
            if previous and item["text"] != previous["text"]:
                clause = self._amendment_clause(previous, item)
                if clause is None:
                    from app.application.code_agent.tools._runtime_control_contract import failed, with_text

                    return with_text(failed("task_decide", "A model declaration cannot replace an existing requirement. "
                        f"Keep {item['id']}: {previous['text']}. To amend an explicitly changed condition, "
                        "set source_clarification=<1-based accepted input> and copy the exact amendment or addition clause "
                        "from that stored direct user clarification into text. Preserve all other constraints. "
                        "The neutral preambles 'Ещё поправка:' and 'Уточнение к текущей задаче:' may be omitted. "
                        "Accepted clarifications: " + json.dumps([
                            {"source_clarification": index, "text": text}
                            for index, text in enumerate(self.contract.get("clarifications", []), 1)
                        ], ensure_ascii=False),
                        code="requirement_replacement_not_authorized"))
                note = f"\n\nУточнение пользователя {item['source_clarification']}: {clause}"
                # A requirement can contain several independent constraints.
                # Keep them all, then attach the exact authorized amendment.
                text = previous["text"] if note in previous["text"] else previous["text"] + note
                item = {**item, "text": text, "mandatory": previous["mandatory"] or item["mandatory"]}
            bound_requirements.append(item)
        if bound_requirements == decision["requirements"]:
            return output
        from app.application.code_agent.tools._runtime_control_contract import with_text

        return with_text({**{key: value for key, value in output.items() if key != "text"},
                          "result": {**result, "task_decision": {**decision, "requirements": bound_requirements}}})

    def _authorized_amendment(self, previous: dict, incoming: dict) -> bool:
        return self._amendment_clause(previous, incoming) is not None

    def _amendment_clause(self, previous: dict, incoming: dict) -> str | None:
        index = incoming.get("source_clarification")
        clarifications = self.contract.get("clarifications", [])
        if (type(index) is not int or not 1 <= index <= len(clarifications)
                or not isinstance(clarifications[index - 1], str)):
            return None
        text = incoming["text"]
        prefix = previous["text"] + f"\n\nУточнение пользователя {index}: "
        if text.startswith(prefix):
            text = text[len(prefix):]
        def copied_clause(value: str) -> str:
            return re.sub(r"^(?:Ещё поправка|Уточнение к текущей задаче):\s*", "", value.strip(),
                          flags=re.IGNORECASE)
        normalize = lambda value: " ".join(copied_clause(value).split()).strip(".!?;").casefold()
        normalized = normalize(text)
        # Match a whole copied user clause, not a substring that can discard
        # its leading prohibition or turn quoted fragments into consent.
        clauses = re.split(r"(?<=[.!?;])\s+|\n+", clarifications[index - 1])
        clause = next((copied_clause(value) for value in clauses if normalize(value) == normalized), None)
        if clause is None or not re.search(
            r"\b(?:вместо|замен\w*|измени\w*|добав\w*|replace|instead|change|add|append)\b", normalized,
        ):
            return None
        if re.search(
            r"\bне\s+(?:(?:надо|нужно|следует|пытайся)\s+)?(?:замен\w*|измен\w*|меня\w*|обнов\w*|добав\w*)"
            r"|\b(?:do not|don't|never)\s+(?:replace|change|update|add|append)\b", normalized,
        ):
            return None
        # A copied replacement clause must identify the old condition, rather
        # than authorizing unrelated requirements in the same task.
        ignored = {"прове", "требо", "должн", "резул", "check", "requi", "shoul"}
        def anchors(value: str) -> set[str]:
            return {token[:5] for token in re.findall(r"[\w.]+", value.casefold())
                    if len(token) >= 4 and token[:5] not in ignored}
        def file_tokens(value: str) -> set[str]:
            suffixes = _CODE_SUFFIXES | {".md", ".txt", ".json", ".csv", ".tsv", ".xlsx", ".xls",
                                        ".docx", ".pdf", ".html", ".xml", ".yaml", ".yml", ".toml"}
            return {token for token in re.findall(r"(?<![\w.])[\w-]+(?:\.[\w-]+)+(?![\w.])", value.casefold())
                    if Path(token).suffix in suffixes}
        matches = (len(anchors(previous["text"]) & anchors(normalized)) >= 2
                   or bool(file_tokens(previous["text"]) & file_tokens(normalized)))
        return clause if matches else None

    def bind_verification(self, output: dict[str, Any], before: dict[str, Any],
                          epoch: int) -> dict[str, Any]:
        """Keep execution success distinct from a check of now-stale inputs."""
        result = output.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("verification"), dict):
            return output
        from app.application.code_agent.tools._runtime_control_contract import failed, with_text

        verification = {**result["verification"], **before}
        verification["checks"] = self._bound_checks(verification)
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
            verification["checks"] = self._bound_checks(verification)
            targets = {item.get("path") for item in verification.get("targets", [])}
            checked_ids = {check.get("requirement_id") for check in verification["checks"]
                           if check.get("requirement_id")}
            self.verifications = [old for old in self.verifications if
                {item.get("path") for item in old.get("targets", [])} != targets
                or (checked_ids and not {check.get("requirement_id") for check in old.get("checks", [])
                                         if check.get("requirement_id")} <= checked_ids)]
            self.verifications.append(verification)
            failure = self._failure_observation(verification, input_epoch)
            if failure and not any(item.get("case_id") == failure["case_id"]
                    and item.get("requirement_ids") == failure["requirement_ids"]
                    and item.get("skill_binding") == failure["skill_binding"] for item in self.learning_history):
                self.learning_history.append(failure)
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
                bound = self.bind_decision(output)
                if bound.get("ok") is not True:
                    return
                decision = dict(bound["result"]["task_decision"])
                if "delivery" not in decision and "delivery" in self.decision:
                    decision["delivery"] = self.decision["delivery"]
                if "requirements" in decision:
                    merged = {item["id"]: item for item in self.contract.get("requirements", [])}
                    for item in _requirements(decision["requirements"]):
                        previous = merged.get(item["id"])
                        if previous is not None:
                            # A model declaration cannot silently remove a user requirement.
                            item = {**item, "mandatory": previous["mandatory"] or item["mandatory"]}
                        merged[item["id"]] = item
                    self.set_contract(self.contract.get("goal", ""), list(merged.values()))
                    self.contract["declaration_pending"] = False
                    decision["requirements"] = list(merged.values())
                self.decision = decision
                self.artifact_contract_seen = bool(self.artifact_contract_seen or decision.get("targets")
                    or decision.get("inputs") or decision.get("delivery", {}).get("mode") == "chat_download")
            elif operation == "skill_publish" and result.get("revision"):
                self.published[result["name"]] = {
                    key: result[key] for key in ("candidate_id", "revision", "sha256")
                }
            elif operation == "skill_load" and isinstance(result.get("skill"), dict):
                skill = result["skill"]
                self.loaded[skill["name"]] = {key: skill.get(key) for key in
                    ("candidate_id", "revision", "sha256", "package_sha256", "directory")}
    def pending(self) -> str:
        if self.contract.get("goal") and (self.sources or self.decision.get("targets")) and (
                not self.contract.get("requirements") or self.contract.get("declaration_pending")):
            return ("Объяви весь актуальный контракт через task_decide.config.requirements:[{id,text,mandatory:true}]: "
                    "все обязательные пункты исходного сообщения и принятых уточнений. "
                    "Сохрани прежние требования. Для явно изменённого условия укажи тот же id, "
                    "source_clarification=<номер принятого уточнения, начиная с 1> и в text точную "
                    "фразу изменения или добавления из этого прямого сообщения пользователя "
                    "(можно опустить только вступление «Ещё поправка:» или «Уточнение к текущей задаче:»); "
                    "остальные условия сохраняются. "
                    "после этого проверь каждый requirement_id по текущим файлам.")
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
        if not (self.sources or self.decision or self.delivery_attempts or self.deliveries
                or self.verifications or self.loaded or self.published):
            # The pinned task contract already carries goal, requirements and
            # clarifications. An outcome with no actions adds no new evidence.
            return ""
        return "[Результат и накопленный опыт текущей задачи]\n" + json.dumps(
            {"decision": self.decision, "sources": self.sources, "contract": self.contract,
             "unconfirmed_requirements": self.missing_requirements(epoch),
             "pending": self.pending(),
             "missing_downloads": self.missing_deliveries(),
             "unpublished_attempts": [path for path in self.delivery_attempts
                 if self.deliveries.get(path, {}).get("status") != "published"],
             "downloads": [{key: item.get(key) for key in ("status", "download_url", "target")}
                           for item in self.deliveries.values()],
             "published": self.published, "loaded": list(self.loaded),
             "checks": [{"status": item.get("status"), "targets": item.get("targets"),
                         "checks": item.get("checks")}
                        for item in self.current_verifications(epoch)]}, ensure_ascii=False, sort_keys=True,
        )

    def current_verifications(self, epoch: int) -> list[dict[str, Any]]:
        """Never revive an earlier pass after changed inputs or a run mutation."""
        version = self.version(epoch)
        for item in self.verifications:
            if item.get("input_version") != version or not self._receipt_files_current(item):
                item["status"] = "unverified"
        return [dict(item) for item in self.verifications]

    @staticmethod
    def _receipt_files_current(item: dict[str, Any]) -> bool:
        try:
            return (bool(item.get("targets")) and bool(item.get("report_sha256"))
                and all(file_digest(Path(target["path"])) == target["sha256"] for target in item["targets"])
                and file_digest(Path(item["report_path"])) == item["report_sha256"])
        except (OSError, ValueError, KeyError, TypeError):
            return False

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
        if self.contract:
            version.append(self.contract)
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
        if (not targets or not self.checks_current(targets, epoch) or self.missing_requirements(epoch)
                or self.contract.get("declaration_pending")):
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

    def _case_id(self, binding: dict[str, Any]) -> str:
        case = {"skill_binding": binding, "goal": self.contract.get("goal", ""),
                "targets": sorted(self.decision.get("targets", [])),
                "inputs": sorted(self.decision.get("inputs", []))}
        return hashlib.sha256(json.dumps(case, sort_keys=True).encode("utf-8")).hexdigest()

    def _failure_observation(self, receipt: dict[str, Any], epoch: int) -> dict | None:
        binding = self.skill_binding()
        failed_ids = sorted({item["requirement_id"] for item in self._bound_checks(receipt)
                             if item.get("passed") is False and item.get("requirement_id")})
        if (not binding.get("identity", {}).get("sha256") or not failed_ids
                or receipt.get("skill_binding") != binding or receipt.get("input_version") != self.version(epoch)
                or receipt.get("status") != "failed" or receipt.get("exit_code") != 0
                or receipt.get("execution_ok") is not True or not self._receipt_files_current(receipt)):
            return None
        try:
            inputs = [{"path": path, "sha256": file_digest(Path(path))} for path in self.decision.get("inputs", [])]
        except (OSError, TypeError, ValueError):
            return None
        return {"outcome": "requirement_failure", "case_id": self._case_id(binding),
                "skill_binding": binding, "requirement_ids": failed_ids, "execution_ok": True,
                "targets": receipt["targets"], "inputs": inputs,
                "reports": [{"path": receipt["report_path"], "sha256": receipt["report_sha256"]}],
                "input_version": receipt["input_version"]}

    def learning_observations(self, epoch: int) -> list[dict[str, Any]]:
        observations = [dict(item) for item in self.learning_history]
        success = self.learning_evidence(epoch)
        if success:
            case_id = self._case_id(success["skill_binding"])
            observations.append({**success, "outcome": "verified_success", "case_id": case_id,
                "execution_ok": True,
                "requirement_ids": [item["id"] for item in self.contract.get("requirements", [])
                                    if item.get("mandatory", True)],
                "recovery_of": sorted({item["case_id"] for item in observations
                    if item.get("outcome") == "requirement_failure" and item.get("case_id") == case_id})})
        return observations
