#!/usr/bin/env python3
"""Focused fail-closed tests for single-treatment hybrid checkpoint replay."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import (
    redirect_stderr,
    redirect_stdout,
)
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
RUNNER = (
    ROOT / "experiments/scripts/replay_general_hybrid_checkpoint_treatment.py"
)
SPEC = importlib.util.spec_from_file_location("hybrid_single_replay", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


EXIT = "Exiting @ tick 123 because m5_exit instruction encountered\n"
STATS = """---------- Begin Simulation Statistics ----------
simTicks {ticks}
---------- End Simulation Statistics   ----------
"""


class ReplayGeneralHybridCheckpointTreatmentTest(unittest.TestCase):
    def write_file(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def result_line(self, workload: str, mode: str) -> str:
        if workload == "api":
            return f"VIRTUAL_TILE_CONSUMER_RESULT mode={mode} hash=api-key errors=0\n"
        if workload == "cg":
            return "CG_FINGERPRINT result=PASS nonfinite_x=0 nonfinite_z=0 x_q5=cg-key\n"
        return "UME_OUTPUT_FP nonfinite=0 output_hash=ume-key\nUME_REFERENCE_PASS output_errors=0\n"

    def make_source(
        self, root: Path, workload: str = "api"
    ) -> tuple[Path, dict[str, object]]:
        source = root / "source"
        inputs = source / "inputs"
        for name in (
            "gem5.opt",
            "libramulator.so",
            "ramulator.yaml",
            "hybrid",
        ):
            self.write_file(inputs / name, name + "\n")
        selector_path = source / "treatment.txt"
        self.write_file(selector_path, "token_stream_ld 4096\n")
        checkpoint = source / "checkpoints" / "hybrid" / "gem5"
        self.write_file(checkpoint / "m5.cpt", "checkpoint\n")
        identity = runner.tree_identity(checkpoint)
        arms = [
            {
                "name": "native16",
                "profile": "native16",
                "binary": "hybrid",
                "checkpoint_group": "hybrid",
                "selector": None,
                "role": "native_control",
            },
            {
                "name": "native4",
                "profile": "native4",
                "binary": "hybrid",
                "checkpoint_group": "hybrid",
                "selector": None,
                "role": "native_control",
            },
            {
                "name": "hybrid_stream_control",
                "profile": "hybrid",
                "binary": "hybrid",
                "checkpoint_group": "hybrid",
                "selector": "paged 4096",
                "role": "ordinary_stream_control",
            },
            {
                "name": "hybrid_page_gated",
                "profile": "hybrid",
                "binary": "hybrid",
                "checkpoint_group": "hybrid",
                "selector": "paged_overlap 4096",
                "role": "page_gated_stream_control",
            },
            {
                "name": "hybrid_token_stream_ld",
                "profile": "hybrid",
                "binary": "hybrid",
                "checkpoint_group": "hybrid",
                "selector": "token_stream_ld 4096",
                "role": "token_stream_ld_correctness_control",
            },
        ]
        for arm in arms:
            run = source / "arms" / str(arm["name"]) / "replica-1"
            mode = str(arm["selector"] or "native").split()[0]
            self.write_file(run / "restore.exit", "0\n")
            self.write_file(
                run / "restore.log",
                f"TREATMENT mode={mode}\n"
                + self.result_line(workload, mode)
                + EXIT,
            )
            self.write_file(run / "gem5/stats.txt", STATS.format(ticks=1000))
            if arm["selector"] is not None:
                self.write_file(
                    run / "treatment.txt", str(arm["selector"]) + "\n"
                )
        artifacts = {
            "gem5": inputs / "gem5.opt",
            "ramulator_library": inputs / "libramulator.so",
            "ramulator_config": inputs / "ramulator.yaml",
            "hybrid": inputs / "hybrid",
        }
        manifest = {
            "schema": runner.MATRIX_SCHEMA,
            "source_status": "clean",
            "source_commit": "source-commit",
            "workload": workload,
            "replicas": 1,
            "profiles": {**runner.PROFILE, "native16": {}, "native4": {}},
            "arms": arms,
            "mem_channels": 2,
            "l3_ports": 4,
            "selector_path": str(selector_path),
            "options": {"hybrid": f"deferred {selector_path}"},
            "extra_gem5_args": [],
            "restore_arm_gem5_args": {},
            "checkpoint_identity": {"hybrid": identity},
            "artifacts": {
                key: {"path": str(path), "sha256": runner.sha256_file(path)}
                for key, path in artifacts.items()
            },
        }
        self.write_file(source / "manifest.json", json.dumps(manifest))
        self.write_file(source / "campaign.exit", "0\n")
        self.write_file(source / "campaign.complete", "done\n")
        return source, manifest

    def plan(
        self, source: Path, root: Path, treatment: str = "candidate"
    ) -> tuple[int, str, str]:
        gem5 = root / "candidate-gem5"
        config = root / "candidate.py"
        self.write_file(gem5, "candidate\n")
        self.write_file(config, "CONFIG = 1\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = [
            str(RUNNER),
            "--source-matrix",
            str(source),
            "--checkpoint-group",
            "hybrid",
            "--control-arm",
            "hybrid_token_stream_ld",
            "--treatment-name",
            treatment,
            "--out",
            str(root / "out"),
            "--gem5",
            str(gem5),
            "--config",
            str(config),
        ]
        with patch("sys.argv", argv), redirect_stdout(stdout), redirect_stderr(
            stderr
        ):
            rc = runner.main()
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_api_source_plan_reuses_exact_control_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _ = self.make_source(root, "api")
            rc, stdout, stderr = self.plan(source, root)
            self.assertEqual(rc, 0, stderr)
            plan = json.loads(stdout)
            self.assertEqual(plan["workload"], "api")
            self.assertEqual(
                plan["frozen_source_arm"]["selector"], "token_stream_ld 4096"
            )
            self.assertEqual(plan["frozen_source_arm"]["profile"], "hybrid")
            self.assertEqual(plan["source_control_exact_key"], "api-key")

    def test_restore_command_keeps_the_source_matrix_cpu_shape(self) -> None:
        command = runner.restore_command(
            Path("gem5"),
            Path("se.py"),
            Path("out"),
            Path("checkpoint"),
            Path("guest"),
            "deferred selector",
            "hybrid",
            Path("ramulator.yaml"),
            2,
            4,
            [],
        )
        n_index = command.index("-n")
        self.assertEqual(
            command[n_index + 1 : n_index + 3], ["4", "--mem-size"]
        )
        self.assertIn("--checkpoint-dir=checkpoint", command)
        self.assertIn("--maa_num_tile_elements=16384", command)

    def test_cg_and_ume_exact_contracts_are_supported(self) -> None:
        for workload, key in (
            ("cg", "cg-key"),
            ("ume-gzp", "ume-key"),
            ("ume-gzz", "ume-key"),
        ):
            with self.subTest(
                workload=workload
            ), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, _ = self.make_source(root, workload)
                rc, stdout, stderr = self.plan(source, root)
                self.assertEqual(rc, 0, stderr)
                self.assertEqual(
                    json.loads(stdout)["source_control_exact_key"], key
                )

    def test_checkpoint_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _ = self.make_source(root)
            self.write_file(
                source / "checkpoints/hybrid/gem5/m5.cpt", "mutated\n"
            )
            rc, _, stderr = self.plan(source, root)
            self.assertEqual(rc, 1)
            self.assertIn("checkpoint identity mismatch", stderr)

    def test_restore_time_checkpoint_mutation_is_rejected_after_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root, manifest = self.make_source(root)
            candidate_dir = root / "candidate-config"
            gem5 = root / "candidate-gem5"
            config = candidate_dir / "se.py"
            self.write_file(gem5, "candidate\n")
            self.write_file(config, "CONFIG = 1\n")
            source = runner.selected_source(
                source_root, manifest, "hybrid", "hybrid_token_stream_ld"
            )
            args = type(
                "Args",
                (),
                {
                    "out": root / "out",
                    "gem5": gem5,
                    "config": config,
                    "treatment_name": "candidate",
                    "checkpoint_group": "hybrid",
                    "sole_arm_gem5_arg": [],
                },
            )()

            def fake_run_logged(command, log, environment):
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(
                    "TREATMENT mode=token_stream_ld\n"
                    + self.result_line("api", "token_stream_ld")
                    + EXIT,
                    encoding="utf-8",
                )
                log.with_suffix(".exit").write_text("0\n", encoding="utf-8")
                self.write_file(
                    log.parent / "gem5/stats.txt", STATS.format(ticks=999)
                )
                self.write_file(
                    source_root / "checkpoints/hybrid/gem5/m5.cpt",
                    "mutated after restore\n",
                )
                return 0

            def fake_subprocess(command, **kwargs):
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": str(
                            Path(command[1]).parent / "libramulator.so"
                        ),
                        "stderr": "",
                    },
                )()

            with patch.object(
                runner, "source_clean", return_value=("local", "clean")
            ), patch.object(
                runner, "run_logged", side_effect=fake_run_logged
            ), patch.object(
                runner.subprocess, "run", side_effect=fake_subprocess
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "checkpoint mutated"
                ):
                    runner.execute(
                        args,
                        source_root,
                        manifest,
                        runner.sha256_file(source_root / "manifest.json"),
                        source,
                    )
            self.assertEqual(
                (root / "out/campaign.exit").read_text(encoding="utf-8"), "1\n"
            )

    def test_success_emits_immutable_hashed_manifest_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root, manifest = self.make_source(root)
            candidate_dir = root / "candidate-config"
            gem5 = root / "candidate-gem5"
            config = candidate_dir / "se.py"
            self.write_file(gem5, "candidate\n")
            self.write_file(config, "CONFIG = 1\n")
            source = runner.selected_source(
                source_root, manifest, "hybrid", "hybrid_token_stream_ld"
            )
            args = type(
                "Args",
                (),
                {
                    "out": root / "out",
                    "gem5": gem5,
                    "config": config,
                    "treatment_name": "candidate",
                    "checkpoint_group": "hybrid",
                    "sole_arm_gem5_arg": [],
                },
            )()

            def fake_run_logged(command, log, environment):
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(
                    "TREATMENT mode=token_stream_ld\n"
                    + self.result_line("api", "token_stream_ld")
                    + EXIT,
                    encoding="utf-8",
                )
                log.with_suffix(".exit").write_text("0\n", encoding="utf-8")
                self.write_file(
                    log.parent / "gem5/stats.txt", STATS.format(ticks=999)
                )
                return 0

            def fake_subprocess(command, **kwargs):
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": str(
                            Path(command[1]).parent / "libramulator.so"
                        ),
                        "stderr": "",
                    },
                )()

            with patch.object(
                runner, "source_clean", return_value=("local", "clean")
            ), patch.object(
                runner, "run_logged", side_effect=fake_run_logged
            ), patch.object(
                runner.subprocess, "run", side_effect=fake_subprocess
            ):
                runner.execute(
                    args,
                    source_root,
                    manifest,
                    runner.sha256_file(source_root / "manifest.json"),
                    source,
                )
            report_path = root / "out/report.json"
            manifest_path = root / "out/manifest.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["treatment_first_roi_simTicks"], 999)
            self.assertEqual(
                set(report["binary_hashes"]),
                {
                    "source_gem5_sha256",
                    "candidate_gem5_sha256",
                    "source_guest_sha256",
                },
            )
            self.assertEqual(report_path.stat().st_mode & 0o222, 0)
            self.assertEqual(manifest_path.stat().st_mode & 0o222, 0)

    def test_treatment_exact_key_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root, manifest = self.make_source(root)
            candidate_dir = root / "candidate-config"
            gem5 = root / "candidate-gem5"
            config = candidate_dir / "se.py"
            self.write_file(gem5, "candidate\n")
            self.write_file(config, "CONFIG = 1\n")
            source = runner.selected_source(
                source_root, manifest, "hybrid", "hybrid_token_stream_ld"
            )
            args = type(
                "Args",
                (),
                {
                    "out": root / "out",
                    "gem5": gem5,
                    "config": config,
                    "treatment_name": "candidate",
                    "checkpoint_group": "hybrid",
                    "sole_arm_gem5_arg": [],
                },
            )()

            def fake_run_logged(command, log, environment):
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(
                    "TREATMENT mode=token_stream_ld\n"
                    "VIRTUAL_TILE_CONSUMER_RESULT mode=token_stream_ld "
                    "hash=wrong-key errors=0\n" + EXIT,
                    encoding="utf-8",
                )
                log.with_suffix(".exit").write_text("0\n", encoding="utf-8")
                self.write_file(
                    log.parent / "gem5/stats.txt", STATS.format(ticks=999)
                )
                return 0

            def fake_subprocess(command, **kwargs):
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": str(
                            Path(command[1]).parent / "libramulator.so"
                        ),
                        "stderr": "",
                    },
                )()

            with patch.object(
                runner, "source_clean", return_value=("local", "clean")
            ), patch.object(
                runner, "run_logged", side_effect=fake_run_logged
            ), patch.object(
                runner.subprocess, "run", side_effect=fake_subprocess
            ):
                with self.assertRaisesRegex(RuntimeError, "correctness key"):
                    runner.execute(
                        args,
                        source_root,
                        manifest,
                        runner.sha256_file(source_root / "manifest.json"),
                        source,
                    )
            self.assertFalse((root / "out/report.json").exists())

    def test_incomplete_or_dirty_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _ = self.make_source(root)
            self.write_file(source / "campaign.exit", "1\n")
            rc, _, stderr = self.plan(source, root)
            self.assertEqual(rc, 1)
            self.assertIn("did not complete", stderr)
            self.write_file(source / "campaign.exit", "0\n")
            data = json.loads(
                (source / "manifest.json").read_text(encoding="utf-8")
            )
            data["source_status"] = "dirty"
            self.write_file(source / "manifest.json", json.dumps(data))
            rc, _, stderr = self.plan(source, root)
            self.assertEqual(rc, 1)
            self.assertIn("not created from a clean source", stderr)

    def test_selector_profile_and_existing_output_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, manifest = self.make_source(root)
            self.write_file(
                source / "arms/hybrid_token_stream_ld/replica-1/treatment.txt",
                "wrong 4096\n",
            )
            rc, _, stderr = self.plan(source, root)
            self.assertEqual(rc, 1)
            self.assertIn("selector mismatch", stderr)
            self.write_file(
                source / "arms/hybrid_token_stream_ld/replica-1/treatment.txt",
                "token_stream_ld 4096\n",
            )
            manifest["profiles"]["hybrid"] = {"logical": 1}
            self.write_file(source / "manifest.json", json.dumps(manifest))
            rc, _, stderr = self.plan(source, root)
            self.assertEqual(rc, 1)
            self.assertIn("profile mismatch", stderr)
            manifest["profiles"]["hybrid"] = runner.PROFILE["hybrid"]
            self.write_file(source / "manifest.json", json.dumps(manifest))
            (root / "out").mkdir()
            rc, _, stderr = self.plan(source, root)
            self.assertEqual(rc, 1)
            self.assertIn("refusing to overwrite", stderr)

    def test_unsupported_workload_and_conflicting_arm_arg_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, manifest = self.make_source(root, "api")
            manifest["workload"] = "gapbs-pr"
            self.write_file(source / "manifest.json", json.dumps(manifest))
            rc, _, stderr = self.plan(source, root)
            self.assertEqual(rc, 1)
            self.assertIn("unsupported workload", stderr)
        with self.assertRaisesRegex(Exception, "frozen replay invariant"):
            runner.parse_sole_arm_gem5_arg("--checkpoint-dir=/bad")


if __name__ == "__main__":
    unittest.main()
