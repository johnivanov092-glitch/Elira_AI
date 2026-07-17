"""R4B — publish a processed workspace file to the user as a download artifact.

Behavior tests: a real workspace file publishes with exact bytes/size/sha and is
served HTTP 200 by the EXISTING /api/skills/download route; every source/name
escape vector (absolute/drive/UNC/../ADS/reserved/control/trailing) is rejected;
symlink escape, source dir/device/missing rejected; existing download never
overwritten; short writes complete; integrity mismatch publishes nothing and
leaves no temp; errors leak no absolute path; upload stays deferred; approval /
tool_search activation match the filesystem-edit contract; schema rejects extra
args.
"""
from __future__ import annotations

import glob
import hashlib
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools import _resources as res  # noqa: E402
from app.application.code_agent.tools._dispatch import build_tool_dispatch  # noqa: E402
from app.application.media import resource_store as rs  # noqa: E402
from app.core.config import DATA_DIR, GENERATED_DIR  # noqa: E402

_DATA = ("готовый результат mixed 123\x00\x01 " * 700).encode("utf-8")


def _publish(project_root, **kw):
    return build_tool_dispatch(Path(project_root))["resource_publish"](**kw)


class _Base(unittest.TestCase):
    def setUp(self):
        self.proj = Path(tempfile.mkdtemp())
        (self.proj / "out").mkdir()
        self.src = self.proj / "out" / "result.bin"
        self.src.write_bytes(_DATA)

    def _published(self):
        return glob.glob(str(GENERATED_DIR / "*"))


