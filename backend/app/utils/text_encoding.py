from __future__ import annotations

from copy import deepcopy
from typing import Any


MOJIBAKE_MARKERS = (
    "\u0420\u0401",
    "\u0420\u040e",
    "\u0420\u0406",
    "\u0420\u0408",
    "\u0420\u040b",
    "\u0420\u0452",
    "\u0420\u0453",
    "\u0420\u0454",
    "\u0420\u0455",
    "\u0420\u0458",
    "\u0420\u045c",
    "\u0420\u045f",
    "\u0420\u00a0",
    "\u0420\u0491",
    "\u0421\u0402",
    "\u0421\u0403",
    "\u0421\u040a",
    "\u0421\u040b",
    "\u0421\u040f",
    "\u0421\u0453",
    "\u0421\u201a",
    "\u0421\u2030",
    "\u0421\u2039",
    "\u0421\u20ac",
    "\u00d0",
    "\u00d1",
    "\u00e2\u20ac",
    "\u0432\u0402",
    "\ufffd",
)


def looks_like_mojibake(value: str) -> bool:
    return any(marker in value for marker in MOJIBAKE_MARKERS)


def mojibake_score(value: str) -> int:
    return sum(value.count(marker) for marker in MOJIBAKE_MARKERS)


def _encode_with_byte_fallback(value: str, encoding: str) -> bytes:
    raw = bytearray()
    for char in value:
        try:
            raw.extend(char.encode(encoding))
            continue
        except UnicodeEncodeError:
            codepoint = ord(char)
            if codepoint <= 0xFF:
                raw.append(codepoint)
                continue
            raise
    return bytes(raw)


def _decode_mojibake_candidate(value: str, encoding: str) -> str | None:
    try:
        return _encode_with_byte_fallback(value, encoding).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None


def repair_mojibake_text(value: str, *, max_passes: int = 2) -> str:
    current = value
    for _ in range(max_passes):
        if not looks_like_mojibake(current):
            break
        current_score = mojibake_score(current)
        candidates = [
            candidate
            for encoding in ("cp1251", "cp1252", "latin1")
            if (candidate := _decode_mojibake_candidate(current, encoding))
        ]
        better = [
            candidate
            for candidate in candidates
            if mojibake_score(candidate) < current_score
        ]
        if not better:
            break
        current = min(better, key=mojibake_score)
    return current


def repair_mojibake_payload(value: Any) -> Any:
    if isinstance(value, str):
        return repair_mojibake_text(value)
    if isinstance(value, list):
        return [repair_mojibake_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(repair_mojibake_payload(item) for item in value)
    if isinstance(value, dict):
        return {
            repair_mojibake_payload(key): repair_mojibake_payload(item)
            for key, item in value.items()
        }
    return deepcopy(value)
