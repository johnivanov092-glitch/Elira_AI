"""Canonical subprocess-output decoder (kills the Windows console mojibake).

Windows console apps (ping / ipconfig / arp / a remote ssh command / a sandbox
Python print) emit the OEM codepage (cp866 on RU Windows). Capturing them with
subprocess `text=True` decodes via the ANSI/locale default (cp1251) → mojibake the
model cannot read. Capture BYTES and decode here instead: UTF-8 strict first (UTF-8
tools + pure ASCII), then the OEM codepage, then cp866/cp1251, then a lossless
latin-1 so it never raises. One implementation, imported by every subprocess site.
"""
from __future__ import annotations

import os


def decode_console(data: bytes | str | None) -> str:
    if not data:
        return ""
    if isinstance(data, str):  # already decoded (or a mock) — pass through
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    encodings = ("oem", "cp866", "cp1251") if os.name == "nt" else ("cp1251",)
    for enc in encodings:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace")
