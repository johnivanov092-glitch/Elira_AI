"""Provisioner guard-rails (scripts/provision_change_executor.py): the trusted release-artifact
verification, the outside-repo / drive-root refusals, base-python validation, and the venv-wipe
guard. These are the checks that keep deploy from copying privileged code out of a
model-writable repo or wiping an unrelated directory."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import provision_change_executor as prov  # noqa: E402


def _make_artifact(dir_path: Path, files: dict) -> str:
    """Write .py files + a correct MANIFEST.sha256; return the manifest digest."""
    dir_path.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, body in files.items():
        (dir_path / name).write_text(body, encoding="utf-8")
        hashes[name] = prov._sha256_file(dir_path / name)
    text = prov._manifest_text(hashes)
    (dir_path / prov._MANIFEST).write_text(text, encoding="utf-8")
    return prov._manifest_digest(text)


class ArtifactVerifyTest(unittest.TestCase):
    def setUp(self):
        # a tmp dir OUTSIDE the repo (system temp is not under the repo tree)
        self._tmp = Path(tempfile.mkdtemp())
        self.art = self._tmp / "artifact"
        self.digest = _make_artifact(self.art, {"engine.py": "x=1\n", "store.py": "y=2\n"})

    def test_good_artifact_accepted(self):
        hashes = prov._verify_artifact(self.art, self.digest)
        self.assertEqual(set(hashes), {"engine.py", "store.py"})

    def test_wrong_pin_rejected(self):
        with self.assertRaises(SystemExit):
            prov._verify_artifact(self.art, "deadbeef")

    def test_tampered_file_rejected_same_pin(self):
        # append to a file WITHOUT updating the manifest -> file-hash mismatch (the pin still
        # matches the old manifest text, so this is caught by the per-file hash check)
        with open(self.art / "engine.py", "a", encoding="utf-8") as fh:
            fh.write("# evil\n")
        with self.assertRaises(SystemExit):
            prov._verify_artifact(self.art, self.digest)

    def test_tampered_file_and_manifest_rejected_by_pin(self):
        # update BOTH the file and the manifest -> the manifest digest changes -> pin mismatch
        new_digest = _make_artifact(self.art, {"engine.py": "evil()\n", "store.py": "y=2\n"})
        self.assertNotEqual(new_digest, self.digest)
        with self.assertRaises(SystemExit):
            prov._verify_artifact(self.art, self.digest)          # old pin no longer matches

    def test_extra_file_rejected(self):
        (self.art / "smuggled.py").write_text("evil()\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            prov._verify_artifact(self.art, self.digest)          # .py set != manifest

    def test_missing_manifest_rejected(self):
        (self.art / prov._MANIFEST).unlink()
        with self.assertRaises(SystemExit):
            prov._verify_artifact(self.art, self.digest)

    def test_artifact_inside_repo_rejected(self):
        with self.assertRaises(SystemExit):
            prov._verify_artifact(ROOT / "some_artifact", self.digest)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)


class CopyArtifactTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self.art = self._tmp / "artifact"
        _make_artifact(self.art, {"engine.py": "x=1\n"})
        self.hashes = {"engine.py": prov._sha256_file(self.art / "engine.py")}
        self.root = self._tmp / "root"; self.root.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_fresh_copy_and_reverify(self):
        prov._copy_from_artifact(self.root, self.art, self.hashes, force=False)
        self.assertEqual((self.root / "change_executor" / "engine.py").read_text(encoding="utf-8"), "x=1\n")

    def test_preplanted_code_rejected_without_force(self):
        # P1 (review): a pre-planted change_executor/ must be a HARD ERROR, not a silent skip.
        dst = self.root / "change_executor"; dst.mkdir()
        (dst / "engine.py").write_text("EVIL=1\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            prov._copy_from_artifact(self.root, self.art, self.hashes, force=False)
        self.assertEqual((dst / "engine.py").read_text(encoding="utf-8"), "EVIL=1\n")   # NOT laundered in

    def test_force_replaces_preplanted_and_reverifies(self):
        dst = self.root / "change_executor"; dst.mkdir()
        (dst / "evil.py").write_text("EVIL=1\n", encoding="utf-8")
        prov._copy_from_artifact(self.root, self.art, self.hashes, force=True)
        self.assertFalse((dst / "evil.py").exists())                                    # attacker file gone
        self.assertEqual((dst / "engine.py").read_text(encoding="utf-8"), "x=1\n")      # verified code deployed


class GuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_refuse_drive_root(self):
        anchor = Path(self._tmp).resolve().anchor      # 'C:\\' or '/'
        with self.assertRaises(SystemExit):
            prov._refuse_if_in_repo(Path(anchor))

    def test_refuse_in_repo(self):
        with self.assertRaises(SystemExit):
            prov._refuse_if_in_repo(ROOT / "sub" / "dir")

    def test_outside_repo_ok(self):
        prov._refuse_if_in_repo(self._tmp)             # no raise

    def test_base_python_in_repo_rejected(self):
        with self.assertRaises(SystemExit):
            prov._validate_base_python(str(ROOT / "backend" / ".venv" / "Scripts" / "python.exe"))

    def test_base_python_missing_rejected(self):
        with self.assertRaises(SystemExit):
            prov._validate_base_python(str(self._tmp / "nope.exe"))

    def test_make_venv_refuses_to_wipe_non_venv_dir(self):
        root = self._tmp / "root"
        (root / "venv").mkdir(parents=True)
        (root / "venv" / "keep.txt").write_text("important\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            prov._make_venv(root, sys.executable, force=False)     # no --force -> must NOT wipe
        self.assertTrue((root / "venv" / "keep.txt").exists())     # preserved


if __name__ == "__main__":
    unittest.main()
