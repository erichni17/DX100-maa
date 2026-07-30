import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/collect_power_results.py"
SPEC = importlib.util.spec_from_file_location("collect_power", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CollectPowerResultsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        total = {
            "Internal": {},
            "Total": {
                "internal": 0.1,
                "switching": 0.2,
                "leakage": 0.01,
                "total": 0.31,
            },
        }
        lines = ["Build completed successfully"]
        for profile in MODULE.PROFILES:
            (self.root / f"fp64_portfolio_{profile}.saif").write_text(
                "(SAIFILE)\n", encoding="utf-8"
            )
            for kind in ("vectorless", "vector-driven"):
                (
                    self.root / f"fp64_portfolio_{profile}_{kind}_power.json"
                ).write_text(json.dumps(total), encoding="utf-8")
            lines.extend(
                (
                    f"read_saif -scope TOP/DUT fp64_portfolio_{profile}.saif",
                    "prefix: Annotated 139 pin activities.",
                )
            )
        self.log = self.root / "service.log"
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_collects_all_profiles(self):
        result = MODULE.collect(self.root, self.log)
        self.assertEqual(set(result["profiles"]), set(MODULE.PROFILES))
        self.assertFalse(result["power_claim_eligible"])

    def test_rejects_partial_annotation(self):
        self.log.write_text(
            self.log.read_text(encoding="utf-8").replace(
                "Annotated 139", "Annotated 138", 1
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "incomplete"):
            MODULE.collect(self.root, self.log)

    def test_collects_dual_slot_prefix_and_pin_count(self):
        lines = ["Build completed successfully"]
        total = {
            "Total": {
                "internal": 0.1,
                "switching": 0.2,
                "leakage": 0.01,
                "total": 0.31,
            }
        }
        for profile in MODULE.PROFILES:
            (self.root / f"fp64_dual_portfolio_{profile}.saif").write_text(
                "(SAIFILE)\n", encoding="utf-8")
            for kind in ("vectorless", "vector-driven"):
                path = self.root / (
                    f"fp64_dual_portfolio_{profile}_{kind}_power.json")
                path.write_text(json.dumps(total), encoding="utf-8")
            lines.extend((
                "read_saif -scope TOP/DUT "
                f"fp64_dual_portfolio_{profile}.saif",
                "prefix: Annotated 276 pin activities.",
            ))
        log = self.root / "dual.log"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = MODULE.collect(
            self.root,
            log,
            design_prefix="fp64_dual_portfolio",
            expected_pins=276,
        )
        self.assertTrue(all(
            profile["all_top_input_pins_annotated"]
            for profile in result["profiles"].values()))


if __name__ == "__main__":
    unittest.main()
