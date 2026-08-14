#!/usr/bin/env python3
"""Deterministic command/selector contract for the matched micro matrix."""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_general_hybrid_benchmark_matrix.py"
SPEC = importlib.util.spec_from_file_location("general_hybrid_runner", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


class GeneralHybridBenchmarkContractTest(unittest.TestCase):
    def test_micro_arm_order_and_selectors_are_fixed(self) -> None:
        arms = runner.make_arms("api", True, True, [])
        self.assertEqual(
            [arm["name"] for arm in arms],
            [
                "native16",
                "native4",
                "hybrid_stream_control",
                "hybrid_page_gated",
                "hybrid_token_stream_ld",
                "hybrid_token_stream_ld_pingpong",
            ],
        )
        self.assertEqual(
            [arm["selector"] for arm in arms[2:]],
            [
                "paged 4096",
                "paged_overlap 4096",
                "token_stream_ld 4096",
                "token_stream_ld_pingpong 4096",
            ],
        )
        self.assertEqual(
            {arm["checkpoint_group"] for arm in arms[2:]}, {"hybrid"}
        )

    def test_non_api_pingpong_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "only by the API"):
            runner.make_arms("cg", True, True, [])

    def test_api_native16_can_share_the_hybrid_checkpoint(self) -> None:
        arms = runner.share_api_native16_hybrid_checkpoint(
            "api", runner.make_arms("api", True, False, [])
        )
        native16 = arms[0]
        self.assertEqual(native16["name"], "native16")
        self.assertEqual(native16["profile"], "native16")
        self.assertEqual(native16["binary"], "hybrid")
        self.assertEqual(native16["checkpoint_group"], "hybrid")
        self.assertEqual(native16["selector"], "native_direct 16384")
        self.assertEqual(
            {
                arm["checkpoint_group"]
                for arm in arms
                if arm["name"] != "native4"
            },
            {"hybrid"},
        )
        with self.assertRaisesRegex(ValueError, "only by the API"):
            runner.share_api_native16_hybrid_checkpoint(
                "cg", runner.make_arms("cg", True, False, [])
            )

    def test_page_zero_prearm_is_independently_selectable(self) -> None:
        arms = runner.make_arms("api", True, False, [], True)
        prearm = arms[-1]
        self.assertEqual(prearm["name"], "hybrid_token_stream_ld_page0_prearm")
        self.assertEqual(
            prearm["selector"], "token_stream_ld_page0_prearm 4096"
        )
        self.assertEqual(
            prearm["role"], "token_stream_ld_page0_prearm_correctness_control"
        )
        with self.assertRaisesRegex(ValueError, "only by the API"):
            runner.make_arms("cg", True, False, [], True)

    def test_gapbs_hybrid_matrix_fails_closed_when_not_wired(self) -> None:
        with self.assertRaisesRegex(ValueError, "no wired general hybrid"):
            runner.make_arms("gapbs-pr", True, False, [])
        controls = runner.make_arms("gapbs-bfs", False, False, [])
        self.assertEqual(
            [arm["name"] for arm in controls], ["native16", "native4"]
        )

    def test_profiles_bind_16k_metadata_and_4k_physical_hybrid(self) -> None:
        hybrid = runner.PROFILE["hybrid"]
        self.assertEqual(hybrid["logical"], 16384)
        self.assertEqual(hybrid["physical"], 4096)
        self.assertEqual(hybrid["offset_entries"], 16384)
        self.assertEqual(hybrid["offset_epoch_entries"], 16384)
        arguments = runner.profile_args("hybrid")
        for expected in (
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_num_offset_table_entries=16384",
            "--maa_num_offset_table_epoch_entries=16384",
        ):
            self.assertIn(expected, arguments)
        self.assertIn(
            "--maa_direct_retirement_line_handoff",
            runner.common_restore_args(Path("ramulator.yaml"), 2),
        )
        self.assertIn(
            "--l3_ports=8",
            runner.common_restore_args(Path("ramulator.yaml"), 2, 8),
        )

    def test_checkpoint_and_restore_capture_materializer_trace(self) -> None:
        checkpoint = runner.checkpoint_command(
            Path("gem5.opt"),
            Path("se.py"),
            Path("checkpoint-out"),
            Path("micro"),
            "deferred selector.txt",
        )
        restore = runner.restore_command(
            Path("gem5.opt"),
            Path("se.py"),
            Path("restore-out"),
            Path("checkpoint"),
            Path("micro"),
            "deferred selector.txt",
            "hybrid",
            Path("ramulator.yaml"),
            2,
            8,
            [],
        )
        for command in (checkpoint, restore):
            self.assertIn("--debug-flags=MAAVirtualTrace", command)
            self.assertIn("--debug-file=virtual_trace.log", command)
        self.assertIn("--l3_ports=8", restore)

    def test_restore_arm_gem5_args_are_exact_and_fail_closed(self) -> None:
        arms = runner.make_arms("api", True, False, [])
        self.assertEqual(runner.restore_arm_gem5_args([], arms), {})
        mapping = runner.restore_arm_gem5_args(
            [
                runner.parse_restore_arm_gem5_arg(
                    "hybrid_token_stream_ld="
                    "--maa_virtual_masked_fragment_slots=8"
                )
            ],
            arms,
        )
        self.assertEqual(
            mapping,
            {
                "hybrid_token_stream_ld": (
                    "--maa_virtual_masked_fragment_slots=8"
                )
            },
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            runner.restore_arm_gem5_args(
                [("native16", "--a=1"), ("native16", "--b=2")], arms
            )
        with self.assertRaisesRegex(ValueError, "unknown"):
            runner.restore_arm_gem5_args([("missing", "--a=1")], arms)
        for malformed in ("native16", "native16=not-an-option", "=--a=1"):
            with self.assertRaises(Exception):
                runner.parse_restore_arm_gem5_arg(malformed)

    def test_restore_arm_args_are_in_plan_and_not_checkpoint_commands(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = {
                name: root / name
                for name in (
                    "gem5",
                    "ramulator.so",
                    "ramulator.yaml",
                    "se.py",
                    "native16",
                    "native4",
                    "hybrid",
                )
            }
            for path in inputs.values():
                path.write_text("input", encoding="utf-8")
            stdout = io.StringIO()
            argv = [
                str(RUNNER),
                "--workload",
                "api",
                "--out",
                str(root / "out"),
                "--gem5",
                str(inputs["gem5"]),
                "--ramulator-library",
                str(inputs["ramulator.so"]),
                "--ramulator-config",
                str(inputs["ramulator.yaml"]),
                "--config",
                str(inputs["se.py"]),
                "--native16",
                str(inputs["native16"]),
                "--native4",
                str(inputs["native4"]),
                "--hybrid",
                str(inputs["hybrid"]),
                "--hybrid-options",
                "deferred {selector}",
                "--shared-native16-hybrid-checkpoint",
                "--restore-arm-gem5-arg",
                "hybrid_token_stream_ld=--maa_virtual_masked_fragment_slots=8",
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(stdout):
                self.assertEqual(runner.main(), 0)
            plan = json.loads(stdout.getvalue())
            self.assertEqual(
                plan["restore_arm_gem5_args"],
                {
                    "hybrid_token_stream_ld": (
                        "--maa_virtual_masked_fragment_slots=8"
                    )
                },
            )
        self.assertEqual(
            runner.restore_args_for_arm(
                ["--global=1"],
                plan["restore_arm_gem5_args"],
                "hybrid_token_stream_ld",
            ),
            ["--global=1", "--maa_virtual_masked_fragment_slots=8"],
        )
        checkpoint = runner.checkpoint_command(
            Path("gem5.opt"),
            Path("se.py"),
            Path("checkpoint-out"),
            Path("micro"),
            "deferred selector.txt",
        )
        self.assertNotIn("--maa_virtual_masked_fragment_slots=8", checkpoint)

    def test_config_freeze_preserves_relative_import_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_root = root / "configs"
            source = config_root / "deprecated" / "example" / "se.py"
            common = config_root / "common" / "Options.py"
            source.parent.mkdir(parents=True)
            common.parent.mkdir(parents=True)
            source.write_text("from common import Options\n", encoding="utf-8")
            common.write_text("VALUE = 1\n", encoding="utf-8")

            frozen, identity = runner.freeze_config_tree(
                source, config_root, root / "frozen-configs"
            )

            self.assertEqual(
                frozen,
                (root / "frozen-configs/deprecated/example/se.py").resolve(),
            )
            self.assertTrue(
                (root / "frozen-configs/common/Options.py").is_file()
            )
            self.assertEqual(len(identity["files"]), 2)

    def test_artifact_freeze_rejects_a_concurrent_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "frozen"
            source.write_bytes(b"stable input")
            self.assertEqual(
                runner.copy_stable_artifact(source, destination),
                runner.sha256_file(source),
            )
            with patch.object(
                runner,
                "sha256_file",
                side_effect=("before", "after", "frozen"),
            ):
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    runner.copy_stable_artifact(source, destination)
            self.assertFalse(destination.exists())

    def test_future_treatment_is_explicit_not_control_alias(self) -> None:
        arms = runner.make_arms(
            "cg",
            True,
            False,
            [{"name": "generic_line", "selector": "generic_line"}],
        )
        future = arms[-1]
        self.assertEqual(future["role"], "future_explicit_treatment")
        self.assertEqual(future["selector"], "generic_line")
        self.assertNotEqual(future["selector"], "token_stream_ld")

    def test_deferred_options_require_exact_selector_placeholder(self) -> None:
        selector = Path("/evidence/treatment.txt")
        self.assertEqual(
            runner.render_options("deferred {selector}", selector),
            "deferred /evidence/treatment.txt",
        )
        self.assertEqual(
            runner.render_options("-f {input0}", None, [Path("/frozen/g.sg")]),
            "-f /frozen/g.sg",
        )
        with self.assertRaises(ValueError):
            runner.render_options("deferred", selector)

    def test_integrated_sources_emit_fail_closed_correctness_markers(
        self,
    ) -> None:
        expected = {
            "benchmarks/API/test_virtual_tile_consumer.cpp": (
                "VIRTUAL_TILE_CONSUMER_RESULT mode=",
                "errors=",
            ),
            "benchmarks/NAS/cg/cg.cpp": ("CG_FINGERPRINT mode=", "result=%s"),
            "benchmarks/UME/gradzatp.cpp": (
                "UME_OUTPUT_FP output_hash=",
                "UME_REFERENCE_PASS",
            ),
            "benchmarks/UME/gradzatz.cpp": (
                "UME_OUTPUT_FP output_hash=",
                "UME_REFERENCE_PASS",
            ),
            "benchmarks/gapbs/src/pr.cc": ("PR_FP", "unquantizable="),
            "benchmarks/gapbs/src/bfs.cc": (
                "BFS_FP levels=",
                "invalid_chains=",
            ),
            "benchmarks/spatter/src/Spatter/Configuration.cc": (
                "MAA_GATHER_VERIFY_PASS length=",
                "hash=",
            ),
        }
        for relative, markers in expected.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            for marker in markers:
                self.assertIn(marker, source, relative)

    def test_api_materializer_uses_dedicated_page_registers(self) -> None:
        source = (
            ROOT / "benchmarks/API/test_virtual_tile_consumer.cpp"
        ).read_text(encoding="utf-8")
        pingpong = source.split("if (token_stream_ld_pingpong) {", 1)[1]
        pingpong, serial = pingpong.split("} else if (!transparent) {", 1)
        serial = serial.split("if (overlap_pages || token_stream_ld)", 1)[0]

        for branch in (pingpong, serial):
            self.assertIn("page_min_reg", branch)
            self.assertIn("page_max_reg", branch)
            self.assertIn("page_stride_reg", branch)
        self.assertNotIn("completion_tile, min_reg, max_reg", pingpong)
        self.assertNotIn("completion_tile, min_reg, max_reg", serial)

        # These writes would be an admission fence because the live producer
        # still owns its logical-range registers.
        before_first_materializer = pingpong.split(
            "maa_stream_load_virtual_page<double>", 1
        )[0]
        self.assertNotIn("maa_const(", before_first_materializer)
        token_body = serial.split("if (token_stream_ld) {", 1)[1].split(
            "} else {", 1
        )[0]
        self.assertNotIn("maa_const(", token_body)

    def test_cg_and_ume_materializers_use_immutable_page_registers(
        self,
    ) -> None:
        checks = {
            "benchmarks/NAS/cg/cg.cpp": (
                "page_min_reg",
                "page_max_reg",
                "page_stride_reg",
            ),
            "benchmarks/UME/gradzatp.cpp": (
                "page_min_reg",
                "page_max_reg",
                "page_stride_reg",
            ),
            "benchmarks/UME/gradzatz.cpp": (
                "page_min_reg",
                "page_max_reg",
                "page_stride_reg",
            ),
        }
        for relative, names in checks.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("MAA_GENERAL_VIRTUAL_CONSUMER", source, relative)
            for name in names:
                self.assertIn(name, source, relative)
            general = source.split("MAA_GENERAL_VIRTUAL_CONSUMER", 1)[1]
            self.assertNotIn("maa_const<int>(0, page_min_reg)", general)
            self.assertNotIn(
                "maa_const<int>(page_size, page_max_reg)", general
            )

    def test_cg_general_consumer_shares_immutable_page_bounds(self) -> None:
        source = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "static int page_min_reg, page_max_reg, page_stride_reg;",
            source,
        )
        allocation = source.split("#pragma omp barrier", 1)[1].split(
            "/* the conj grad iteration loop */", 1
        )[0]
        self.assertIn("#pragma omp single", allocation)
        self.assertIn("page_min_reg = get_new_reg<int>(0);", allocation)
        self.assertIn(
            "page_max_reg = get_new_reg<int>(MAA_CONSUMER_TILE_SIZE);",
            allocation,
        )
        self.assertIn("page_stride_reg = get_new_reg<int>(1);", allocation)

        page_loads = source.split("maa_virtual_consumer_load_page<float>")[1:]
        self.assertEqual(len(page_loads), 2)
        for load in page_loads:
            arguments = load.split(");", 1)[0]
            self.assertIn(
                "page_min_reg, page_max_reg, page_stride_reg", arguments
            )

    def test_page_zero_prearm_is_explicit_and_exact(self) -> None:
        source = (ROOT / "src/mem/MAA/MAA.cc").read_text(encoding="utf-8")
        guest = (
            ROOT / "benchmarks/API/test_virtual_tile_consumer.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("isPageZeroPrearmMaterialization", source)
        for marker in (
            "instruction->src2SpdID != instruction->src1SpdID",
            "instruction->datatype != Instruction::DataType::FLOAT64_TYPE",
            "rf->getData<int>(instruction->src1RegID) != 0",
            "virtualPageBackingAddr[instruction->src1SpdID]",
            "event=page_materialization_prearm schema=1",
            "queuePageZeroPrearm",
            "activatePendingPageZeroPrearms",
            "event=page_materialization_prearm_activate schema=1",
        ):
            self.assertIn(marker, source)
        queued = source.split("queuePageZeroPrearm(instruction)", 1)[1]
        self.assertLess(
            queued.index("pkt->makeTimingResponse()"),
            queued.index("submitPageMaterialization(instruction)"),
        )
        prearm, producer = guest.split("if (token_stream_ld_page0_prearm)", 1)[
            1
        ].split('if (mode == "paged_staged"', 1)
        self.assertIn("maa_stream_load_virtual_page_prearm<double>", prearm)
        self.assertIn("maa_indirect_load_virtual", producer)

    def test_batched_materializer_wakeup_is_opt_in_and_exact(self) -> None:
        source = (ROOT / "src/mem/MAA/MAA.cc").read_text(encoding="utf-8")
        policy = (ROOT / "src/mem/MAA/HybridConsumerPipeline.hh").read_text(
            encoding="utf-8"
        )
        options = (ROOT / "configs/common/Options.py").read_text(
            encoding="utf-8"
        )
        config = (ROOT / "configs/common/MAAConfig.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("MaxEarlyWakeupBatches = 16", policy)
        self.assertIn("isEarlyWakeupLine", policy)
        self.assertIn("page_materialization_wakeup_batches", source)
        self.assertIn("page_materialization_wakeup_batches", options)
        self.assertIn("page_materialization_wakeup_batches", config)
        self.assertIn(
            "page_materialization_wakeup_batches = Param.Unsigned(\n        0,",
            (ROOT / "src/mem/MAA/MAA.py").read_text(encoding="utf-8"),
        )
        service = source.split("MAA::servicePageMaterialization()", 1)[
            1
        ].split("auto discardUnsentPacket", 1)[0]
        self.assertIn(
            "virtualPageGeneration[request.owner.tokenTile]", service
        )
        commit = source.split(
            "event=page_materialization_line_commit schema=1", 1
        )[0].rsplit("for (uint16_t word", 1)[1]
        self.assertIn("isEarlyWakeupLine", commit)
        self.assertIn("spd->wakeup_waiting_units", commit)
        self.assertLess(
            commit.index("spd->wakeup_waiting_units"),
            commit.index("completeMaterialize"),
        )
        batched_block = commit.split(
            "if (HybridConsumerPipeline::isEarlyWakeupLine(", 1
        )[1]
        self.assertNotIn("isPageZeroPrearmMaterialization", batched_block)

    def test_masked_fragment_accumulation_is_bounded_and_default_off(
        self,
    ) -> None:
        sim_object = (ROOT / "src/mem/MAA/MAA.py").read_text(encoding="utf-8")
        options = (ROOT / "configs/common/Options.py").read_text(
            encoding="utf-8"
        )
        config = (ROOT / "configs/common/MAAConfig.py").read_text(
            encoding="utf-8"
        )
        pipeline = (ROOT / "src/mem/MAA/HybridConsumerPipeline.hh").read_text(
            encoding="utf-8"
        )
        for text in (sim_object, options, config):
            self.assertIn("page_materialization_fragment_buffers", text)
        self.assertIn(
            "page_materialization_fragment_buffers = Param.Unsigned(\n"
            "        0,",
            sim_object,
        )
        self.assertIn("choices=range(0, 17)", options)
        self.assertIn(
            "MaxMaterializationFragmentBuffers =\n        LineBufferCount",
            pipeline,
        )
        self.assertIn("BufferState::ProducerFragments", pipeline)

    def test_gapbs_adds_native4_controls_without_false_hybrid_target(
        self,
    ) -> None:
        makefile = (ROOT / "benchmarks/gapbs/Makefile").read_text(
            encoding="utf-8"
        )
        self.assertIn("pr_maa_2G_4K_fp", makefile)
        self.assertIn("bfs_maa_2G_4K_fp", makefile)
        self.assertNotIn("GENERAL_VIRTUAL_CONSUMER", makefile)


if __name__ == "__main__":
    unittest.main()
