from __future__ import annotations

from pathlib import Path
from typing import Any

# ─── computer use (desktop control) ───────────────────────────────────────────
#
# A single `computer` tool that mirrors Claude's computer-use: take a screenshot
# and drive the mouse/keyboard. The screenshot is interpreted by the server
# vision model (MiniCPM-V, :8004) so the text-only code model can "see" the
# screen and decide where to act; input control uses pyautogui.
#
# Runtime constraints:
#   • pyautogui is imported LAZILY inside the handler — the module must import
#     cleanly on a headless host (CI, a server with no display) where pyautogui
#     would fail at import. Absence is surfaced as a non-fatal ERROR string.
#   • Product authorization comes only from the composer's Workflow permission
#     mode; in `bypass` there is no additional computer-tool approval.
#   • Coordinate grounding from a vision model is approximate — the screenshot
#     reports the screen size so the agent reasons in the real pixel space, and
#     the description prompt asks for element positions.

_DEFAULT_SCREEN_PROMPT = (
    "Это скриншот экрана рабочего стола. Опиши, что на нём видно: окна, кнопки, "
    "поля ввода, меню и их примерное расположение (слева/справа/сверху/снизу и "
    "приблизительные координаты в пикселях). Если есть текст — приведи его."
)

_VALID_ACTIONS = {
    "screenshot",
    "left_click",
    "right_click",
    "double_click",
    "middle_click",
    "move",
    "type",
    "key",
    "scroll",
}


def _load_pyautogui() -> tuple[Any, str | None]:
    """Import pyautogui on demand; return (module, error). Never raises."""
    try:
        import pyautogui  # type: ignore
        # Move the mouse to a screen corner to abort a runaway loop.
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        return pyautogui, None
    except Exception as exc:  # pragma: no cover - depends on host display/deps
        return None, str(exc)


def _grab_png() -> tuple[bytes | None, tuple[int, int] | None, str | None]:
    """Capture the primary screen as PNG. Tries pyautogui, then PIL.ImageGrab."""
    import io
    try:
        try:
            import pyautogui  # type: ignore
            img = pyautogui.screenshot()
        except Exception:
            from PIL import ImageGrab  # type: ignore
            img = ImageGrab.grab()
        size = (int(img.width), int(img.height))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), size, None
    except Exception as exc:  # pragma: no cover - depends on host display
        return None, None, str(exc)


def _coerce_xy(x: Any, y: Any) -> tuple[int, int] | None:
    try:
        return int(x), int(y)
    except (TypeError, ValueError):
        return None


def tool_computer(
    project_root: Path,
    *,
    action: str = "screenshot",
    x: Any = None,
    y: Any = None,
    text: str = "",
    keys: Any = None,
    amount: int = 3,
    direction: str = "down",
    clicks: int = 1,
    prompt: str = "",
) -> dict[str, Any]:
    """Control the desktop: screenshot (vision-described) + mouse/keyboard.

    Actions: screenshot | left_click | right_click | double_click | middle_click
    | move | type | key | scroll. Coordinates (x, y) are absolute pixels.
    """
    act = (action or "screenshot").strip().lower()
    if act not in _VALID_ACTIONS:
        return {
            "ok": False,
            "error": "unknown_action",
            "text": f"ERROR: unknown action '{action}'. Valid: {', '.join(sorted(_VALID_ACTIONS))}.",
        }

    # ── screenshot: capture + let the vision model interpret it ──────────────
    if act == "screenshot":
        png, size, err = _grab_png()
        if png is None:
            return {"ok": False, "error": "capture_failed", "text": f"ERROR: could not capture screen: {err}"}
        try:
            from app.infrastructure.llm.vision_ocr import describe_image
        except Exception as exc:  # pragma: no cover - import guard
            return {"ok": False, "error": "vision_unavailable", "text": f"ERROR: vision support unavailable: {exc}"}
        w, h = size or (0, 0)
        description = describe_image("screen.png", png, prompt=(prompt or _DEFAULT_SCREEN_PROMPT))
        if not description:
            return {
                "ok": False,
                "error": "vision_empty",
                "text": f"Скриншот сделан ({w}×{h} px), но зрение не вернуло описание (сервис недоступен).",
            }
        return {"ok": True, "text": f"Скриншот рабочего стола ({w}×{h} px):\n{description}"}

    # Every non-screenshot action needs real input control.
    gui, err = _load_pyautogui()
    if gui is None:
        return {
            "ok": False,
            "error": "input_unavailable",
            "text": (
                "ERROR: управление вводом недоступно — не удалось загрузить pyautogui "
                f"({err}). Установи пакет в окружение бэкенда."
            ),
        }

    try:
        if act in {"left_click", "right_click", "double_click", "middle_click", "move"}:
            xy = _coerce_xy(x, y)
            if xy is None:
                return {"ok": False, "error": "invalid_coordinates", "text": f"ERROR: action '{act}' requires integer x and y."}
            px, py = xy
            if act == "move":
                gui.moveTo(px, py)
                return {"ok": True, "text": f"Курсор перемещён в ({px}, {py})."}
            button = "right" if act == "right_click" else "middle" if act == "middle_click" else "left"
            n = 2 if act == "double_click" else max(1, int(clicks or 1))
            gui.click(x=px, y=py, clicks=n, button=button)
            return {"ok": True, "text": f"Клик {button}×{n} в ({px}, {py})."}

        if act == "type":
            if not text:
                return {"ok": False, "error": "text_required", "text": "ERROR: action 'type' requires non-empty text."}
            gui.write(str(text), interval=0.01)
            return {"ok": True, "text": f"Введён текст ({len(str(text))} симв.)."}

        if act == "key":
            combo = keys if isinstance(keys, list) else ([keys] if isinstance(keys, str) and keys else [])
            combo = [str(k).strip().lower() for k in combo if str(k).strip()]
            if not combo:
                return {"ok": False, "error": "keys_required", "text": "ERROR: action 'key' requires `keys` (e.g. [\"ctrl\",\"c\"] or [\"enter\"])."}
            if len(combo) == 1:
                gui.press(combo[0])
            else:
                gui.hotkey(*combo)
            return {"ok": True, "text": f"Нажато: {'+'.join(combo)}."}

        if act == "scroll":
            step = abs(int(amount or 3)) * 100
            dy = -step if str(direction).strip().lower() == "down" else step
            xy = _coerce_xy(x, y)
            if xy is not None:
                gui.moveTo(*xy)
            gui.scroll(dy)
            return {"ok": True, "text": f"Прокрутка {direction} на {abs(int(amount or 3))} шага."}
    except Exception as exc:  # pragma: no cover - runtime input failure
        return {"ok": False, "error": "action_failed", "text": f"ERROR: действие '{act}' не выполнено: {exc}"}

    return {"ok": False, "error": "action_not_handled", "text": f"ERROR: action '{act}' not handled."}
