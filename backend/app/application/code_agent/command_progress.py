"""Run-local tool repetition and command-result evidence in the existing loop.

Call identity is independent of output and code mutations. Terminal command
results retain their separate epoch-scoped evidence; a job poll is not another
execution in that ledger. Both survive Resume in the existing run journal.
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


_VOLATILE_RESULT_FIELDS = {"record_path", "record_sha256", "started_at", "finished_at"}


def _canonical_command(value: str) -> str:
    """Collapse only unquoted spaces/tabs; preserve data and shell newlines."""
    chars: list[str] = []
    quote = ""
    escaped = False
    pending_space = False
    for char in value:
        if not quote and not escaped and char in {" ", "\t"}:
            pending_space = True
            continue
        if pending_space:
            if chars:
                chars.append(" ")
            pending_space = False
        if escaped:
            chars.append(char)
            escaped = False
        elif char in {"\\", "^"}:
            chars.append(char)
            escaped = True
        elif quote:
            chars.append(char)
            if char == quote:
                quote = ""
        elif char in {"'", '"'}:
            chars.append(char)
            quote = char
        else:
            chars.append(char)
    return "".join(chars)


def _canonical_arguments(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: (_canonical_command(item) if key == "command" and isinstance(item, str)
                      else _canonical_arguments(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_arguments(item) for item in value]
    return value


def _normalized_result(value: Any) -> Any:
    """Drop receipt metadata from JSON, including JSON emitted in stdout."""
    if isinstance(value, dict):
        return {key: _normalized_result(item) for key, item in value.items()
                if key not in _VOLATILE_RESULT_FIELDS}
    if isinstance(value, list):
        return [_normalized_result(item) for item in value]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return value
        if isinstance(decoded, (dict, list)):
            return _normalized_result(decoded)
    return value


def _call_key(tool_name: str, arguments: Any) -> str:
    return _digest({"tool": tool_name, "arguments": _canonical_arguments(arguments)})


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

class CommandProgress:
    """Count canonical tool calls and preserve typed command observations."""

    def __init__(self) -> None:
        self._epoch: str | int | None = None
        self._results: dict[str, dict[str, Any]] = {}
        self._attempts: dict[str, dict[str, Any]] = {}
        self._notice: dict[str, str] = {}
        self._revision = 0
        self._diagnostics: dict[str, str] = {}
        self._calls: dict[str, int] = {}
        self._sequence: list[str] = []
        self._pending_call: str | None = None
        self._call_notice: dict[str, str] = {}

    @classmethod
    def from_snapshot(cls, value: Any) -> "CommandProgress":
        tracker = cls()
        if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] not in (1, 2):
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
            if row and row.get("notified", bool(row.get("refusals"))) and row["result"] == result and notice["text"]:
                tracker._notice = {"key": key, "result": result, "text": notice["text"]}
        calls = value.get("calls")
        if isinstance(calls, dict):
            tracker._calls = {key: count for key, count in calls.items()
                              if _valid_digest(key) and type(count) is int and count > 0}
        sequence = value.get("sequence")
        if isinstance(sequence, list):
            tracker._sequence = [key for key in sequence[-6:] if _valid_digest(key) and key in tracker._calls]
        call_notice = value.get("call_notice")
        if (isinstance(call_notice, dict) and isinstance(call_notice.get("text"), str)
                and _valid_digest(call_notice.get("key"))
                and tracker._calls.get(call_notice["key"], 0) >= 3):
            tracker._call_notice = {"key": call_notice["key"], "text": call_notice["text"]}
        return tracker

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 2,
            "epoch": self._epoch,
            "results": {key: dict(value) for key, value in self._results.items()},
            "attempts": {key: dict(value) for key, value in self._attempts.items()},
            "notice": dict(self._notice),
            "revision": self._revision,
            "diagnostics": dict(self._diagnostics),
            "calls": dict(self._calls),
            "sequence": list(self._sequence),
            "call_notice": dict(self._call_notice),
        }

    def context(self) -> str:
        """Active advice to pin across Resume until another strategy is chosen."""
        return self._call_notice.get("text", "") or self._notice.get("text", "")

    def apply_user_clarification(self) -> None:
        """New owner-supplied inputs permit another strategy after a blocked run."""
        self._calls.clear()
        self._sequence.clear()
        self._pending_call = None
        self._call_notice.clear()
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
        self, tool_name: str, arguments: Any, *, cwd: str, epoch: str | int = 0,
        model_turn: int | None = None,
    ) -> dict[str, Any] | None:
        """Warn on call three; stop before call five in a row or six overall."""
        self._sync_epoch(epoch)
        key = _call_key(tool_name, arguments)
        if self._call_notice.get("key") != key:
            self._call_notice.clear()
        count = self._calls.get(key, 0) + 1
        self._calls[key] = count
        self._sequence = (self._sequence + [key])[-6:]
        consecutive = 0
        for previous in reversed(self._sequence):
            if previous != key:
                break
            consecutive += 1
        alternating = (len(self._sequence) == 6 and self._sequence[0] != self._sequence[1]
                       and self._sequence[::2] == [self._sequence[0]] * 3
                       and self._sequence[1::2] == [self._sequence[1]] * 3)
        self._pending_call = key
        if consecutive < 5 and count < 6 and not alternating:
            return None
        self._pending_call = None
        limit = "чередование A-B-A-B-A-B" if alternating else (
            f"{consecutive} подряд" if consecutive >= 5 else f"{count} за прогон")
        text = (
            f"[ВОССТАНОВЛЕНИЕ ХОДА ЗАДАЧИ] Повтор {tool_name} не исполнен: {limit}. "
            "Задача не завершена: повторная стратегия исчерпана. "
            "Уже полученные результаты сохранены; их корректность не оценивалась. "
            "Прогон приостановлен (blocked), Resume доступен: измени запрос/способ "
            "либо добавь новые данные и продолжи по уже полученному."
        )
        self._call_notice = {"key": key, "text": text}
        return {"status": "blocked", "reason": "repeated_tool_call", "call_sha256": key,
                "call_count": count, "consecutive_calls": consecutive, "alternating_cycle": alternating,
                "business_outcome": "not_assessed", "text": text}

    def observe(
        self,
        tool_name: str,
        arguments: Any,
        output: dict[str, Any],
        *,
        execution_status: str = "ok",
        epoch: str | int = 0,
        cwd: str = "",
    ) -> str | None:
        call_hint = None
        key = _call_key(tool_name, arguments)
        if self._pending_call == key:
            self._pending_call = None
            count = self._calls[key]
            if count == 3:
                call_hint = (
                    f"[ВОССТАНОВЛЕНИЕ ХОДА ЗАДАЧИ] Этот вызов {tool_name} уже выполнялся "
                    f"{count} раза, результат выше; измени запрос/способ или отвечай по тому, что есть."
                )
                self._call_notice = {"key": key, "text": call_hint}
        result_hint = self._observe_command_result(
            tool_name, arguments, output, execution_status=execution_status, epoch=epoch, cwd=cwd,
        )
        return call_hint or result_hint

    def _observe_command_result(
        self, tool_name: str, arguments: dict[str, Any], output: dict[str, Any], *,
        execution_status: str, epoch: str | int, cwd: str,
    ) -> str | None:
        self._sync_epoch(epoch)
        if (execution_status == "ok" and output.get("ok", True)
                and tool_name in {"read_file", "grep", "glob", "project_map"}):
            key = _digest({"tool": tool_name, "arguments": arguments, "cwd": cwd})
            result = _digest(_normalized_result(output))
            if self._diagnostics.get(key) != result:
                self._diagnostics[key] = result
                self._revision += 1
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
        # Typed text takes precedence over the raw digest: skill receipt paths
        # and timestamps must not make otherwise identical results look new.
        if isinstance(output.get("stdout"), str) and isinstance(output.get("stderr"), str):
            output_hash = _digest(_normalized_result({"stdout": output["stdout"], "stderr": output["stderr"]}))
        else:
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
        if previous["count"] < 3 or previous["notified"]:
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
