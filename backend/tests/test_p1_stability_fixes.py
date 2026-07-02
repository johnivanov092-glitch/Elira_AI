from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class UploadLimitTest(unittest.TestCase):
    """FIX-12: upload routes reject oversized bodies with HTTP 413."""

    def _client(self):
        from app.api.routes import files as files_route
        app = FastAPI()
        app.include_router(files_route.router)
        return files_route, TestClient(app)

    def test_oversized_upload_rejected_413(self):
        files_route, client = self._client()
        with patch.object(files_route, "MAX_UPLOAD_BYTES", 10):
            r = client.post(
                "/api/files/extract-text",
                files={"file": ("big.txt", b"x" * 64, "text/plain")},
            )
        self.assertEqual(r.status_code, 413)

    def test_small_upload_passes_size_gate(self):
        _files_route, client = self._client()
        r = client.post(
            "/api/files/extract-text",
            files={"file": ("a.txt", b"hello world", "text/plain")},
        )
        self.assertNotEqual(r.status_code, 413)  # under the limit → not rejected


if __name__ == "__main__":
    unittest.main()