class HappyPathTest(_Base):
    def test_publish_exact_bytes_and_http_200(self):
        # give it a unique name so the shared GENERATED_DIR never collides
        name = f"r4b_{os.getpid()}_{len(self._published())}.bin"
        out = _publish(self.proj, project_path="out/result.bin", download_name=name)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["project_path"], "out/result.bin")
        self.assertEqual(out["download_url"], f"/api/skills/download/{name}")
        self.assertEqual(out["download_name"], name)
        self.assertEqual(out["size"], len(_DATA))
        self.assertEqual(out["sha256"], hashlib.sha256(_DATA).hexdigest())
        self.assertNotIn("touched_path", out)  # publishing does not modify the project
        self.assertIn("Published out/result.bin", out["text"])
        served = GENERATED_DIR / name
        self.assertEqual(served.read_bytes(), _DATA)
        # served HTTP 200 by the EXISTING download route
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.skills_routes import router as skills_router
        app = FastAPI(); app.include_router(skills_router)
        client = TestClient(app)
        r = client.get(out["download_url"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, _DATA)
        served.unlink(missing_ok=True)

    def test_default_download_name_is_safe_basename(self):
        name = f"dflt_{os.getpid()}"
        (self.proj / f"{name}.txt").write_bytes(b"x")
        out = _publish(self.proj, project_path=f"{name}.txt")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["download_name"], f"{name}.txt")
        (GENERATED_DIR / f"{name}.txt").unlink(missing_ok=True)

    def test_result_carries_no_absolute_or_storage_path(self):
        name = f"noabs_{os.getpid()}.bin"
        out = _publish(self.proj, project_path="out/result.bin", download_name=name)
        blob = str(out)
        self.assertNotIn(str(self.proj), blob)
        self.assertNotIn(str(GENERATED_DIR), blob)
        (GENERATED_DIR / name).unlink(missing_ok=True)

    def test_download_url_encodes_safe_filename_characters(self):
        name = f"отчёт #{os.getpid()} 50%.txt"
        out = _publish(self.proj, project_path="out/result.bin", download_name=name)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["download_name"], name)
        self.assertEqual(out["download_url"], f"/api/skills/download/{quote(name, safe='')}")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.skills_routes import router as skills_router
        app = FastAPI(); app.include_router(skills_router)
        response = TestClient(app).get(out["download_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, _DATA)
        (GENERATED_DIR / name).unlink(missing_ok=True)


class SourceContainmentTest(_Base):
    def test_source_escape_vectors_rejected(self):
        for bad in ("/etc/passwd", "C:/Windows/win.ini", "\\\\srv\\share\\x",
                    "../../evil", "out/../../escape", "a\x00b", "..", "."):
            out = _publish(self.proj, project_path=bad)
            self.assertFalse(out["ok"], f"{bad!r} should be rejected")
            self.assertEqual(out["error"], "invalid_source", bad)

    def test_symlink_escape_rejected(self):
        outside = Path(tempfile.mkdtemp())
        (outside / "secret.bin").write_bytes(b"SECRET")
        link = self.proj / "link"
        try:
            os.symlink(str(outside), str(link), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink creation not permitted")
        out = _publish(self.proj, project_path="link/secret.bin")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid_source")

    def test_directory_and_missing_rejected(self):
        self.assertEqual(_publish(self.proj, project_path="out")["error"], "source_not_file")
        self.assertEqual(_publish(self.proj, project_path="out/nope.bin")["error"], "source_not_file")

    def test_no_env_bypass(self):
        with mock.patch.dict(os.environ, {"ELIRA_FS_UNRESTRICTED": "1"}):
            out = _publish(self.proj, project_path="/etc/passwd")
        self.assertEqual(out["error"], "invalid_source")

    def test_source_is_rechecked_immediately_before_open(self):
        outside = Path(tempfile.mkdtemp()) / "secret.bin"
        outside.write_bytes(b"SECRET")
        name = f"source_swap_{os.getpid()}.bin"
        real_mkstemp = tempfile.mkstemp

        def swap_source(*args, **kwargs):
            fd, tmp_name = real_mkstemp(*args, **kwargs)
            self.src.unlink()
            os.symlink(str(outside), str(self.src))
            return fd, tmp_name

        try:
            with mock.patch.object(rs.tempfile, "mkstemp", side_effect=swap_source):
                with self.assertRaises(rs.ResourceError) as caught:
                    rs.publish_copy(
                        workspace_root=self.proj,
                        source=self.src,
                        destination_root=DATA_DIR,
                        dest_dir=GENERATED_DIR,
                        final_name=name,
                    )
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink creation not permitted")
        self.assertEqual(caught.exception.reason, "source_outside_workspace")
        self.assertFalse((GENERATED_DIR / name).exists())
        self.assertEqual(glob.glob(str(GENERATED_DIR / ".publish-*")), [])


class DownloadNameTest(_Base):
    def test_download_name_rejects_paths_and_dangerous_names(self):
        before = set(self._published())
        for bad in ("../evil", "a/b", "sub\\c", "C:x", "CON", "PRN", "COM1", "LPT2",
                    "COM¹", "file.txt:ads", "trailing ", "trailing.", "na\x00me", " lead"):
            out = _publish(self.proj, project_path="out/result.bin", download_name=bad)
            self.assertFalse(out["ok"], f"{bad!r} should be rejected")
            self.assertEqual(out["error"], "invalid_download_name", bad)
        self.assertEqual(set(self._published()), before)

    def test_download_name_accepts_plain_filename(self):
        self.assertIsNotNone(res._safe_download_name("report.pdf"))
        self.assertIsNotNone(res._safe_download_name("a b.txt"))     # inner space ok
        self.assertIsNone(res._safe_download_name(""))


class OverwriteIntegrityTest(_Base):
    def test_existing_download_not_overwritten(self):
        name = f"exist_{os.getpid()}.bin"
        (GENERATED_DIR / name).write_bytes(b"ORIGINAL")
        try:
            out = _publish(self.proj, project_path="out/result.bin", download_name=name)
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "destination_exists")
            self.assertEqual((GENERATED_DIR / name).read_bytes(), b"ORIGINAL")
        finally:
            (GENERATED_DIR / name).unlink(missing_ok=True)

    def test_dangling_destination_link_is_occupied(self):
        name = f"dangling_{os.getpid()}.bin"
        link = GENERATED_DIR / name
        target = GENERATED_DIR / f"missing_target_{os.getpid()}.bin"
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink creation not permitted")
        try:
            out = _publish(self.proj, project_path="out/result.bin", download_name=name)
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "destination_exists")
            self.assertFalse(target.exists())
        finally:
            link.unlink(missing_ok=True)

    def test_destination_directory_must_stay_inside_server_root(self):
        destination_root = Path(tempfile.mkdtemp())
        outside = Path(tempfile.mkdtemp())
        linked_dir = destination_root / "generated"
        try:
            os.symlink(str(outside), str(linked_dir), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink creation not permitted")
        with self.assertRaises(rs.ResourceError) as caught:
            rs.publish_copy(
                workspace_root=self.proj,
                source=self.src,
                destination_root=destination_root,
                dest_dir=linked_dir,
                final_name="escaped.bin",
            )
        self.assertEqual(caught.exception.reason, "invalid_destination")
        self.assertFalse((outside / "escaped.bin").exists())

    def test_short_writes_produce_complete_file(self):
        name = f"short_{os.getpid()}.bin"
        big = self.proj / "big.bin"
        big.write_bytes(b"z" * (256 * 1024))
        real_write = os.write

        def short(fd, data):
            return real_write(fd, bytes(data[:3]))
        try:
            with mock.patch("os.write", side_effect=short):
                out = _publish(self.proj, project_path="big.bin", download_name=name)
            self.assertTrue(out["ok"], out)
            self.assertEqual((GENERATED_DIR / name).read_bytes(), b"z" * (256 * 1024))
        finally:
            (GENERATED_DIR / name).unlink(missing_ok=True)

    def test_integrity_mismatch_publishes_nothing(self):
        # corrupt the readback so the staged verify fails
        name = f"corrupt_{os.getpid()}.bin"
        real_read = os.read
        seen = {"n": 0}

        def flip(fd, n):
            data = real_read(fd, n)
            if data and seen["n"] == 0:      # tamper with the first readback chunk only
                seen["n"] += 1
                return b"\x00" + data[1:]
            return data
        with mock.patch("os.read", side_effect=flip):
            with self.assertRaises(rs.ResourceError) as c:
                rs.publish_copy(workspace_root=self.proj, source=self.src,
                                destination_root=DATA_DIR, dest_dir=GENERATED_DIR,
                                final_name=name)
        self.assertEqual(c.exception.reason, "integrity_mismatch")
        self.assertFalse((GENERATED_DIR / name).exists())
        self.assertEqual(glob.glob(str(GENERATED_DIR / ".publish-*")), [])

    def test_oversized_source_bounded(self):
        name = f"cap_{os.getpid()}.bin"
        with self.assertRaises(rs.ResourceError) as c:
            rs.publish_copy(workspace_root=self.proj, source=self.src,
                            destination_root=DATA_DIR, dest_dir=GENERATED_DIR,
                            final_name=name, cap=16)
        self.assertEqual(c.exception.reason, "resource_too_large")
        self.assertFalse((GENERATED_DIR / name).exists())
        self.assertEqual(glob.glob(str(GENERATED_DIR / ".publish-*")), [])


class DeferredAndPolicyTest(_Base):
    def test_publish_does_not_process(self):
        name = f"defer_{os.getpid()}.bin"
        with mock.patch("app.application.file_extract.runtime.extract_file") as ex, \
                mock.patch("app.application.voice.runtime.transcribe") as stt:
            out = _publish(self.proj, project_path="out/result.bin", download_name=name)
        self.assertTrue(out["ok"])
        ex.assert_not_called()
        stt.assert_not_called()
        (GENERATED_DIR / name).unlink(missing_ok=True)

    def test_extra_args_and_schema_reject_injection(self):
        for extra in ({"url": "/x"}, {"path": "/etc"}, {"argv": "rm"},
                      {"device": "cuda"}, {"execution_target": "local_gpu"}):
            out = _publish(self.proj, project_path="out/result.bin", **extra)
            self.assertEqual(out["error"], "unsupported_arguments")
        from app.application.code_agent.tool_schemas import build_tool_schemas
        spec = next(s for s in build_tool_schemas()
                    if s["function"]["name"] == "resource_publish")
        params = spec["function"]["parameters"]
        self.assertFalse(params["additionalProperties"])
        self.assertEqual(set(params["properties"]), {"project_path", "download_name"})
        self.assertEqual(params["required"], ["project_path"])

    def test_approval_policy_matches_filesystem_edit(self):
        from app.application.code_agent.tool_policy import (
            BASE_TOOLS, EDIT_ONLY_TOOLS, SEARCH_ACTIVATABLE_SIDE_EFFECT,
        )
        from app.application.tool_registry.builtins import build_builtin_tools
        spec = next(s for s in build_builtin_tools() if s["name"] == "resource_publish")
        self.assertEqual(spec["source"], "code_agent")
        self.assertEqual(spec["permission"], "require_approval")
        self.assertTrue(spec["side_effect"])
        self.assertEqual(set(spec["scopes"]), {"fs.read", "fs.write"})
        self.assertIn("resource_publish", EDIT_ONLY_TOOLS)
        self.assertIn("resource_publish", SEARCH_ACTIVATABLE_SIDE_EFFECT)
        self.assertNotIn("resource_publish", BASE_TOOLS)

    def test_tool_search_activates_in_ask_and_accept_edits(self):
        from app.application.agent_kernel.deferred_tools import (
            clear_run, enable_deferred_tools, is_tool_active,
        )
        from app.application.code_agent.tools import tool_search
        from app.application.tool_registry.runtime import seed_builtin_tools
        seed_builtin_tools()
        for mode in ("ask", "accept_edits", "bypass"):
            rid = f"pub-search-{mode}"
            enable_deferred_tools(rid, ())
            try:
                # A narrow query of resource_publish-distinctive tokens ("опубликовать"
                # / "publish" match essentially only this tool) so the ≤5-per-call
                # activation cap never masks the eligibility this test targets: a
                # side_effect tool in SEARCH_ACTIVATABLE_SIDE_EFFECT is activatable in
                # ask/accept_edits (not just bypass).
                tool_search(run_id=rid, query="опубликовать publish", permission_mode=mode)
                self.assertTrue(is_tool_active(rid, "resource_publish"),
                                f"not activated in {mode}")
            finally:
                clear_run(rid)

    def test_publish_does_not_claim_document_format_validity(self):
        from app.application.code_agent.loop_helpers import _unbacked_docgen_claim

        # resource_publish can deliver arbitrary bytes.  A successful copy of
        # plain text named report.pdf must not satisfy file_gen's format claim.
        self.src.write_bytes(b"this is not a PDF")
        name = f"plain_{os.getpid()}.pdf"
        out = _publish(self.proj, project_path="out/result.bin", download_name=name)
        self.assertTrue(out["ok"], out)
        answer = "Готов файл report.pdf, его можно скачать."
        self.assertEqual(_unbacked_docgen_claim(answer, []), ["report.pdf"])
        (GENERATED_DIR / name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
