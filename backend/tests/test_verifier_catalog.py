"""Catalog-as-contract validation (Batch A).

The Verifier Coverage Catalog (verifier_catalog.yaml) is an engineering contract, NOT a
runtime classifier (yet). These tests keep it honest against the code: every `code:`
pointer resolves (file exists + cited symbols are present), supported rows carry a
resolvable pointer, and no row claims a bogus support status.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

CODE_DIR = BACKEND_ROOT / "app" / "application" / "code_agent"
CATALOG = CODE_DIR / "verifier_catalog.yaml"

try:
    import yaml  # noqa: E402
    _HAVE_YAML = True
except Exception:  # pragma: no cover
    _HAVE_YAML = False

_VALID_SUPPORT = {"supported", "partial", "missing"}
# code pointers that are explicitly not-yet-implemented
_GAP_MARKERS = ("GAP", "deferred")


def _py_files(pointer: str) -> list[str]:
    return re.findall(r"[A-Za-z_][\w./]*\.py", pointer or "")


def _cited_symbols(pointer: str) -> list[str]:
    """Symbols the pointer explicitly names: `file.py:sym1(...), sym2` and any `NAME(`."""
    syms: list[str] = []
    for after in re.findall(r"\.py:([^;]+)", pointer or ""):
        for tok in after.split(","):
            m = re.match(r"\s*([A-Za-z_]\w+)", tok)
            if m:
                syms.append(m.group(1))
    syms += re.findall(r"\b([A-Za-z_]\w+)\s*\(", pointer or "")
    return sorted(set(syms))


def _resolve_file(name: str) -> Path | None:
    base = name.split("/")[-1]
    for p in CODE_DIR.rglob(base):
        return p
    return None


@unittest.skipUnless(_HAVE_YAML, "PyYAML not installed")
class VerifierCatalogContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
        cls.categories = cls.cat.get("categories") or []

    def test_catalog_loads_and_has_categories(self):
        self.assertTrue(self.categories)
        for row in self.categories:
            self.assertIn("id", row)
            self.assertIn(row.get("support"), _VALID_SUPPORT, row.get("id"))

    def test_code_pointers_resolve(self):
        """Every non-GAP code pointer: cited .py files exist and cited symbols appear in them."""
        for row in self.categories:
            ptr = row.get("code") or ""
            if not ptr or any(g in ptr for g in _GAP_MARKERS):
                continue
            files = _py_files(ptr)
            self.assertTrue(files, f"{row['id']}: code pointer names no .py file")
            texts = []
            for f in files:
                path = _resolve_file(f)
                self.assertIsNotNone(path, f"{row['id']}: code file not found: {f}")
                texts.append(path.read_text(encoding="utf-8", errors="ignore"))
            blob = "\n".join(texts)
            for sym in _cited_symbols(ptr):
                self.assertIn(sym, blob, f"{row['id']}: cited symbol '{sym}' not found in {files}")

    def test_supported_rows_have_resolvable_pointer(self):
        for row in self.categories:
            if row.get("support") != "supported":
                continue
            ptr = row.get("code") or ""
            self.assertTrue(ptr and not any(g in ptr for g in _GAP_MARKERS),
                            f"{row['id']}: supported row must cite implemented code (no GAP)")
            self.assertTrue(_py_files(ptr), f"{row['id']}: supported row pointer names no file")

    def test_missing_rows_do_not_pretend_supported(self):
        for row in self.categories:
            if row.get("support") == "missing":
                ptr = row.get("code") or ""
                self.assertTrue(any(g in ptr for g in _GAP_MARKERS) or "closes: []" not in ptr,
                                f"{row['id']}: missing row should mark its code pointer GAP/deferred")
                self.assertEqual(row.get("closes", []) or [], [],
                                 f"{row['id']}: a missing row must close nothing")


if __name__ == "__main__":
    unittest.main()
