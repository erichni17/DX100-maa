import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/generate_portfolio_saif.py"
CONTRACT = ROOT / "activity/portfolio_activity_contract.json"
SPEC = importlib.util.spec_from_file_location("portfolio_saif", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PortfolioSaifTest(unittest.TestCase):
    def test_all_profiles_generate_balanced_saif(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for profile in contract["profiles"]:
            with self.subTest(profile=profile):
                result = MODULE.generate(contract, profile)
                self.assertTrue(result.startswith("(SAIFILE\n"))
                self.assertIn("(INSTANCE LanlFp64Portfolio1A1M8D", result)
                self.assertIn("reqA[63]", result)
                self.assertEqual(result.count("(DURATION "), 1)
                self.assertTrue(result.endswith(")\n"))

    def test_empty_profile_is_rejected(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        contract["profiles"]["empty"] = {
            "operation_counts": {
                "add_subtract": 0,
                "multiply": 0,
                "divide": 0,
            }
        }
        with self.assertRaisesRegex(ValueError, "empty activity profile"):
            MODULE.generate(contract, "empty")


if __name__ == "__main__":
    unittest.main()
