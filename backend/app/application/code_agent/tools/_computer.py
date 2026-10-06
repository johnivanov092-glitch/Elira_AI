from __future__ import annotations

import os
from pathlib import Path
import time
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

def _send_unicode_text(text: str) -> tuple[int, int]:
    """Type ``text`` on Windows via SendInput(KEYEVENTF_UNICODE): (sent, expected) events.

    pyautogui.write only maps ASCII 32–127 through the layout active at import, so
    Cyrillic was silently dropped (and Latin could come out as «руддщ» under the
    Russian layout) while the tool reported success. Unicode key events bypass the
    layout; Enter/Tab are real virtual keys.
    """
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _EVENT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("event", _EVENT)]

    input_keyboard, key_up, unicode_flag = 1, 0x0002, 0x0004
    send = ctypes.windll.user32.SendInput

    def press(vk: int = 0, scan: int = 0, flags: int = 0) -> int:
        pair = (INPUT * 2)()
        for item, extra in zip(pair, (0, key_up)):
            item.type = input_keyboard
            item.event.ki = KEYBDINPUT(vk, scan, flags | extra, 0, 0)
        return int(send(2, pair, ctypes.sizeof(INPUT)))

    sent = expected = 0
    for index, char in enumerate(text):
        if char == "\r" and text[index + 1:index + 2] == "\n":
            continue
        if char in "\r\n":
            expected += 2
            sent += press(vk=0x0D)
        elif char == "\t":
            expected += 2
            sent += press(vk=0x09)
        else:
            encoded = char.encode("utf-16-le")
            for offset in range(0, len(encoded), 2):  # surrogate pairs for non-BMP characters
                expected += 2
                sent += press(scan=int.from_bytes(encoded[offset:offset + 2], "little"), flags=unicode_flag)
        time.sleep(0.005)
    return sent, expected


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
            value = str(text)
            if os.name == "nt":
                sent, expected = _send_unicode_text(value)
                if sent < expected:
                    return {"ok": False, "error": "input_rejected",
                            "text": (f"ERROR: система приняла {sent} из {expected} событий ввода — текст введён "
                                     "не полностью (окно другого уровня прав или блокировка ввода).")}
            elif not value.isascii():
                return {"ok": False, "error": "unsupported_text",
                        "text": "ERROR: ввод не-ASCII текста (кириллица) на этой платформе не поддерживается; "
                                "ничего не введено."}
            else:
                gui.write(value, interval=0.01)
            return {"ok": True, "text": f"Введён текст ({len(value)} симв.)."}

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
