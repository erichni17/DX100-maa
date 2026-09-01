import json
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "benchmarks/gapbs/src/sssp.cc"
ADMISSION = ROOT / "benchmarks/gapbs/src/sssp_chunk_admission.hh"
MAKEFILE = ROOT / "benchmarks/gapbs/Makefile"
PREDICTOR = ROOT / "experiments/tools/predict_sssp_chunk_admission.cc"
RUNNER = (
    ROOT / "experiments/scripts/run_sssp_conflict_tolerant_snapshot_small.sh"
)
S22 = ROOT / (
    "experiments/analysis/"
    "sssp_conflict_tolerant_snapshot_predictor_s22_2026-09-01.json"
)


class SsspConflictTolerantSnapshotContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()
        cls.admission = ADMISSION.read_text()
        cls.makefile = MAKEFILE.read_text()
        cls.predictor = PREDICTOR.read_text()
        cls.runner = RUNNER.read_text()

    def test_default_is_off_and_dedicated_target_is_exact(self):
        legacy_target = self.makefile[
            self.makefile.index(
                "sssp_maa_2G_old_result_hybrid_fp:"
            ) : self.makefile.index("sssp_maa_2G_conflict_snapshot_fp:")
        ]
        prototype_target = self.makefile[
            self.makefile.index(
                "sssp_maa_2G_conflict_snapshot_fp:"
            ) : self.makefile.index(
                "# Scale-22 BC",
                self.makefile.index("sssp_maa_2G_conflict_snapshot_fp:"),
            )
        ]
        self.assertNotIn("SSSP_CONFLICT_TOLERANT_SNAPSHOT", legacy_target)
        self.assertIn("-DSSSP_CONFLICT_TOLERANT_SNAPSHOT=1", prototype_target)
        self.assertIn("-DSSSP_OLD_RESULT_HYBRID=1", prototype_target)
        self.assertIn("requires old-result hybrid", self.source)

    def test_snapshot_precedes_admission_and_explicit_team_barrier(self):
        snapshot = self.source.index("hybrid_source_snapshot[pos] =")
        single = self.source.rfind("#pragma omp single nowait", 0, snapshot)
        active = self.source.index(
            "fill(hybrid_active_sources.begin()", snapshot
        )
        first_destination_min = self.source.index(
            "RunSsspHybridWindow(\n                                        tid",
            active,
        )
        self.assertLess(single, snapshot)
        self.assertLess(snapshot, active)
        self.assertLess(active, first_destination_min)
        barrier = self.source.index("#pragma omp barrier", active)
        self.assertIn("nowait", self.source[single:snapshot])
        self.assertLess(active, barrier)
        self.assertLess(barrier, first_destination_min)
        self.assertIn(
            "atomic_thread_fence(memory_order_seq_cst);",
            self.source[snapshot:active],
        )

    def test_every_maa_source_consumer_uses_occurrence_snapshot(self):
        maa_path = self.source[
            self.source.index("const int cft =") : self.source.index(
                "if (curr_bin_index < local_bins.size()"
            )
        ]
        self.assertIn(
            "maa_stream_load<WeightT>(\n"
            "                        hybrid_source_snapshot.data()",
            maa_path,
        )
        self.assertIn("hybrid_source_snapshot.data() + idx", maa_path)
        tail = self.source[
            self.source.index("RunSsspCoherentTail(") : self.source.index(
                "RunSsspHybridWindow("
            )
        ]
        self.assertIn("source_snapshot[cursor_pos]", tail)
        self.assertIn("ConsumeCursorWords(", tail)
        admission_scan = self.source[
            self.source.index(
                "hybrid_snapshot_iteration ="
            ) : self.source.index("if ((int)curr_frontier_tail <")
        ]
        self.assertGreaterEqual(
            admission_scan.count("hybrid_source_snapshot[pos]"), 3
        )

    def test_hazards_are_observed_but_only_bounds_rejects_snapshot(self):
        self.assertIn("reasons[owner] |= ActiveSource", self.admission)
        self.assertEqual(self.admission.count("|= CrossOwner"), 2)
        policy = self.admission[
            self.admission.index(
                "safeForConflictTolerantSnapshot"
            ) : self.admission.index("bool hasReason")
        ]
        self.assertIn("reasons[owner] & Bounds", policy)
        self.assertNotIn("ActiveSource", policy)
        self.assertNotIn("CrossOwner", policy)
        for field in (
            "tolerated_hazard_windows",
            "active_source_observed_windows",
            "cross_owner_observed_windows",
            "active_source_tolerated_windows",
            "cross_owner_tolerated_windows",
        ):
            self.assertIn(field, self.source)

    def test_order_completion_critical_and_storage_disclosures_survive(self):
        routed = self.source[
            self.source.index(
                "#pragma omp critical",
                self.source.index("maa_range_loop<SGOffset>"),
            ) : self.source.index("} while (curr_size > 0);")
        ]
        self.assertIn("RunSsspHybridWindow(", routed)
        helper = self.source[
            self.source.index("RunSsspHybridWindow(") : self.source.index(
                "#endif", self.source.index("RunSsspHybridWindow(")
            )
        ]
        self.assertIn("wait_ready(completion_tile);", helper)
        self.assertIn("for (size_t lane = end; lane-- > begin;)", helper)
        self.assertIn("for (size_t lane = begin; lane < end; ++lane)", helper)
        for disclosure in (
            "source_snapshot_words=",
            "source_snapshot_bytes=",
            "external_snapshot_capacity_bytes=",
            "snapshot_backing=ordinary_coherent_external",
            "snapshot_hidden_sram_bytes=0",
            "snapshot_lifetime_closure=1",
            "hidden_result_payload_bytes=0",
        ):
            self.assertIn(disclosure, self.source)

    def test_predictor_and_candidate_only_runner_encode_exact_policy(self):
        self.assertIn('"conflict-tolerant-snapshot"', self.predictor)
        self.assertIn("source_snapshot[pos]", self.predictor)
        self.assertIn("safeForConflictTolerantSnapshot", self.predictor)
        for text in (
            "all_safe|active_source|cross_owner",
            "-DSSSP_CONFLICT_TOLERANT_SNAPSHOT=1",
            "-Wall -Wextra -Werror",
            "--policy conflict-tolerant-snapshot",
            "native_arms=0",
            "wall_timeout=none",
            "full_graph=false",
            "expected_graph_sha=",
            "expected_fingerprint=",
            "SSSP_CONFLICT_TOLERANT_SNAPSHOT_SMALL_PASS",
        ):
            self.assertIn(text, self.runner)
        self.assertNotIn("timeout ", self.runner)

    def test_material_s22_prediction_precedes_any_full_launch(self):
        result = json.loads(S22.read_text())
        totals = result["totals"]
        self.assertEqual(result["schema"], 3)
        self.assertEqual(result["policy"], "conflict-tolerant-snapshot")
        self.assertEqual(result["source"], 2_796_003)
        self.assertEqual(result["directed_edges"], 134_217_158)
        self.assertEqual(totals["eligible_windows"], 7_232)
        self.assertEqual(totals["routed_windows"], 7_232)
        self.assertEqual(totals["unsafe_eligible_windows"], 0)
        self.assertEqual(totals["tolerated_hazard_windows"], 7_232)
        self.assertEqual(totals["source_snapshot_words"], 12_608_932)
        self.assertEqual(totals["snapshot_hidden_sram_bytes"], 0)
        self.assertTrue(totals["counts_close"])

    def test_guest_compiles_with_werror(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "sssp_snapshot_guest"
            subprocess.run(
                [
                    "g++",
                    "-Ibenchmarks/API",
                    "-Iinclude",
                    "-Iutil/m5/src",
                    "-std=c++11",
                    "-O3",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-Wno-ignored-qualifiers",
                    "-Wno-unused-parameter",
                    "-fopenmp",
                    "-DGEM5",
                    "-DMAA",
                    "-DNUM_CORES=4",
                    "-DNUM_TILES_PER_CORE=8",
                    "-DTILE_SIZE=16384",
                    "-DMAA_CONSUMER_TILE_SIZE=4096",
                    "-DMAA_MEM_SIZE=0x80000000",
                    "-DSSSP_FP_ENABLE=1",
                    "-DSSSP_OLD_RESULT_HYBRID=1",
                    "-DSSSP_CONFLICT_TOLERANT_SNAPSHOT=1",
                    "util/m5/src/abi/x86/m5op.S",
                    str(SOURCE),
                    "-o",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
            )


if __name__ == "__main__":
    unittest.main()
