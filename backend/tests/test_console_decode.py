"""run_bash output decoding — regression for the Windows mojibake bug.

Live: «Проанализируй домашнюю сеть» produced unreadable output
(`ħŸ¬Ѓ Ï €Ѓв ¬Ё 6 192.0.0.1…`) because Windows console apps (ping/ipconfig/arp)
emit the OEM codepage (cp866 on RU Windows) and text=True decoded them with the
ANSI default. The model couldn't read the output → flailed on the network task.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._run import _decode_console  # noqa: E402


class DecodeConsoleTest(unittest.TestCase):
    def test_utf8(self):
        self.assertEqual(_decode_console("Обмен пакетами".encode("utf-8")), "Обмен пакетами")

    def test_ascii(self):
        self.assertEqual(_decode_console(b"Reply from 192.168.88.1: bytes=32"),
                         "Reply from 192.168.88.1: bytes=32")

    def test_empty(self):
        self.assertEqual(_decode_console(b""), "")

    @unittest.skipUnless(os.name == "nt", "OEM/cp866 is Windows-only")
    def test_windows_console_cp866_not_mojibake(self):
        ru = "Обмен пакетами с 192.168.88.1 по 32 байт данных:"
        decoded = _decode_console(ru.encode("cp866"))
        self.assertEqual(decoded, ru)
        self.assertNotIn("Ђ", decoded)  # no stray Ђ-style mojibake char

    def test_never_raises_on_arbitrary_bytes(self):
        self.assertIsInstance(_decode_console(bytes(range(256))), str)

    def test_str_and_none_pass_through(self):
        # Tolerant of an already-decoded str (or a mocked str stdout) and None.
        self.assertEqual(_decode_console("уже строка"), "уже строка")
        self.assertEqual(_decode_console(None), "")

    def test_server_log_tail_decodes_oem(self):
        # run_server writes raw child bytes to the log; _read_log_tail must decode
        # them like console output, not assume UTF-8 (was mojibake on RU Windows).
        import tempfile
        from pathlib import Path
        from app.application.code_agent.tools._run import _read_log_tail
        ru = "Сервер запущен на порту 5173"
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "server.log"
            log.write_bytes(ru.encode("cp866") if os.name == "nt" else ru.encode("utf-8"))
            self.assertIn("5173", _read_log_tail(log))
            self.assertIn("Сервер", _read_log_tail(log))


if __name__ == "__main__":
    unittest.main()
