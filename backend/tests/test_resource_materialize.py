"""R4A — ResourceRef → project workspace bridge (resource_materialize).

Behavior tests: a run-bound resource materializes into the workspace with exact
bytes/size/sha; unbound/cross-session refused before any read; absolute / drive /
UNC / '..' / symlink-escape destinations rejected; existing destination never
overwritten; streaming survives short writes; a failed copy cleans its temp and
leaks no absolute path; upload stays deferred; the approval/policy metadata
matches the existing filesystem-edit contract.
"""
from __future__ import annotations

import dataclasses
import glob
import hashlib
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools import reset_current_run_id, set_current_run_id  # noqa: E402
from app.application.code_agent.tools import _resources as res  # noqa: E402
from app.application.code_agent.tools._dispatch import build_tool_dispatch  # noqa: E402
from app.application.media import resource_store as rs, run_binding  # noqa: E402

_DATA = ("двоичные данные mixed 123\x00\x01 " * 500).encode("utf-8")


def _materialize(project_root, **kw):
    return build_tool_dispatch(Path(project_root))["resource_materialize"](**kw)


class _Base(unittest.TestCase):
    def setUp(self):
        self.proj = Path(tempfile.mkdtemp())
        self.rid_run = "run-mat"
        run_binding.clear_run(self.rid_run)
        self._tok = set_current_run_id(self.rid_run)

    def tearDown(self):
        reset_current_run_id(self._tok)
        run_binding.clear_run(self.rid_run)

    def _bound(self, name="clip.bin", data=_DATA, owner="s1"):
        rec = rs.register_resource(original_name=name, content_type="application/octet-stream",
                                   owner_session=owner, data=data)
        run_binding.bind_resources(self.rid_run, [rec.resource_id])
        return rec


class HappyPathTest(_Base):
    def test_bound_resource_appears_with_exact_bytes(self):
        rec = self._bound("photo.jpg")
        out = _materialize(self.proj, resource_id=rec.resource_id)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["project_path"], "photo.jpg")
        self.assertEqual(out["size"], len(_DATA))
        self.assertEqual(out["sha256"], rec.sha256)
        landed = self.proj / out["project_path"]
        self.assertEqual(landed.read_bytes(), _DATA)
        self.assertEqual(hashlib.sha256(landed.read_bytes()).hexdigest(), rec.sha256)

    def test_custom_relative_destination_with_subdir(self):
        rec = self._bound()
        out = _materialize(self.proj, resource_id=rec.resource_id, destination_name="in/nested/a.bin")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["project_path"], "in/nested/a.bin")
        self.assertTrue((self.proj / "in" / "nested" / "a.bin").is_file())

    def test_default_name_is_sanitized_basename(self):
        rec = self._bound("../../etc/evil name.sh")
        out = _materialize(self.proj, resource_id=rec.resource_id)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["project_path"], "evil name.sh")     # no path parts

    def test_default_windows_device_name_falls_back_safely(self):
        rec = self._bound("CON.txt")
        out = _materialize(self.proj, resource_id=rec.resource_id)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["project_path"], "file")

    def test_result_and_touched_path_carry_no_absolute_path(self):
        rec = self._bound()
        out = _materialize(self.proj, resource_id=rec.resource_id)
        self.assertNotIn(str(self.proj), str(out))
        self.assertNotIn(rec.storage_path, str(out))
        self.assertEqual(out["touched_path"], out["project_path"])   # into touched-files flow
        self.assertEqual(out["diff_action"], "create")


class RunBindingTest(_Base):
    def test_unbound_refused_before_read(self):
        rec = rs.register_resource(original_name="x.bin", content_type="application/octet-stream",
                                   owner_session="s1", data=_DATA)   # NOT bound
        out = _materialize(self.proj, resource_id=rec.resource_id)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "resource_not_bound")
        self.assertEqual(list(self.proj.iterdir()), [])              # nothing written

    def test_other_run_cannot_materialize(self):
        rec = self._bound()
        other = set_current_run_id("run-other")
        try:
            out = _materialize(self.proj, resource_id=rec.resource_id)
        finally:
            reset_current_run_id(other)
        self.assertEqual(out["error"], "resource_not_bound")

    def test_no_run_context_refused(self):
        rec = self._bound()
        reset_current_run_id(self._tok)
        try:
            out = _materialize(self.proj, resource_id=rec.resource_id)
            self.assertEqual(out["error"], "no_run_context")
        finally:
            self._tok = set_current_run_id(self.rid_run)

    def test_path_as_id_and_unknown_id_refused(self):
        for bad in ("C:/Windows/win.ini", "../../etc/passwd", "not-a-real-id"):
            run_binding.bind_resources(self.rid_run, [bad])          # even if "bound", get_record rejects
            out = _materialize(self.proj, resource_id=bad)
            self.assertFalse(out["ok"])
            self.assertIn(out["error"], ("resource_not_bound", "resource_not_found"))

    def test_extra_model_args_refused(self):
        rec = self._bound()
        for extra in ({"path": "/etc/x"}, {"device": "cuda"}, {"argv": "rm -rf"}):
            out = _materialize(self.proj, resource_id=rec.resource_id, **extra)
            self.assertEqual(out["error"], "unsupported_arguments")
        self.assertEqual(list(self.proj.iterdir()), [])


