"""Run-local command observations and bounded recovery in the existing loop.

The agent loop owns the input/code epoch and persists ``snapshot()`` in its
existing journal. Process IDs identify attempts only: they never establish
progress. A poll of an existing job is not another execution, including after
Resume. Only identical terminal outcomes from distinct attempts are compared.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urldefrag


def command_digest(command: str, *, argv: list[str] | None = None) -> str:
    """Keep arguments and business numbers exact; do not normalize shell code."""
    return _digest({"argv": argv} if argv is not None else {"command": command})


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


WEB_REPEAT_TOOLS = frozenset({"web_search", "web_fetch", "web_query", "browser"})
WEB_REPEAT_HINT_AT = 2


def _document_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 8192 or not value.startswith(("https://", "http://")):
        return None
    try:
        return urldefrag(value)[0]
    except ValueError:
        # The tool owns URL validation; repeat detection must not bypass it.
        return None


def _web_arguments(arguments: dict[str, Any], whole_documents: set[str] | None = None, *, tool_name: str = "") -> dict[str, Any]:
    """Ordering a parallel batch differently is not a new retrieval strategy."""
    from app.application.code_agent.tools._web import normalize_web_tool_arguments

    normalized = normalize_web_tool_arguments(tool_name, arguments)
    def resource(value: Any) -> Any:
        base = _document_url(value)
        return base if whole_documents and base in whole_documents else value
    if whole_documents:
        if "url" in normalized:
            normalized["url"] = resource(normalized["url"])
        if isinstance(normalized.get("urls"), list):
            normalized["urls"] = [resource(value) for value in normalized["urls"]]
    for field in ("queries", "urls"):
        values = normalized.get(field)
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            normalized[field] = sorted(values)
    return normalized


def _web_result(output: dict[str, Any]) -> str:
    """Compare retrieved evidence, excluding rank, timestamps and engine noise."""
    pages = output.get("pages")
    if (output.get("ok") is False and isinstance(pages, list) and pages
            and all(isinstance(page, dict) and type(page.get("status_code")) is int
                    and page["status_code"] >= 400 for page in pages)):
        # Cookies/redirect URLs and verbose error strings do not turn another
        # HTTP 429/503 for the same requested pages into a different outcome.
        return _digest({"http_failures": sorted([
            {"url": page.get("url"), "status": page["status_code"]} for page in pages], key=_digest)})
    sources = output.get("sources")
    if isinstance(sources, list) and sources and all(isinstance(item, dict) and item.get("url") for item in sources):
        rows = []
        for item in sources:
            # Retrieval URLs may gain a fresh redirect nonce on every read.
            # For actual excerpts, the bytes identify new evidence; search hits
            # still need their URL because they have no retrieved text hash.
            fields = ["status", "title", "content_hash", "excerpt_hash", "error"]
            if not (item.get("status") == "excerpt" and _valid_digest(item.get("excerpt_hash"))):
                fields.append("url")
            rows.append({field: item.get(field) for field in fields})
        return _digest({"ok": output.get("ok", True), "sources": sorted(rows, key=_digest)})
    text = output.get("text")
    return _digest({"ok": output.get("ok", True), "result": text if isinstance(text, str) else output})


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _source_handles(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and 0 < len(item) <= 80))[:128]


def _web_failure_hint(tool_name: str) -> str:
    fallback = ("При отказе HTTP попробуй browser для этого URL, если доступен, либо другой найденный источник. "
                if tool_name == "web_fetch" else "Попробуй другой найденный источник. ")
    return (
        "[ОШИБКА ВЕБ-ЧТЕНИЯ] Ошибки доступа не являются содержимым статьи. "
        "Используй точный URL из поиска или запроса пользователя; не угадывай варианты пути. "
        + fallback + "Если чтение недоступно, явно укажи этот пробел в ответе."
    )


class CommandProgress:
    """Compare exact observed results within one code/input version.

    A notice says that observed output repeated, not that business work failed.
    Changed artifacts/inputs must advance the caller's epoch. Missing typed
    execution/output evidence is ignored rather than inferred from prose.
    """

    def __init__(self) -> None:
        self._epoch: str | int | None = None
        self._results: dict[str, dict[str, Any]] = {}
        self._attempts: dict[str, dict[str, Any]] = {}
        self._notice: dict[str, str] = {}
        self._revision = 0
        self._diagnostics: dict[str, str] = {}
        self._web_repeats: dict[str, dict[str, Any]] = {}
        self._whole_documents: set[str] = set()

    @classmethod
    def from_snapshot(cls, value: Any) -> "CommandProgress":
        tracker = cls()
        if not isinstance(value, dict) or value.get("version") != 1:
            return tracker
        epoch = value.get("epoch")
        if type(epoch) not in (str, int, type(None)):
            return tracker
        tracker._epoch = epoch
        documents = value.get("whole_documents")
        if isinstance(documents, list):
            tracker._whole_documents = {item for item in documents[:512]
                                       if _document_url(item) == item and isinstance(item, str) and "#" not in item}
        revision = value.get("revision", 0)
        tracker._revision = revision if type(revision) is int and revision >= 0 else 0
        diagnostics = value.get("diagnostics")
        if isinstance(diagnostics, dict):
            tracker._diagnostics = {key: result for key, result in diagnostics.items()
                                    if _valid_digest(key) and _valid_digest(result)}
        web_repeats = value.get("web_repeats")
        for key, row in web_repeats.items() if isinstance(web_repeats, dict) else ():
            if (_valid_digest(key) and isinstance(row, dict) and _valid_digest(row.get("result"))
                    and type(row.get("count")) is int and row["count"] > 0
                    and type(row.get("refusals", 0)) is int and row.get("refusals", 0) >= 0):
                tracker._web_repeats[key] = {"result": row["result"], "count": row["count"],
                    "tool": row.get("tool") if row.get("tool") in WEB_REPEAT_TOOLS else "",
                    "retryable": row.get("retryable") is not False,
                    "source_ids": _source_handles(row.get("source_ids")),
                    "refusals": row.get("refusals", 0),
                    "refusal_turn": row.get("refusal_turn") if type(row.get("refusal_turn")) is int else None,
                    "checked_revision": row.get("checked_revision", tracker._revision)
                    if type(row.get("checked_revision", tracker._revision)) is int else tracker._revision}
        results = value.get("results")
        for key, row in results.items() if isinstance(results, dict) else ():
            if (_valid_digest(key) and isinstance(row, dict)
                    and _valid_digest(row.get("result"))
                    and type(row.get("count")) is int and row["count"] > 0
                    and type(row.get("notified")) is bool):
                tracker._results[key] = {
                    "result": row["result"], "count": row["count"], "notified": row["notified"],
                }
                for field in ("checked_revision", "rechecks", "refusals"):
                    number = row.get(field, 0)
                    tracker._results[key][field] = number if type(number) is int and number >= 0 else 0
        attempts = value.get("attempts")
        for key, row in attempts.items() if isinstance(attempts, dict) else ():
            if (_valid_digest(key) and isinstance(row, dict)
                    and type(row.get("epoch")) in (str, int)
                    and type(row.get("terminal")) is bool):
                tracker._attempts[key] = {"epoch": row["epoch"], "terminal": row["terminal"]}
        notice = value.get("notice")
        if isinstance(notice, dict) and isinstance(notice.get("text"), str):
            key, result = notice.get("key"), notice.get("result")
            row = (tracker._results.get(key) or tracker._web_repeats.get(key)) if isinstance(key, str) else None
            if row and row.get("notified", bool(row.get("refusals"))) and row["result"] == result and notice["text"]:
                tracker._notice = {"key": key, "result": result, "text": notice["text"]}
        return tracker

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "epoch": self._epoch,
            "results": {key: dict(value) for key, value in self._results.items()},
            "attempts": {key: dict(value) for key, value in self._attempts.items()},
            "notice": dict(self._notice),
            "revision": self._revision,
            "diagnostics": dict(self._diagnostics),
            "web_repeats": {key: dict(value) for key, value in self._web_repeats.items()},
            "whole_documents": sorted(self._whole_documents),
        }

    def context(self) -> str:
        """Active advice to pin across Resume until its evidence changes."""
        return self._notice.get("text", "")

    def exhausted_web_tools(self) -> set[str]:
        """Report exhausted operations for diagnostics; never hide tool schemas.

        Other tools and the original task remain available. A model plan alone
        cannot reset this boundary. New diagnostics may retry a failed call;
        successful retrieval needs changed arguments, inputs or artifacts.
        """
        return {row["tool"] for row in self._web_repeats.values()
                if row.get("tool") in WEB_REPEAT_TOOLS - {"browser"} and row["refusals"] >= 2
                and (not row.get("retryable", True) or row["checked_revision"] >= self._revision)}

    def cached_source_ids(self, tool_name: str, arguments: dict[str, Any], *, epoch: str | int) -> list[str]:
        """Existing ledger handles for a repeated read, before refusal accounting."""
        self._sync_epoch(epoch)
        if tool_name not in {"web_fetch", "web_query"}:
            return []
        key = _digest({"tool": tool_name, "arguments": _web_arguments(arguments, self._whole_documents if tool_name == "web_fetch" else None, tool_name=tool_name)})
        row = self._web_repeats.get(key, {})
        if row.get("retryable", True) and self._revision > row.get("checked_revision", -1):
            return []
        return list(row.get("source_ids", []))

    def _web_repeat_hint(self, tool_name: str, arguments: dict[str, Any], output: dict[str, Any]) -> str | None:
        """Observable hint for an identical web call that returned identical text.

        Successful retrieval is reused immediately. A failed retrieval gets
        one retry; further unchanged dispatches require a different strategy.
        """
        key = _digest({"tool": tool_name, "arguments": _web_arguments(arguments, self._whole_documents if tool_name == "web_fetch" else None, tool_name=tool_name)})
        if tool_name == "browser" and (arguments.get("actions") or arguments.get("viewport") or output.get("ok", True)):
            # Interactive browser work and successful live DOM checks retain
            # their own semantics. Only repeated failed passive reads recover.
            self._web_repeats.pop(key, None)
            return None
        result = _web_result(output)
        source_ids = _source_handles([source.get("id") for source in output.get("sources", [])
                                     if isinstance(source, dict)]
                                    if isinstance(output.get("sources"), list) else [])
        row = self._web_repeats.get(key)
        if row is None or row["result"] != result:
            self._web_repeats[key] = {"tool": tool_name, "result": result, "count": 1, "refusals": 0,
                                      "retryable": not output.get("ok", True),
                                      "source_ids": source_ids,
                                      "checked_revision": self._revision}
            if self._notice.get("key") == key:
                self._notice.clear()
            if not output.get("ok", True) and tool_name in {"web_fetch", "browser"}:
                return _web_failure_hint(tool_name)
            return None
        row["count"] += 1
        row["source_ids"] = source_ids
        row["checked_revision"] = self._revision
        if row["count"] < WEB_REPEAT_HINT_AT:
            return None
        if row.get("retryable", True) and tool_name in {"web_fetch", "browser"}:
            return _web_failure_hint(tool_name)
        return (
            f"[ПОВТОР БЕЗ НОВЫХ ДАННЫХ] {tool_name} с теми же аргументами уже {row['count']}-й раз "
            "вернул тот же результат. Это не остановка задачи, но новых данных этот вызов не даст. "
            "Измени запрос (другие слова, язык, источник, период), прочитай уже найденные страницы "
            "или отвечай по уже собранным материалам."
        )

    def _sync_epoch(self, epoch: str | int) -> None:
        if type(epoch) not in (str, int):
            raise ValueError("Command progress epoch must identify the code/input version")
        if self._epoch != epoch:
            self._epoch = epoch
            self._results.clear()
            self._notice.clear()
            self._diagnostics.clear()
            self._web_repeats.clear()
            self._whole_documents.clear()
            self._revision = 0

    def before_dispatch(
        self, tool_name: str, arguments: dict[str, Any], *, cwd: str, epoch: str | int = 0,
        model_turn: int | None = None,
    ) -> dict[str, Any] | None:
        """Stop another unchanged execution, while permitting distinct diagnosis.

        This is a recovery boundary, not a permission check or a business-result
        verifier. Logs/status of existing jobs remain available without limits.
        A newly observed diagnostic authorizes one probe of external state; two
        unchanged probes exhaust recovery for this exact command/input version.
        """
        self._sync_epoch(epoch)
        if tool_name in WEB_REPEAT_TOOLS:
            key = _digest({"tool": tool_name, "arguments": _web_arguments(arguments, self._whole_documents if tool_name == "web_fetch" else None, tool_name=tool_name)})
            row = self._web_repeats.get(key)
            if row is None or row["count"] < (2 if row.get("retryable", True) else 1):
                return None
            if row.get("retryable", True) and self._revision > row["checked_revision"]:
                # New diagnostics permit one fresh probe of a failed call.
                # Another source cannot invalidate a successful retrieval.
                # No run-wide budget: new evidence can keep a long task useful.
                row["checked_revision"] = self._revision
                row["refusals"] = 0
                row["refusal_turn"] = None
                if self._notice.get("key") == key:
                    self._notice.clear()
                return None
            # A batch is one model decision. Its duplicates cannot ignore
            # feedback which the model has not yet received.
            if model_turn is None or row.get("refusal_turn") != model_turn:
                row["refusals"] += 1
                row["refusal_turn"] = model_turn
            status = ("blocked" if row["refusals"] >= 4 else
                      "strategy_required" if row["refusals"] >= 2 else "recovery_required")
            guidance = (_web_failure_hint(tool_name) + " "
                        if row.get("retryable", True) and tool_name in {"web_fetch", "browser"}
                        else "используй результат предыдущего вызова. ")
            text = (
                f"[ПОВТОР БЕЗ НОВЫХ ДАННЫХ] {tool_name} не исполнен: "
                + guidance + "Ограничение относится только к этим аргументам; "
                "другой запрос или источник доступен. Продолжай исходную задачу."
            )
            self._notice = {"key": key, "result": row["result"], "text": text}
            return {"status": status, "reason": "repeated_web_without_progress",
                    "observed_attempts": row["count"], "result_sha256": row["result"],
                    "source_ids": row.get("source_ids", []),
                    "business_outcome": "not_assessed", "text": text}
        if tool_name not in {"run_bash", "run_server"}:
            return None
        if tool_name == "run_server" and arguments.get("action", "start") != "start":
            return None
        command = arguments.get("command")
        if not isinstance(command, str) or not command or not cwd:
            return None
        command_hash = command_digest(command.strip())
        key = _digest({"tool": tool_name, "command": command_hash, "cwd": cwd})
        row = self._results.get(key)
        if row is None or row["count"] < 3:
            return None
        if row["rechecks"] < 2 and self._revision > row["checked_revision"]:
            row["checked_revision"] = self._revision
            row["rechecks"] += 1
            row["refusals"] = 0
            return None
        row["refusals"] += 1
        status = "blocked" if row["rechecks"] >= 2 or row["refusals"] >= 2 else "recovery_required"
        text = (
            f"[ВОССТАНОВЛЕНИЕ ХОДА ЗАДАЧИ] Повтор {tool_name} не исполнен: "
            f"{row['count']} отдельных запуска дали одинаковый наблюдаемый результат "
            "в том же каталоге и версии входов/кода. Корректность бизнес-результата не оценивалась. "
            "Сначала выполни другую диагностическую проверку: прочитай входы, проверь конкретный "
            "артефакт или внешнее состояние. Новый наблюдаемый результат разрешает одну контрольную "
            "попытку. Измени способ решения, если контрольная попытка снова ничего не изменила. "
            "Для работающей фоновой задачи используй run_server(action='logs') без нового запуска."
        )
        if status == "blocked":
            text += " Повторная стратегия исчерпана; задача приостановлена и доступна для Resume с новыми данными или другим способом."
        self._notice = {"key": key, "result": row["result"], "text": text}
        return {"status": status, "reason": "repeated_command_without_progress",
                "command_sha256": command_hash, "result_sha256": row["result"],
                "observed_attempts": row["count"], "rechecks": row["rechecks"],
                "business_outcome": "not_assessed", "text": text}

    def observe(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        output: dict[str, Any],
        *,
        execution_status: str = "ok",
        epoch: str | int = 0,
        cwd: str = "",
    ) -> str | None:
        self._sync_epoch(epoch)
        if tool_name == "web_fetch" and isinstance(output.get("pages"), list):
            for page in output["pages"]:
                if isinstance(page, dict) and page.get("mime") == "application/pdf":
                    for value in (page.get("url"), page.get("final_url")):
                        base = _document_url(value)
                        if base is not None and len(self._whole_documents) < 512:
                            self._whole_documents.add(base)
        if (execution_status == "ok" and output.get("ok", True)
                and tool_name in {"read_file", "grep", "glob", "project_map", "web_fetch", "web_search", "web_query", "browser"}):
            key = _digest({"tool": tool_name, "arguments": _web_arguments(arguments, self._whole_documents if tool_name == "web_fetch" else None, tool_name=tool_name)
                           if tool_name in WEB_REPEAT_TOOLS else arguments, "cwd": cwd})
            result = _web_result(output) if tool_name in WEB_REPEAT_TOOLS else _digest(output)
            if self._diagnostics.get(key) != result:
                already_seen = tool_name in WEB_REPEAT_TOOLS and result in self._diagnostics.values()
                self._diagnostics[key] = result
                if not already_seen:
                    self._revision += 1
        if execution_status in {"ok", "error"} and tool_name in WEB_REPEAT_TOOLS:
            return self._web_repeat_hint(tool_name, arguments, output)
        # The kernel reports an executed nonzero process as "error". Typed
        # terminal evidence below distinguishes it from dispatch/preflight errors.
        if execution_status not in {"ok", "error"} or tool_name not in {"run_server", "run_bash"}:
            return None

        attempt = None
        if tool_name == "run_server":
            if output.get("action") not in {"start", "logs"}:
                return None
            status = output.get("status")
            # Job UUID is durable across backend restarts. Legacy server records
            # need a creation token as well as PID to avoid PID reuse collisions.
            identity = output.get("job_id") or output.get("attempt_id")
            if not isinstance(identity, str) or not identity:
                return None
            attempt_key = _digest({"tool": tool_name, "attempt": identity})
            attempt = self._attempts.setdefault(attempt_key, {"epoch": epoch, "terminal": False})
            if attempt["terminal"] or status not in {"completed", "failed", "cancelled"}:
                return None
            if status == "cancelled" or attempt["epoch"] != epoch:
                attempt["terminal"] = True
                return None
        else:
            status = "completed" if output.get("exit_code") == 0 else "failed"
            if output.get("error") == "cancelled":
                return None

        exit_code = output.get("exit_code")
        if type(exit_code) is not int:
            return None
        command_hash = output.get("command_sha256")
        if not _valid_digest(command_hash):
            command = arguments.get("command")
            if not isinstance(command, str) or not command:
                return None
            command_hash = command_digest(command)
        directory = output.get("cwd") or cwd
        if not isinstance(directory, str) or not directory:
            return None
        output_hash = output.get("output_sha256")
        if not _valid_digest(output_hash):
            if not (isinstance(output.get("stdout"), str) and isinstance(output.get("stderr"), str)):
                return None
            output_hash = _digest({"stdout": output["stdout"], "stderr": output["stderr"]})
        if attempt is not None:
            attempt["terminal"] = True
        key = _digest({"tool": tool_name, "command": command_hash, "cwd": directory})
        result = _digest({"status": status, "exit_code": exit_code, "output": output_hash})
        if self._notice.get("key") == key and self._notice.get("result") != result:
            self._notice.clear()
        previous = self._results.get(key)
        if previous is None or previous["result"] != result:
            self._revision += 1
            self._results[key] = {"result": result, "count": 1, "notified": False,
                                  "checked_revision": self._revision, "rechecks": 0, "refusals": 0}
            return None
        previous["count"] += 1
        if previous["count"] == 3:
            # Recovery needs evidence observed after repetition was established.
            previous["checked_revision"] = self._revision
        if previous["notified"]:
            return None
        previous["notified"] = True
        hint = (
            f"[ВОССТАНОВЛЕНИЕ ХОДА ЗАДАЧИ] {tool_name}: {previous['count']} отдельных запуска "
            f"одной команды (SHA-256 {command_hash[:16]}) в том же рабочем каталоге и версии "
            f"входов/кода дали одинаковый наблюдаемый результат: status={status}, exit={exit_code}. "
            "Новый PID не означает продвижение. Это не оценка корректности данных и не остановка задачи. "
            "Сопоставь диагностический вывод и полученные файлы с ожидаемым результатом; "
            "исправь причину либо выбери другую проверку/способ. Если повтор нужен из-за изменения "
            "внешнего состояния, сначала проверь и зафиксируй это изменение."
        )
        self._notice = {"key": key, "result": result, "text": hint}
        return hint
