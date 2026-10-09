"""Mutable desktop control. Captures PNG; image interpretation uses read_image."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


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
    except (TypeError, ValueError, OverflowError):
        return None


def run_action(
    *,
    action: str = "screenshot",
    x: Any = None,
    y: Any = None,
    text: str = "",
    keys: Any = None,
    amount: int = 3,
    direction: str = "down",
    clicks: int = 1,
    output: str = "",
) -> dict[str, Any]:
    """Control the desktop: screenshot (PNG file) + mouse/keyboard.

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

    if act == "screenshot":
        if not output:
            return {"ok": False, "error": "output_required", "text": "Screenshot requires --output <file.png>."}
        target = Path(output).expanduser().resolve()
        if target.suffix.lower() != ".png":
            return {"ok": False, "error": "invalid_output", "text": "Screenshot output must be a PNG file."}
        png, size, err = _grab_png()
        if png is None:
            return {"ok": False, "error": "capture_failed", "text": f"Could not capture screen: {err}"}
        try:
            target.write_bytes(png)
        except OSError as exc:
            return {"ok": False, "error": "write_failed", "text": str(exc)}
        width, height = size or (0, 0)
        return {"ok": True, "path": str(target), "width": width, "height": height,
                "text": "Снимок сохранён. Прочитай его через read_image перед действием; снимок не подтверждает результат ввода."}

    # Every non-screenshot action needs real input control.
    gui, err = _load_pyautogui()
    if gui is None:
        return {
            "ok": False,
            "error": "input_unavailable",
            "text": (
                "ERROR: управление вводом недоступно — не удалось загрузить pyautogui "
                f"({err}). Установи зависимости из requirements.txt в .venv этого навыка."
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=sorted(_VALID_ACTIONS | {"status"}))
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--text", default="")
    parser.add_argument("--keys", nargs="+")
    parser.add_argument("--amount", type=int, default=3)
    parser.add_argument("--direction", choices=("up", "down"), default="down")
    parser.add_argument("--clicks", type=int, default=1)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if args.action == "status":
        missing = [name for name in ("pyautogui", "PIL") if importlib.util.find_spec(name) is None]
        result = {"ok": not missing, "missing_modules": missing, "python": sys.executable,
                  "desktop_verified": False, "text": "Проверены зависимости; экран и ввод не проверялись."}
    else:
        result = run_action(**vars(args))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
