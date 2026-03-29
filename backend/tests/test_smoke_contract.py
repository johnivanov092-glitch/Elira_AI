from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.smoke_contract_check import collect_failures  # noqa: E402


class SmokeContractTest(unittest.TestCase):
    def test_contract_guards(self) -> None:
        results = collect_failures()

        self.assertFalse(results["missing_paths"], f"Missing required backend routes: {results['missing_paths']}")
        self.assertFalse(results["raw_fetch_hits"], f"Raw fetch() calls found: {results['raw_fetch_hits']}")
        self.assertFalse(results["component_literal_hits"], f"Endpoint literals found in components: {results['component_literal_hits']}")
        self.assertFalse(results["dead_endpoint_hits"], f"Dead frontend endpoints still referenced: {results['dead_endpoint_hits']}")
        self.assertFalse(results["capability_failures"], f"Capability shape issues: {results['capability_failures']}")
        self.assertFalse(results["persona_failures"], f"Persona status issues: {results['persona_failures']}")


if __name__ == "__main__":
    unittest.main()
