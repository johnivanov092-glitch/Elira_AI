from __future__ import annotations

import unittest
from unittest.mock import patch

from app.application.library.runtime import _AUDIO_EXTS, extract_preview


class LibraryAudioPreviewTest(unittest.TestCase):
    """Drag-dropped audio must be transcribed into the Library (same STT path as
    the composer attachment), not stored as a text-less blob."""

    def test_ogg_recognised_as_audio(self) -> None:
        self.assertIn(".ogg", _AUDIO_EXTS)

    def test_extract_preview_transcribes_audio(self) -> None:
        # extract_preview lazy-imports extract_file, so patch it at the source.
        with patch(
            "app.application.file_extract.runtime.extract_file",
            return_value={"ok": True, "text": "привет мир", "chars": 10},
        ) as m:
            out = extract_preview("voice.ogg", b"fake-audio-bytes")
        m.assert_called_once()
        self.assertEqual(out, "привет мир")

    def test_extract_preview_audio_failure_is_empty_no_raise(self) -> None:
        with patch(
            "app.application.file_extract.runtime.extract_file",
            side_effect=RuntimeError("stt unreachable"),
        ):
            out = extract_preview("note.opus", b"x")
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