class ContainmentTest(_Base):
    def test_escape_vectors_rejected(self):
        rec = self._bound()
        # NB: "" is NOT here — an empty destination_name is the optional-default
        # trigger (safe basename), tested separately; these are explicit escapes.
        for bad in ("/etc/passwd", "C:/Windows/win.ini", "\\\\server\\share\\x",
                    "../../evil", "a/../../../evil", "..\\..\\x", ".",
                    "foo\x00bar", "E:/x", "sub/../../escape", "..",
                    "file.txt:secret", "CON", "nul.txt", "AUX.pdf",
                    "COM1.log", "LPT9", "COM¹.txt", "LPT²", "sub/NUL.txt", "bad?.txt",
                    "bad|name", "trailing.", "trailing ", "control\x01.txt"):
            out = _materialize(self.proj, resource_id=rec.resource_id, destination_name=bad)
            self.assertFalse(out["ok"], f"{bad!r} should be rejected")
            self.assertEqual(out["error"], "invalid_destination", bad)

    def test_symlink_escape_rejected(self):
        rec = self._bound()
        outside = Path(tempfile.mkdtemp())
        link = self.proj / "escape"
        try:
            os.symlink(str(outside), str(link), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink creation not permitted on this host")
        out = _materialize(self.proj, resource_id=rec.resource_id, destination_name="escape/pwned.bin")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid_destination")
        self.assertFalse((outside / "pwned.bin").exists())           # nothing escaped

    def test_no_env_bypass(self):
        # resource_materialize must NEVER honor ELIRA_FS_UNRESTRICTED (unlike the
        # general file tools) — a materialized file always stays in the workspace.
        rec = self._bound()
        with mock.patch.dict(os.environ, {"ELIRA_FS_UNRESTRICTED": "1"}):
            out = _materialize(self.proj, resource_id=rec.resource_id, destination_name="/tmp/escape.bin")
        self.assertEqual(out["error"], "invalid_destination")


class OverwriteAndIntegrityTest(_Base):
    def test_existing_destination_not_replaced(self):
        rec = self._bound()
        first = _materialize(self.proj, resource_id=rec.resource_id, destination_name="a.bin")
        self.assertTrue(first["ok"])
        (self.proj / "a.bin").write_bytes(b"ORIGINAL")               # pre-existing content
        second = _materialize(self.proj, resource_id=rec.resource_id, destination_name="a.bin")
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "destination_exists")
        self.assertEqual((self.proj / "a.bin").read_bytes(), b"ORIGINAL")   # untouched

    def test_short_writes_produce_complete_file(self):
        rec = self._bound(data=b"y" * (300 * 1024))
        real_write = os.write

        def short(fd, data):
            return real_write(fd, bytes(data[:3]))               # write 3 bytes at a time
        with mock.patch("os.write", side_effect=short):
            out = _materialize(self.proj, resource_id=rec.resource_id, destination_name="s.bin")
        self.assertTrue(out["ok"], out)
        self.assertEqual((self.proj / "s.bin").read_bytes(), b"y" * (300 * 1024))

    def test_integrity_mismatch_cleans_temp_and_writes_nothing(self):
        rec = self._bound()
        bad_record = dataclasses.replace(rec, sha256="0" * 64)      # force verify mismatch
        with self.assertRaises(rs.ResourceError) as c:
            rs.materialize(bad_record, self.proj, "x.bin", workspace_root=self.proj)
        self.assertEqual(c.exception.reason, "integrity_mismatch")
        self.assertFalse((self.proj / "x.bin").exists())
        self.assertEqual(glob.glob(str(self.proj / ".materialize-*")), [])

    def test_staged_file_is_verified_before_commit(self):
        rec = self._bound()
        real_fsync = os.fsync

        def tamper(fd):
            real_fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, b"BAD")
            os.ftruncate(fd, 3)

        with mock.patch.object(os, "fsync", side_effect=tamper):
            out = _materialize(self.proj, resource_id=rec.resource_id, destination_name="bad.bin")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "integrity_mismatch")
        self.assertFalse((self.proj / "bad.bin").exists())
        self.assertEqual(glob.glob(str(self.proj / ".materialize-*")), [])

    def test_missing_blob_cleans_up(self):
        rec = self._bound()
        gone = dataclasses.replace(rec, storage_path=str(self.proj / "nope"))
        with self.assertRaises(rs.ResourceError) as c:
            rs.materialize(gone, self.proj, "x.bin", workspace_root=self.proj)
        self.assertEqual(c.exception.reason, "resource_blob_missing")
        self.assertEqual(glob.glob(str(self.proj / ".materialize-*")), [])

    def test_oversized_source_is_bounded(self):
        rec = self._bound()
        smaller = dataclasses.replace(rec, size=4)                  # claim tiny size vs real blob
        with self.assertRaises(rs.ResourceError) as c:
            rs.materialize(smaller, self.proj, "x.bin", workspace_root=self.proj)
        self.assertIn(c.exception.reason, ("resource_too_large", "integrity_mismatch"))
        self.assertFalse((self.proj / "x.bin").exists())

    def test_store_rechecks_workspace_containment(self):
        rec = self._bound()
        outside = Path(tempfile.mkdtemp())
        with self.assertRaises(rs.ResourceError) as c:
            rs.materialize(rec, outside, "x.bin", workspace_root=self.proj)
        self.assertEqual(c.exception.reason, "invalid_destination")
        self.assertFalse((outside / "x.bin").exists())

    def test_post_link_temp_cleanup_retries_without_hiding_success(self):
        rec = self._bound()
        real_unlink = Path.unlink
        attempts = 0

        def transient_failure(path, *args, **kwargs):
            nonlocal attempts
            if path.name.startswith(".materialize-"):
                attempts += 1
                if attempts == 1:
                    raise OSError("transient cleanup failure")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(rs, "_USE_WINDOWS_RENAME", False), \
                mock.patch.object(Path, "unlink", autospec=True, side_effect=transient_failure):
            rs.materialize(rec, self.proj, "linked.bin", workspace_root=self.proj)
        self.assertEqual((self.proj / "linked.bin").read_bytes(), _DATA)
        self.assertGreaterEqual(attempts, 2)
        self.assertEqual(glob.glob(str(self.proj / ".materialize-*")), [])


