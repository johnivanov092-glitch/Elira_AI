from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.project_brain.service import ProjectBrainService  # noqa: E402


class FakePatchService:
    def __init__(self, *, preview_ok: bool = True, apply_ok: bool = True) -> None:
        self.preview_ok = preview_ok
        self.apply_ok = apply_ok
        self.preview_calls: list[tuple[str, str, int]] = []
        self.apply_calls: list[tuple[str, str]] = []

    def preview_patch(self, path: str, new_content: str, max_chars: int) -> dict:
        self.preview_calls.append((path, new_content, max_chars))
        return {"ok": self.preview_ok, "path": path}

    def apply_patch(self, path: str, new_content: str) -> dict:
        self.apply_calls.append((path, new_content))
        return {"ok": self.apply_ok, "path": path}


class ProjectBrainServiceFacadeTest(unittest.TestCase):
    def test_scan_project_delegates_to_storage_runtime(self) -> None:
        tree = {"ok": True, "items": []}
        with patch("app.application.project_brain.service.list_project_tree", return_value=tree) as mocked:
            result = ProjectBrainService().scan_project()

        mocked.assert_called_once_with(max_depth=4, max_items=500)
        self.assertEqual(result, {"ok": True, "type": "project_scan", "tree": tree})

    def test_find_code_delegates_to_storage_runtime(self) -> None:
        search_result = {"ok": True, "hits": []}
        with patch("app.application.project_brain.service.search_project", return_value=search_result) as mocked:
            result = ProjectBrainService().find_code("needle")

        mocked.assert_called_once_with(query="needle", max_hits=50)
        self.assertEqual(result["results"], search_result)

    def test_apply_patch_and_push_uses_injected_git_commit(self) -> None:
        patch_service = FakePatchService()
        git_calls: list[str] = []

        def fake_git_commit(message: str) -> dict:
            git_calls.append(message)
            return {"ok": True, "message": message}

        service = ProjectBrainService(
            patch_service_factory=lambda: patch_service,
            git_commit_func=fake_git_commit,
        )

        result = service.apply_patch_and_push(
            "backend/app/example.py",
            "print('ok')",
            message="test commit",
            auto_push=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(git_calls, ["test commit"])
        self.assertEqual(patch_service.preview_calls, [("backend/app/example.py", "print('ok')", 20000)])
        self.assertEqual(patch_service.apply_calls, [("backend/app/example.py", "print('ok')")])
        self.assertEqual(result["git"], {"ok": True, "message": "test commit"})

    def test_apply_patch_and_push_does_not_commit_on_apply_failure(self) -> None:
        patch_service = FakePatchService(apply_ok=False)
        git_calls: list[str] = []
        service = ProjectBrainService(
            patch_service_factory=lambda: patch_service,
            git_commit_func=lambda message: git_calls.append(message) or {"ok": True},
        )

        result = service.apply_patch_and_push("x.py", "bad", auto_push=True)

        self.assertFalse(result["ok"])
        self.assertEqual(git_calls, [])
        self.assertIsNone(result["git"])


if __name__ == "__main__":
    unittest.main()
