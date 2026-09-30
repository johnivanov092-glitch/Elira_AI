"""Observe the release owner and forward an explicit UI installation decision."""
from __future__ import annotations

import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from functools import lru_cache

from app.core import release_runtime


LOG = logging.getLogger(__name__)
_PHASES = frozenset({"idle", "preparing", "prepared", "checking", "verified", "awaiting_confirmation", "waiting",
                     "switching", "completed", "rolling_back", "failed", "interrupted", "unavailable"})
_cache_lock = threading.Lock()
_cache: dict[tuple[str, int], tuple[float, dict]] = {}


class ReleaseConfirmationError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def _platform() -> Path:
    return Path(os.getenv("ELIRA_PLATFORM_ROOT") or Path(__file__).resolve().parents[4]).resolve()


def confirm_release(*, request_id: str, port: int) -> None:
    """The owner rechecks the proposal nonce and seal; UI data grants no trust."""
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ReleaseConfirmationError("Некорректный запрос подтверждения.", 422)
    platform = _platform()
    try:
        command = _release_module()._foundation_client_command(platform=platform, port=port)
        foundation = command is not None
        if foundation:
            command = [*command, "confirm", request_id, "--wait", "--timeout", "120"]
        else:
            command = [sys.executable, "-I", "-B",
                       str(Path(__file__).resolve().parents[4] / "scripts/elira_release.py"),
                       "--platform", str(platform), "--port", str(port), "confirm", request_id]
        response = subprocess.run(command, cwd=platform, capture_output=True, text=True,
                                  encoding="utf-8", timeout=130, check=False,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if response.returncode == 2:
            raise ReleaseConfirmationError("Подтверждение ещё обрабатывается. Проверяем состояние установки.", 504)
        if response.returncode != 0:
            LOG.warning("Release confirmation was refused by owner (exit %s)", response.returncode)
            raise ReleaseConfirmationError("Не удалось подтвердить это предложение. Обновите состояние: версия могла измениться или выполняется другая операция.", 409)
        result = json.loads(response.stdout)
        if not isinstance(result, dict) or (foundation and result.get("status") != "completed"):
            raise ValueError("Invalid release confirmation response")
    except subprocess.TimeoutExpired as exc:
        raise ReleaseConfirmationError("Ответ не получен: подтверждение могло быть принято. Проверяем состояние установки.", 504) from exc
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        if isinstance(exc, ReleaseConfirmationError):
            raise
        LOG.warning("Cannot confirm application release: %s", exc)
        raise ReleaseConfirmationError("Не удалось получить результат подтверждения. Проверьте состояние установки.", 503) from exc
    finally:
        with _cache_lock:
            _cache.clear()


@lru_cache(maxsize=1)
def _release_module():
    # Load the observer shipped with this application, never code from a caller
    # supplied project or candidate. Import starts no worker or release manager.
    script = Path(__file__).resolve().parents[4] / "scripts/elira_release.py"
    spec = importlib.util.spec_from_file_location("elira_release_status_owner", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("Release observer is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rollback_release(*, active_release_id: str, previous_release_id: str, port: int) -> None:
    """An explicit UI action selects an exact pair; the owner checks it under its state lock."""
    try:
        _identifier(active_release_id)
        _identifier(previous_release_id)
        if not active_release_id or not previous_release_id or active_release_id == previous_release_id:
            raise ValueError("Invalid rollback selection")
        current = get_release_status(port=port)
        if (not current.get("rollback_available") or current["active_release_id"] != active_release_id
                or current["previous_release_id"] != previous_release_id):
            raise ReleaseConfirmationError("Версия изменилась или откат сейчас недоступен. Обновите состояние.", 409)
        platform = _platform()
        command = _release_module()._foundation_client_command(platform=platform, port=port)
        foundation = command is not None
        if command is None:
            command = [sys.executable, "-I", "-B", str(Path(__file__).resolve().parents[4] / "scripts/elira_release.py"),
                       "--platform", str(platform), "--port", str(port)]
        command = [*command, "rollback", "--expected-active", active_release_id,
                   "--expected-previous", previous_release_id, "--confirm"]
        if foundation:
            command.extend(["--wait", "--timeout", "120"])
        response = subprocess.run(command, cwd=platform, capture_output=True, text=True, encoding="utf-8",
                                  timeout=130, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if response.returncode == 2:
            raise ReleaseConfirmationError("Откат ещё обрабатывается. Проверяем состояние.", 504)
        if response.returncode != 0:
            raise ReleaseConfirmationError("Откат не подтверждён: выбранная версия изменилась или выполняется другая операция.", 409)
        result = json.loads(response.stdout)
        if not isinstance(result, dict) or (foundation and result.get("status") != "completed"):
            raise ValueError("Invalid rollback response")
    except subprocess.TimeoutExpired as exc:
        raise ReleaseConfirmationError("Ответ не получен: откат мог быть принят. Проверьте состояние.", 504) from exc
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        if isinstance(exc, ReleaseConfirmationError):
            raise
        LOG.warning("Cannot request application rollback: %s", exc)
        raise ReleaseConfirmationError("Не удалось получить результат отката. Проверьте состояние.", 503) from exc
    finally:
        with _cache_lock:
            _cache.clear()


def _identifier(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid release identifier")
    _release_module().ReleaseManager._validate_id(value)
    return value


def _public_view(mode: str, state: dict, progress: dict) -> dict:
    if progress.get("version") != 1 or progress.get("phase") not in _PHASES:
        raise ValueError("Unsupported release progress record")
    health = release_runtime.health_fields()
    running = health.get("release_id")
    active = _identifier(running) if running and running != "development" else None
    selected = _identifier(state.get("active"))
    previous = _identifier(state.get("previous"))
    pending = _identifier(state.get("pending"))
    accepted = state.get("last_confirmation")
    pending_approved = bool(pending and isinstance(accepted, dict)
                            and accepted.get("release_id") == pending
                            and isinstance(accepted.get("request_id"), str)
                            and re.fullmatch(r"[a-f0-9]{32}", accepted["request_id"])
                            and isinstance(accepted.get("sha256"), str)
                            and re.fullmatch(r"[a-f0-9]{64}", accepted["sha256"]))
    confirmation = state.get("confirmation")
    if confirmation is not None:
        if (not isinstance(confirmation, dict)
                or not isinstance(confirmation.get("request_id"), str)
                or not re.fullmatch(r"[a-f0-9]{32}", confirmation["request_id"])
                or not isinstance(confirmation.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", confirmation["sha256"])
                or type(confirmation.get("requested_at")) not in (int, float)
                or not 0 < confirmation["requested_at"] < 1e12):
            raise ValueError("Invalid installation proposal")
        confirmation = {"request_id": confirmation["request_id"],
                        "release_id": _identifier(confirmation.get("release_id")),
                        "sha256": confirmation["sha256"], "requested_at": confirmation["requested_at"]}
        if confirmation["release_id"] is None:
            raise ValueError("Installation proposal has no release identifier")
    target = _identifier(progress.get("release_id"))
    phase = progress["phase"]
    transition = state.get("transition")
    transition_phase = transition.get("phase") if isinstance(transition, dict) else transition
    if isinstance(transition, dict) and transition.get("to"):
        target = _identifier(transition["to"])
    # An earlier failed candidate can leave state.error while a new candidate
    # is being prepared. Its error must not replace this operation's evidence.
    error = progress.get("error")
    if phase == "idle":
        error = error or state.get("error") or state.get("last_error")
    if error is not None and not isinstance(error, str):
        raise ValueError("Invalid release error")
    if phase != "unavailable":
        if error and phase not in {"preparing", "checking", "rolling_back", "interrupted"}:
            phase = "failed"
        elif transition_phase and phase not in {"failed", "interrupted", "rolling_back"}:
            phase = "switching"
        elif pending and phase not in {"failed", "interrupted", "preparing", "checking", "rolling_back"}:
            if pending_approved:
                phase, target = "waiting", pending
            else:
                phase, error = "interrupted", "Установка в очереди не имеет подтверждения пользователя."
        elif phase == "awaiting_confirmation" and not (
            confirmation and confirmation["release_id"] == target
            and confirmation["request_id"] == progress.get("operation_id")
        ):
            phase, error = "interrupted", "Предложение установки больше не актуально."
        elif phase == "waiting":
            if selected == target and active == target and health.get("admitted") and not health.get("draining"):
                phase = "completed"
            else:
                phase, error = "interrupted", "Ожидаемая установка больше не находится в очереди."
        elif phase == "completed" and not (
            target is not None and selected == target and active == target
            and health.get("admitted") is True and health.get("draining") is False
        ):
            # A saved selection/build is not proof this backend was admitted.
            phase = "switching" if selected == target and target else "interrupted"
            if phase == "interrupted":
                error = "Запуск указанной версии не подтверждён."
    step = progress.get("step")
    if step is not None:
        if (not isinstance(step, dict) or type(step.get("index")) is not int
                or type(step.get("total")) is not int or not 1 <= step["index"] <= step["total"] <= 100
                or not isinstance(step.get("label"), str) or not step["label"].strip()):
            raise ValueError("Invalid release check progress")
        step = {"index": step["index"], "total": step["total"], "label": step["label"][:200]}
    updated = progress.get("updated_at")
    if updated is not None and (type(updated) not in (int, float) or not 0 < updated < 1e12):
        raise ValueError("Invalid release progress timestamp")
    operation_id = progress.get("operation_id")
    if operation_id is not None and (not isinstance(operation_id, str) or len(operation_id) > 128):
        raise ValueError("Invalid release operation identifier")
    rollback_available = bool(mode != "development" and active and active == selected and previous and previous != active
                              and health.get("admitted") is True and health.get("draining") is False
                              and not pending and not transition_phase and not confirmation
                              and phase not in {"preparing", "checking", "waiting", "switching", "rolling_back", "unavailable"})
    saved_action = state.get("saved_release_action")
    if not isinstance(saved_action, str) or saved_action not in {"update", "rollback"}:
        saved_action = _release_module().saved_release_action(state)
    return {"version": 1, "mode": mode, "phase": phase, "active_release_id": active,
            "previous_release_id": previous, "rollback_available": rollback_available,
            "saved_release_action": saved_action,
            "target_release_id": target, "operation_id": operation_id, "updated_at": updated,
            "step": step, "error": error[:1000] if error else None, "confirmation": confirmation}


def _read_status(platform: Path, port: int) -> dict:
    mode = "development"
    try:
        module = _release_module()
        command = module._foundation_client_command(platform=platform, port=port)
        if command is not None:
            mode = "foundation"
            response = subprocess.run(
                [*command, "status"], cwd=platform, capture_output=True, text=True,
                encoding="utf-8", timeout=4, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            state = json.loads(response.stdout)
            if not isinstance(state, dict) or not isinstance(state.get("progress"), dict):
                raise ValueError("Installed Foundation does not report release progress")
            progress = state["progress"]
        else:
            store = platform / ".runtime/releases"
            state_path = store / "state.json"
            mode = "legacy" if state_path.exists() or (store / "progress.json").exists() else "development"
            if state_path.exists():
                if state_path.stat().st_size > 64 * 1024:
                    raise ValueError("Release state record is too large")
                state = json.loads(state_path.read_text(encoding="utf-8"))
            else:
                state = {}
            if not isinstance(state, dict):
                raise ValueError("Invalid release state record")
            progress = module.read_release_progress(store)
        return _public_view(mode, state, progress)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        LOG.warning("Cannot observe application release status: %s", exc)
        return {"version": 1, "mode": mode, "phase": "unavailable", "active_release_id": None,
                "previous_release_id": None, "rollback_available": False,
                "target_release_id": None, "operation_id": None, "updated_at": None,
                "step": None, "confirmation": None,
                "error": "Не удалось прочитать состояние обновления. Рабочая версия не изменялась этим запросом."}


def get_release_status(*, port: int) -> dict:
    platform = _platform()
    key = (str(platform), port)
    # One bounded observation is shared by concurrent UI polls. Cache only a
    # shallow JSON snapshot; never retain a token, process or manager instance.
    with _cache_lock:
        previous = _cache.get(key)
        if previous and time.monotonic() - previous[0] < 2:
            return dict(previous[1])
        value = _read_status(platform, port)
        _cache.clear()
        _cache[key] = (time.monotonic(), value)
        return dict(value)
