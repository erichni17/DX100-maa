#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "collect_results.py"
SPEC = importlib.util.spec_from_file_location("collect_results", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CollectResultsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.top = "ExampleTop"
        self.logs = (
            self.root
            / "bazel-bin/lanl_fp64/logs/nangate45"
            / self.top
            / "base"
        )
        self.reports = (
            self.root
            / "bazel-bin/lanl_fp64/reports/nangate45"
            / self.top
            / "base"
        )
        self.results = (
            self.root
            / "bazel-bin/lanl_fp64/results/nangate45"
            / self.top
            / "base"
        )
        for directory in (self.logs, self.reports, self.results):
            directory.mkdir(parents=True)

        self.final = {
            "finish__design__instance__count": 100,
            "finish__design__instance__area": 25.0,
            "finish__design__die__area": 80.0,
            "finish__design__core__area": 64.0,
            "finish__design__instance__utilization": 0.390625,
            "finish__timing__setup__ws": 10.0,
            "finish__timing__setup__tns": 0.0,
            "finish__timing__hold__ws": 2.0,
            "finish__timing__hold__tns": 0.0,
            "finish__timing__fmax": 101000000.0,
            "finish__power__internal__total": 0.01,
            "finish__power__switching__total": 0.02,
            "finish__power__leakage__total": 0.001,
            "finish__power__total": 0.031,
            "finish__flow__errors__count": 0,
        }
        self.route = {
            "detailedroute__route__drc_errors": 0,
            "detailedroute__route__wirelength": 1200,
            "detailedroute__route__vias": 300,
            "detailedroute__flow__errors__count": 0,
        }
        self.write_inputs()

    def tearDown(self):
        self.tempdir.cleanup()

    def write_inputs(self):
        (self.logs / "6_report.json").write_text(
            json.dumps(self.final), encoding="utf-8"
        )
        (self.logs / "5_2_route.json").write_text(
            json.dumps(self.route), encoding="utf-8"
        )
        (self.reports / "5_route_drc.rpt").write_text(
            "clean\n", encoding="utf-8"
        )
        (self.results / "6_final.spef").write_text(
            "*SPEF test\n", encoding="utf-8"
        )

    def test_strict_summary_and_claim_boundary_fields(self):
        summary = MODULE.summarize_top(self.root, self.top)
        self.assertEqual(
            summary["physical_metrics"]["instance_area_um2"], 25.0
        )
        self.assertEqual(summary["physical_metrics"]["setup_slack_ns"], 10.0)
        self.assertEqual(summary["physical_metrics"]["hold_slack_ns"], 2.0)
        self.assertTrue(summary["checks"]["target_10ns_setup_met"])
        self.assertTrue(summary["checks"]["detailed_route_drc_clean"])
        self.assertFalse(
            summary["default_activity_power_not_workload_derived_w"][
                "eligible_for_energy_claim"
            ]
        )
        self.assertEqual(len(summary["raw_evidence"]), 4)
        for evidence in summary["raw_evidence"]:
            self.assertEqual(len(evidence["sha256"]), 64)

    def test_missing_metric_fails_closed(self):
        del self.final["finish__timing__setup__ws"]
        self.write_inputs()
        with self.assertRaisesRegex(RuntimeError, "required ORFS metric"):
            MODULE.summarize_top(self.root, self.top)

    def test_nonclean_route_is_preserved(self):
        self.route["detailedroute__route__drc_errors"] = 7
        self.write_inputs()
        summary = MODULE.summarize_top(self.root, self.top)
        self.assertFalse(summary["checks"]["detailed_route_drc_clean"])

    def test_relative_preserves_workspace_path_through_symlink(self):
        with tempfile.TemporaryDirectory() as output_dir:
            target = Path(output_dir) / "artifact.json"
            target.write_text("{}\n", encoding="utf-8")
            link = self.root / "bazel-bin-link"
            link.symlink_to(Path(output_dir), target_is_directory=True)
            self.assertEqual(
                MODULE.relative(link / "artifact.json", self.root),
                "bazel-bin-link/artifact.json",
            )

    def test_relative_rejects_lexical_escape(self):
        with self.assertRaisesRegex(RuntimeError, "outside Bazel root"):
            MODULE.relative(self.root.parent / "outside.json", self.root)

    def test_platform_provenance_is_hashed_and_pvt_checked(self):
        platform = self.root / "platform"
        liberty = platform / "lib/NangateOpenCellLibrary_typical.lib"
        technology_lef = platform / "lef/NangateOpenCellLibrary.tech.lef"
        cell_lef = platform / "lef/NangateOpenCellLibrary.macro.mod.lef"
        liberty.parent.mkdir(parents=True)
        technology_lef.parent.mkdir(parents=True)
        liberty.write_text(
            "\n".join(MODULE.EXPECTED_LIBERTY_MARKERS) + "\n",
            encoding="utf-8",
        )
        technology_lef.write_text("VERSION 5.8 ;\n", encoding="utf-8")
        cell_lef.write_text("VERSION 5.8 ;\n", encoding="utf-8")

        summary = MODULE.summarize_platform(platform)
        self.assertEqual(summary["process_corner"], "TypTyp")
        self.assertEqual(summary["nominal_temperature_c"], 25.0)
        self.assertEqual(summary["nominal_voltage_v"], 1.1)
        for item in summary["files"].values():
            self.assertEqual(len(item["sha256"]), 64)

        liberty.write_text("not the expected corner\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Liberty marker"):
            MODULE.summarize_platform(platform)


if __name__ == "__main__":
    unittest.main()
