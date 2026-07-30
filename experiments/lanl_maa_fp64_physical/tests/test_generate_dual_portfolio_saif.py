import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "generate_dual_portfolio_saif.py"
CONTRACT = ROOT / "activity/portfolio_activity_contract.json"
SPEC = importlib.util.spec_from_file_location("dual_portfolio_saif", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DualPortfolioSaifTest(unittest.TestCase):
    def test_all_profiles_generate_two_slot_saif(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for profile in contract["profiles"]:
            with self.subTest(profile=profile):
                result = MODULE.generate(contract, profile)
                self.assertIn("(DESIGN \"LanlFp64Portfolio2S1A1M8D\")", result)
                self.assertIn("req0A[63]", result)
                self.assertIn("req1B[63]", result)
                self.assertEqual(result.count("(DURATION "), 1)
                valid_ticks = sum(
                    int(re.search(
                        rf"\({signal} \(T0 [0-9]+\) \(T1 ([0-9]+)\)",
                        result,
                    ).group(1))
                    for signal in ("req0Valid", "req1Valid")
                )
                self.assertEqual(
                    valid_ticks // contract["clock_period_ns"],
                    sum(contract["profiles"][profile]
                        ["operation_counts"].values()),
                )

    def test_pairing_never_selects_two_adds_or_two_multiplies(self):
        initial = {"add_subtract": 3, "multiply": 2, "divide": 0}
        remaining = dict(initial)
        first = MODULE.choose_operation(remaining, initial)
        self.assertIsNotNone(first)
        remaining[first] -= 1
        second = MODULE.choose_operation(
            remaining,
            initial,
            excluded_resource=MODULE.resource_class(first),
        )
        self.assertIsNotNone(second)
        self.assertNotEqual(
            MODULE.resource_class(first), MODULE.resource_class(second)
        )

    def test_custom_top_retargets_design_and_instance(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        top = "LanlFp64Portfolio2SSharedRecode1A1M8D"
        result = MODULE.generate(contract, "sparta_64_particle", top=top)
        self.assertIn(f'(DESIGN "{top}")', result)
        self.assertIn(f"(INSTANCE {top}", result)
        self.assertNotIn(f"(INSTANCE {MODULE.DEFAULT_TOP}", result)

    def test_rejects_invalid_top_name(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "design name"):
            MODULE.generate(contract, "sparta_64_particle", top="bad top")


if __name__ == "__main__":
    unittest.main()