class DeferredAndPolicyTest(_Base):
    def test_upload_stays_deferred_no_processing(self):
        # registering + materializing must not invoke any extract/STT/OCR runtime
        with mock.patch("app.application.file_extract.runtime.extract_file") as ex, \
                mock.patch("app.application.voice.runtime.transcribe") as stt:
            rec = self._bound("doc.pdf")
            out = _materialize(self.proj, resource_id=rec.resource_id)
        self.assertTrue(out["ok"])
        ex.assert_not_called()
        stt.assert_not_called()

    def test_approval_policy_matches_filesystem_edit_contract(self):
        from app.application.code_agent.tool_policy import BASE_TOOLS, EDIT_ONLY_TOOLS
        from app.application.tool_registry.builtins import build_builtin_tools
        spec = next(s for s in build_builtin_tools() if s["name"] == "resource_materialize")
        self.assertEqual(spec["permission"], "require_approval")
        self.assertTrue(spec["side_effect"])
        self.assertEqual(set(spec["scopes"]), {"fs.read", "fs.write"})
        self.assertIn("resource_materialize", EDIT_ONLY_TOOLS)      # accept_edits auto-approves
        self.assertNotIn("resource_materialize", BASE_TOOLS)        # activatable via tool_search

    def test_tool_search_activates_in_ask_and_accept_edits(self):
        from app.application.code_agent.tools import tool_search
        from app.application.agent_kernel import deferred_tools
        from app.application.tool_registry.runtime import seed_builtin_tools

        seed_builtin_tools()
        for mode in ("ask", "accept_edits"):
            run_id = f"materialize-{mode}"
            deferred_tools.clear_run(run_id)
            self.addCleanup(deferred_tools.clear_run, run_id)
            deferred_tools.enable_deferred_tools(run_id, [])
            result = tool_search(
                run_id=run_id,
                query="resource_materialize",
                permission_mode=mode,
            )
            self.assertIn("resource_materialize", result["activated"], mode)
            self.assertTrue(deferred_tools.is_tool_active(run_id, "resource_materialize"))

    def test_schema_args_are_only_id_and_destination(self):
        from app.application.code_agent.tool_schemas import build_tool_schemas
        spec = next(s for s in build_tool_schemas()
                    if s["function"]["name"] == "resource_materialize")
        params = spec["function"]["parameters"]
        self.assertFalse(params["additionalProperties"])
        self.assertEqual(set(params["properties"]), {"resource_id", "destination_name"})
        self.assertEqual(params["required"], ["resource_id"])

    def test_tool_search_finds_it_ru_and_en(self):
        from app.application.tool_registry.runtime import search_tool_specs, seed_builtin_tools
        seed_builtin_tools()
        for query in ("положи файл в проект", "materialize resource into project",
                      "сохрани вложение в проект", "workspace"):
            names = [m.get("name") for m in search_tool_specs(query, limit=25)]
            self.assertIn("resource_materialize", names, f"query miss: {query}")


if __name__ == "__main__":
    unittest.main()
