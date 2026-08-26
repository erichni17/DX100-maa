#!/usr/bin/env python3
"""Focused adversarial contract for the CG reduction-order diagnosis."""

import ast
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "benchmarks/NAS/cg/cg.cpp"
RUNNER_PATH = (
    ROOT / "experiments/scripts/run_cg_page_fed_reduction_order_diagnosis.py"
)
REPORT_PATH = (
    ROOT
    / "experiments/analysis/cg_page_fed_reduction_order_diagnosis_2026-08-25.md"
)


def require_runner_contract(text: str) -> None:
    required = (
        "--cg-na",
        "MAX_DIAGNOSTIC_CG_NA = 32768",
        "full CG is forbidden",
        "-DCG_DETERMINISTIC_REDUCTIONS",
        "-DCG_REDUCTION_EVIDENCE",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=10",
        "-DTILE_SIZE=16384",
        "--maa_num_offset_table_entries=16384",
        "--maa_physical_tile_elements=4096",
        "--maa_num_initial_row_table_slices=32",
        "--mem-channels=2",
        "checkpoint_files.before",
        "checkpoint_files.after",
        "artifact_sha256.before",
        "artifact_sha256.after",
        "source_status.before",
        "source_status.after",
        "fingerprint_exact_equal",
        "reduction_partial_and_downstream_bits_exact_equal",
        "IND_SoaJitValueDeliveries",
        "IND_SoaJitValueFills",
        "IND_SoaJitValueHits",
        "IND_SoaJitValueMergedWaiters",
        "raw_root.sha256",
        "gate.complete",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise AssertionError(f"runner contract missing {missing}")


class CgReductionSourceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text()
        definition = cls.source.index(
            "static void conj_grad_maa(int colidx[],\n"
            "                          int rowstr[]"
        )
        cls.maa = cls.source[
            definition : cls.source.index(
                "static void conj_grad_base(int colidx[]", definition
            )
        ]
        helper_start = cls.source.index(
            "static_assert(NUM_CORES == 4",
            cls.source.index("#ifdef CG_DETERMINISTIC_REDUCTIONS"),
        )
        helper_end = cls.source.index(
            "#ifdef MAA_VIRTUAL_GATHER", helper_start
        )
        cls.helper = cls.source[helper_start:helper_end]

    def test_mode_is_opt_in_and_strictly_four_thread(self):
        self.assertIn("#ifdef CG_DETERMINISTIC_REDUCTIONS", self.source)
        self.assertIn("static_assert(NUM_CORES == 4", self.helper)
        self.assertIn("omp_get_num_threads() != NUM_CORES", self.helper)
        self.assertIn(
            "CG reduction evidence requires deterministic reductions",
            self.source,
        )

    def test_partial_array_has_fixed_tid_order_and_single_writer(self):
        for token in (
            "cg_reduction_partials[NUM_CORES]",
            "cg_reduction_partials[tid] = partial",
            "#pragma omp barrier",
            "if (tid == 0)",
            "for (int reduction_tid = 0; reduction_tid < NUM_CORES;",
            "total += cg_reduction_partials[reduction_tid]",
            "*destination = total",
        ):
            self.assertIn(token, self.helper)
        float_helper = self.helper[
            self.helper.index(
                "static void\ncg_deterministic_reduce("
            ) : self.helper.index(
                "static void\ncg_deterministic_outer_reduce("
            )
        ]
        self.assertEqual(float_helper.count("#pragma omp barrier"), 1)

    def test_all_four_maa_float_reductions_use_the_helper(self):
        calls = (
            'rho_tmp, &rho, "initial_rho", 0',
            'd_tmp, &d, "d", cgit',
            'rho_tmp, &rho, "rho", cgit',
            'sum_tmp, &sum, "final_sum", 0',
        )
        for call in calls:
            self.assertEqual(self.maa.count(call), 1)
        self.assertEqual(self.maa.count("cg_deterministic_reduce("), 4)

    def test_all_reduction_contributing_dynamic_tails_have_static_opt_in(self):
        tail = (
            r"for \(j = lastcol_firstcol_plus1_divisible_by_32;\s+"
            r"j < lastcol_firstcol_plus1; j\+\+\)"
        )
        guard = (
            "#ifdef CG_DETERMINISTIC_REDUCTIONS\n"
            "#pragma omp for schedule(static) nowait\n"
            "#else\n"
            "#pragma omp for schedule(dynamic) nowait\n"
            "#endif\n"
        )
        guarded_tails = re.findall(
            re.escape(guard) + r"[ \t]*" + tail + r" \{\n(.*?)\n\s*\}",
            self.maa,
            re.DOTALL,
        )
        self.assertEqual(len(guarded_tails), 4)
        self.assertEqual(
            sum("rho_tmp += r[j] * r[j];" in body for body in guarded_tails),
            2,
        )
        self.assertEqual(
            sum("r[j] -= alpha * q[j];" in body for body in guarded_tails),
            1,
        )
        self.assertEqual(
            sum("d_tmp += p[j] * q[j];" in body for body in guarded_tails),
            1,
        )
        self.assertEqual(
            sum("sum_tmp += suml * suml;" in body for body in guarded_tails),
            1,
        )
        self.assertEqual(self.maa.count("schedule(static) nowait"), 4)

    def test_each_destination_has_post_reduction_barrier_before_consumption(
        self,
    ):
        checks = (
            ('rho_tmp, &rho, "initial_rho"', "page_min_reg ="),
            ('d_tmp, &d, "d", cgit', "alpha = rho0 / d;"),
            ('rho_tmp, &rho, "rho", cgit', "beta = rho / rho0;"),
            ('sum_tmp, &sum, "final_sum"', "*rnorm = sqrt(sum);"),
        )
        for reduction, consumer in checks:
            start = self.maa.index(reduction)
            end = self.maa.index(consumer, start)
            between = self.maa[start:end]
            self.assertIn("#pragma omp barrier", between)

    def test_original_algorithmic_post_reduction_barriers_remain(self):
        for endpoint in (
            "r7 = get_new_reg<int>();\n    }\n#endif\n\n#pragma omp barrier",
            "d += d_tmp;\n        }\n#endif\n#pragma omp barrier",
            "rho += rho_tmp;\n        }\n#endif\n#pragma omp barrier",
            "sum += sum_tmp;\n    }\n#endif\n#pragma omp barrier",
        ):
            self.assertIn(endpoint, self.maa)

    def test_allocator_is_still_serialized_but_does_not_accumulate(self):
        deterministic = self.maa[
            self.maa.index('rho_tmp, &rho, "initial_rho"') : self.maa.index(
                "#else", self.maa.index('rho_tmp, &rho, "initial_rho"')
            )
        ]
        self.assertIn("#pragma omp critical", deterministic)
        self.assertIn("get_new_tile<int>()", deterministic)
        self.assertNotIn("rho +=", deterministic)

    def test_evidence_is_compact_bits_not_memory_accesses(self):
        for token in (
            "phase=%s cgit=%d order=0,1,2,3",
            'std::printf(" alpha=%08" PRIx32',
            'std::printf(" beta=%08" PRIx32',
        ):
            self.assertIn(token, self.helper)
        self.assertNotIn("address=", self.helper)
        self.assertNotIn("ordinal=", self.helper)
        self.assertNotIn("memory_access", self.helper)

    def test_outer_fp64_pair_has_static_ownership_and_ordered_combine(self):
        for token in (
            "cg_outer_reduction_partials[NUM_CORES][2]",
            "cg_outer_reduction_partials[tid][0] = xz_partial",
            "cg_outer_reduction_partials[tid][1] = zz_partial",
            "xz_total += cg_outer_reduction_partials[reduction_tid][0]",
            "zz_total += cg_outer_reduction_partials[reduction_tid][1]",
            "*xz = xz_total",
            "*zz = zz_total",
        ):
            self.assertIn(token, self.helper)
        outer_start = self.source.index("double norm_temp1_partial = 0.0;")
        outer_end = self.source.index("norm_temp2 = 1.0 / sqrt", outer_start)
        outer = self.source[outer_start:outer_end]
        self.assertIn("#pragma omp for schedule(static) nowait", outer)
        self.assertIn("norm_temp1_partial += x[j] * z[j]", outer)
        self.assertIn("norm_temp2_partial += z[j] * z[j]", outer)
        self.assertIn("cg_deterministic_outer_reduce(", outer)
        self.assertIn("#pragma omp barrier", outer)

    def test_outer_ordinary_build_preserves_openmp_reduction(self):
        outer_start = self.source.index("double norm_temp1_partial = 0.0;")
        outer_end = self.source.index("norm_temp2 = 1.0 / sqrt", outer_start)
        outer = self.source[outer_start:outer_end]
        self.assertIn("#else", outer)
        self.assertIn(
            "#pragma omp for reduction(+ : norm_temp1, norm_temp2)", outer
        )
        self.assertIn("norm_temp1 += x[j] * z[j]", outer)
        self.assertIn("norm_temp2 += z[j] * z[j]", outer)

    def test_outer_evidence_has_all_fp64_partials_results_and_consumers(self):
        for token in (
            "CG_OUTER_REDUCTION_EVIDENCE it=%d order=0,1,2,3",
            "xz0=%016",
            "zz3=%016",
            "xz_result=%016",
            "zz_result=%016",
            "norm_scale=%016",
            "zeta=%016",
        ):
            self.assertIn(token, self.helper)


class CgReductionRunnerContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = RUNNER_PATH.read_text()
        cls.tree = ast.parse(cls.text)
        spec = importlib.util.spec_from_file_location(
            "reduction_runner", RUNNER_PATH
        )
        cls.runner = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.runner)

    def test_frozen_artifacts_and_geometry_are_exact(self):
        require_runner_contract(self.text)
        for token in (
            "606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427",
            "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
            "--maa_num_indirect_units_per_maa=4",
            "--maa_num_tiles_per_core=10",
            "--maa_num_tile_elements=16384",
            "--maa_num_offset_table_epoch_entries=16384",
            "archived gem5 did not resolve the frozen Ramulator",
        ):
            self.assertIn(token, self.text)

    def test_one_guest_checkpoint_and_same_selector_path_feed_both_arms(self):
        self.assertEqual(self.text.count("subprocess.run(compile_args"), 1)
        self.assertEqual(self.text.count("run_logged(checkpoint_args"), 1)
        self.assertIn(
            'selector.write_text(f"token_stream_ld {treatment}\\n")', self.text
        )
        self.assertIn(
            "restore_args(guest, selector, checkpoint, arm, page_fed)",
            self.text,
        )
        self.assertNotIn("CG_PHYSICAL_PAGE_PRODUCT_ONLY", self.text)
        self.assertNotIn("CG_PAGE_FED_SOA_ONLY", self.text)

    def test_no_native_full_timeout_or_access_trace_path(self):
        self.assertIn('"native_runs": 0', self.text)
        self.assertIn('"full_cg": False', self.text)
        self.assertIn('"timeout": "none"', self.text)
        self.assertNotIn("--debug-flags", self.text)
        self.assertNotIn("MAAReorderTrace", self.text)
        self.assertNotIn("MAAVirtualTrace", self.text)
        self.assertNotIn("USE_DATA_FROM_FILE", self.text)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(
                node.func, ast.Attribute
            ):
                if node.func.attr == "run":
                    self.assertNotIn(
                        "timeout", {kw.arg for kw in node.keywords}
                    )

    def test_exact_completion_fingerprint_and_artifact_closure_precede_gate(
        self,
    ):
        for token in (
            "m5_exit instruction encountered",
            'line == "ROI End!!!"',
            "result=PASS",
            "expected_evidence_shape(inner_evidence)",
            "expected_outer_evidence_shape(outer_evidence)",
            "require_terminal",
            "require_stats",
            "if checkpoint_before != checkpoint_after",
            "if after_status != before_status or after_commit != before_commit",
        ):
            self.assertIn(token, self.text)
        self.assertLess(
            self.text.index("if checkpoint_before != checkpoint_after"),
            self.text.index('(out / "gate.complete").write_text'),
        )

    def test_value_cache_reuse_closes_without_one_issue_per_delivery(self):
        values = {
            "simTicks": 1,
            "IND_SoaJitInstructions": 1,
            "IND_SoaJitTerminalCompletions": 1,
            "IND_SoaJitSelected": 16384,
            "IND_SoaJitAliasesApplied": 16384,
            "IND_SoaJitValueReadIssues": 16128,
            "IND_SoaJitValueReadResponses": 16128,
            "IND_SoaJitValueFills": 16128,
            "IND_SoaJitValueCachedResponses": 12,
            "IND_SoaJitValueHits": 256,
            "IND_SoaJitValueMergedWaiters": 0,
            "IND_SoaJitValueDeliveries": 16384,
            "IND_SoaJitAReadIssues": 1,
            "IND_SoaJitAReadResponses": 1,
            "IND_SoaJitAWriteIssues": 1,
            "IND_SoaJitAWriteResponses": 1,
            "IND_SoaJitPageFedOperations": 1,
            "IND_SoaJitPageFedAdmitCommands": 4,
            "IND_SoaJitPageFedCloseCommands": 1,
            "IND_SoaJitEpochDrains": 0,
            "IND_BoundedGlobalMergeFallbacks": 0,
            "STR_PublishIssues": 1024,
            "STR_PublishAccepts": 1024,
            "STR_PublishWriteResponses": 1024,
            "STR_PublishTerminals": 4,
        }
        with tempfile.TemporaryDirectory() as temporary:
            stats = Path(temporary) / "stats.txt"
            stats.write_text(
                "---------- Begin Simulation Statistics ----------\n"
                + "".join(
                    f"system.maa.I0_{name} {value}\n"
                    for name, value in values.items()
                )
                + "---------- End Simulation Statistics   ----------\n"
            )
            self.assertEqual(
                self.runner.require_stats(stats, windows=1, page_fed=True),
                values,
            )
            values["IND_SoaJitValueDeliveries"] -= 1
            stats.write_text(
                "---------- Begin Simulation Statistics ----------\n"
                + "".join(
                    f"system.maa.I0_{name} {value}\n"
                    for name, value in values.items()
                )
                + "---------- End Simulation Statistics   ----------\n"
            )
            with self.assertRaisesRegex(RuntimeError, "stats closure failed"):
                self.runner.require_stats(stats, windows=1, page_fed=True)

    def test_artifact_ledger_covers_guest_api_abi_and_config_inputs(self):
        for token in (
            "GUEST_COMPILE_INPUTS",
            "benchmarks/API/MAA.hpp",
            "benchmarks/API/MAA_gem5.hpp",
            "benchmarks/API/MAA_virtual_materialize.hpp",
            "include/gem5/m5ops.h",
            "include/gem5/asm/generic/m5ops.h",
            "include/gem5/maa_logical_spd_cache_abi.hh",
            "include/gem5/maa_page_fed_soa_abi.hh",
            "util/m5/src/abi/x86/m5op.S",
            "RUNNER_CONFIG_INPUTS",
            "configs/common/Benchmarks.py",
            "configs/common/MAAConfig.py",
            "configs/common/MemConfig.py",
            "configs/common/Simulation.py",
            "configs/ruby/Ruby.py",
        ):
            self.assertIn(token, self.text)

    def test_adversarial_removal_of_each_core_guard_fails_contract(self):
        guards = (
            "MAX_DIAGNOSTIC_CG_NA = 32768",
            "-DCG_DETERMINISTIC_REDUCTIONS",
            "--maa_num_initial_row_table_slices=32",
            "checkpoint_files.after",
            "artifact_sha256.after",
            "reduction_partial_and_downstream_bits_exact_equal",
            "gate.complete",
            "IND_SoaJitValueDeliveries",
        )
        require_runner_contract(self.text)
        for guard in guards:
            with self.subTest(guard=guard):
                mutated = self.text.replace(guard, "")
                with self.assertRaises(AssertionError):
                    require_runner_contract(mutated)

    def test_evidence_parser_rejects_reorder_and_downstream_loss(self):
        base = (
            "CG_REDUCTION_EVIDENCE phase={} cgit={} order=0,1,2,3 "
            "p0=00000000 p1=00000000 p2=00000000 p3=00000000 "
            "result=00000000{}"
        )
        lines = [base.format("initial_rho", 0, "")]
        for cgit in range(1, 5):
            lines.append(base.format("d", cgit, " alpha=00000000"))
            lines.append(base.format("rho", cgit, " beta=00000000"))
        lines.append(base.format("final_sum", 0, ""))
        self.runner.expected_evidence_shape(lines)
        with self.assertRaises(RuntimeError):
            self.runner.expected_evidence_shape(
                [lines[1], lines[0], *lines[2:]]
            )
        with self.assertRaises(RuntimeError):
            self.runner.expected_evidence_shape(
                [lines[1].replace(" alpha=00000000", "")]
            )

        outer = (
            "CG_OUTER_REDUCTION_EVIDENCE it=1 order=0,1,2,3 "
            "xz0=0000000000000000 zz0=0000000000000000 "
            "xz1=0000000000000000 zz1=0000000000000000 "
            "xz2=0000000000000000 zz2=0000000000000000 "
            "xz3=0000000000000000 zz3=0000000000000000 "
            "xz_result=0000000000000000 zz_result=0000000000000000 "
            "norm_scale=0000000000000000 zeta=0000000000000000"
        )
        self.runner.expected_outer_evidence_shape([outer])
        with self.assertRaises(RuntimeError):
            self.runner.expected_outer_evidence_shape(
                [outer.replace("order=0,1,2,3", "order=1,0,2,3")]
            )

    def test_report_path_is_unique_and_present(self):
        self.assertTrue(
            REPORT_PATH.name.startswith("cg_page_fed_reduction_order")
        )


if __name__ == "__main__":
    unittest.main()
