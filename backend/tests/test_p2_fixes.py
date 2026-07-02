from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class ErrorLeakTest(unittest.TestCase):
    """FIX-16: a route failure returns a generic 500 body, not the raw exception."""

    def test_files_route_hides_exception_detail(self):
        from app.api.routes import files as files_route
        app = FastAPI()
        app.include_router(files_route.router)
        client = TestClient(app)
        with patch.object(
            files_route.file_extract_runtime, "extract_file",
            side_effect=RuntimeError("SECRET_INTERNAL_PATH"),
        ):
            r = client.post(
                "/api/files/extract-text",
                files={"file": ("a.txt", b"hi", "text/plain")},
            )
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("SECRET_INTERNAL_PATH", r.text)  # no raw leak
        self.assertEqual(r.json().get("error"), "extraction failed")


class ClearAllConfirmTest(unittest.TestCase):
    """FIX-14: wiping ALL memory requires confirm=true."""

    def test_clear_all_without_confirm_is_rejected(self):
        from app.api.routes import advanced_routes
        app = FastAPI()
        app.include_router(advanced_routes.router)
        client = TestClient(app)
        # no category + no confirm → 400, and it must reject BEFORE touching the DB
        r = client.delete("/api/advanced/rag/clear")
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
