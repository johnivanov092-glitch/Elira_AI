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


def command_digest(command: str, *, argv: list[str] | None = None) -> str:
    """Keep arguments and business numbers exact; do not normalize shell code."""
    return _digest({"argv": argv} if argv is not None else {"command": command})


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


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

    @classmethod
    def from_snapshot(cls, value: Any) -> "CommandProgress":
        tracker = cls()
        if not isinstance(value, dict) or value.get("version") != 1:
            return tracker
        epoch = value.get("epoch")
        if type(epoch) not in (str, int, type(None)):
            return tracker
        tracker._epoch = epoch
        revision = value.get("revision", 0)
        tracker._revision = revision if type(revision) is int and revision >= 0 else 0
        diagnostics = value.get("diagnostics")
        if isinstance(diagnostics, dict):
            tracker._diagnostics = {key: result for key, result in diagnostics.items()
                                    if _valid_digest(key) and _valid_digest(result)}
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
            row = tracker._results.get(key) if isinstance(key, str) else None
            if row and row["notified"] and row["result"] == result and notice["text"]:
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
        }

    def context(self) -> str:
        """Active advice to pin across Resume until its evidence changes."""
        return self._notice.get("text", "")

    def _sync_epoch(self, epoch: str | int) -> None:
        if type(epoch) not in (str, int):
            raise ValueError("Command progress epoch must identify the code/input version")
        if self._epoch != epoch:
            self._epoch = epoch
            self._results.clear()
            self._notice.clear()
            self._diagnostics.clear()
            self._revision = 0

    def before_dispatch(
        self, tool_name: str, arguments: dict[str, Any], *, cwd: str, epoch: str | int = 0,
    ) -> dict[str, Any] | None:
        """Stop another unchanged execution, while permitting distinct diagnosis.

        This is a recovery boundary, not a permission check or a business-result
        verifier. Logs/status of existing jobs remain available without limits.
        A newly observed diagnostic authorizes one probe of external state; two
        unchanged probes exhaust recovery for this exact command/input version.
        """
        self._sync_epoch(epoch)
        if tool_name not in {"run_bash", "run_server", "sandbox_run"}:
            return None
        if tool_name == "run_server" and arguments.get("action", "start") != "start":
            return None
        command = arguments.get("code" if tool_name == "sandbox_run" else "command")
        if not isinstance(command, str) or not command or not cwd:
            return None
        command_hash = command_digest(command if tool_name == "sandbox_run" else command.strip())
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
        if (execution_status == "ok" and output.get("ok", True)
                and tool_name in {"read_file", "grep", "glob", "project_map", "web_fetch", "web_search"}):
            key = _digest({"tool": tool_name, "arguments": arguments, "cwd": cwd})
            result = _digest(output)
            if self._diagnostics.get(key) != result:
                self._diagnostics[key] = result
                self._revision += 1
        # The kernel reports an executed nonzero process as "error". Typed
        # terminal evidence below distinguishes it from dispatch/preflight errors.
        if execution_status not in {"ok", "error"} or tool_name not in {"run_server", "run_bash", "sandbox_run"}:
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
            command = arguments.get("code" if tool_name == "sandbox_run" else "command")
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
