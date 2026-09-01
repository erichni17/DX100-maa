import pathlib
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class SsspConflictSnapshotContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "benchmarks/gapbs/src/sssp.cc").read_text()
        cls.admission = (
            ROOT / "benchmarks/gapbs/src/sssp_chunk_admission.hh"
        ).read_text()
        cls.makefile = (ROOT / "benchmarks/gapbs/Makefile").read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_sssp_old_result_hybrid_small.sh"
        ).read_text()

    def test_default_off_compile_time_target_is_isolated(self):
        target = "sssp_maa_2G_conflict_snapshot_fp:"
        self.assertEqual(self.makefile.count(target), 1)
        prototype = self.makefile[self.makefile.index(target) :]
        self.assertIn("-DSSSP_CONFLICT_SNAPSHOT_PROTOTYPE=1", prototype)
        legacy_start = self.makefile.index("sssp_maa_2G_old_result_hybrid_fp:")
        legacy_end = self.makefile.index(target)
        self.assertNotIn(
            "SSSP_CONFLICT_SNAPSHOT_PROTOTYPE",
            self.makefile[legacy_start:legacy_end],
        )
        self.assertIn(
            "SSSP conflict snapshot prototype requires old-result hybrid",
            self.source,
        )

    def test_snapshot_copy_precedes_explicit_barrier_and_any_maa_min(self):
        copy = self.source.index(
            "hybrid_source_snapshot[pos] = source_distance"
        )
        barrier = self.source.index("#pragma omp barrier", copy)
        active_stream = self.source.index(
            "hybrid_source_snapshot.data(), reg0, reg1, regOne", barrier
        )
        candidate_load = self.source.index(
            "hybrid_source_snapshot.data() + idx", active_stream
        )
        window_call = self.source.index("RunSsspHybridWindow(", candidate_load)
        self.assertLess(copy, barrier)
        self.assertLess(barrier, active_stream)
        self.assertLess(active_stream, candidate_load)
        self.assertLess(candidate_load, window_call)

    def test_every_prototype_source_consumer_uses_occurrence_snapshot(self):
        self.assertIn(
            "const WeightT source_distance = source_snapshot[cursor_pos]",
            self.source,
        )
        self.assertIn(
            "static_cast<int64_t>(source_distance) + wn.w", self.source
        )
        self.assertIn(
            "const WeightT source_distance =\n"
            "                        hybrid_source_snapshot[pos]",
            self.source,
        )
        self.assertIn(
            "hybrid_source_snapshot.data() + idx, tilei", self.source
        )
        self.assertIn("PublishAndConsumeSsspFallbackPage(", self.source)
        self.assertIn("RunSsspCoherentTail(", self.source)

    def test_only_bounds_rejects_snapshot_tolerant_routing(self):
        self.assertIn("bool snapshotSafe", self.admission)
        self.assertIn("(reasons[owner] & Bounds) == 0", self.admission)
        self.assertIn("hybrid_chunk_admission.snapshotSafe(", self.source)
        self.assertIn("has_active_source", self.source)
        self.assertIn("has_cross_owner", self.source)
        self.assertIn("active_source_tolerated_windows", self.source)
        self.assertIn("cross_owner_tolerated_windows", self.source)

        program = textwrap.dedent(
            """
            #include <cstdint>
            #include "sssp_chunk_admission.hh"
            int main() {
                using Tracker = sssp_chunk_admission::Tracker;
                Tracker tracker;
                std::uint32_t epoch = 0;
                std::uint32_t owner = 0;
                if (!tracker.reset(2)) return 1;
                if (!tracker.observeDestination(0, true, 1, epoch, owner))
                    return 2;
                if (tracker.safe(0) || !tracker.snapshotSafe(0)) return 3;
                if (!tracker.reject(0, Tracker::Bounds)) return 4;
                if (tracker.snapshotSafe(0)) return 5;
                return 0;
            }
            """
        )
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "tracker_test.cc"
            binary = pathlib.Path(temp) / "tracker_test"
            source.write_text(program)
            subprocess.run(
                [
                    "g++",
                    "-std=c++11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(ROOT / "benchmarks/gapbs/src"),
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            subprocess.run([str(binary)], check=True)

    def test_order_completion_and_external_storage_are_disclosed(self):
        window_call = self.source.index(
            "RunSsspHybridWindow(",
            self.source.index("hybrid_source_snapshot.data() + idx"),
        )
        critical = self.source.rfind("#pragma omp critical", 0, window_call)
        old_result = self.source.index(
            "maa_indirect_rmw_vector_soa_jit_old_result("
        )
        completion = self.source.index(
            "wait_ready(completion_tile);", old_result
        )
        reconstruction = self.source.index(
            "for (size_t page = 0; page < 4", completion
        )
        self.assertGreaterEqual(critical, 0)
        self.assertLess(critical, window_call)
        self.assertLess(old_result, completion)
        self.assertLess(completion, reconstruction)
        for field in (
            "source_snapshot_span=coherent_external",
            "source_snapshot_storage_words=",
            "source_snapshot_storage_bytes=",
            "source_snapshot_copied_words=",
            "source_snapshot_copied_bytes=",
            "source_snapshot_barriers=",
            "hidden_source_snapshot_bytes=0",
            '<< " new_dedicated_payload_bytes="',
            "duplicate_order=legacy_physical_pages",
            "response_closure=",
        ):
            self.assertIn(field, self.source)

    def test_small_gate_is_candidate_only_exact_and_timeout_free(self):
        self.assertIn("SSSP_CONFLICT_SNAPSHOT_PROTOTYPE", self.runner)
        self.assertIn(
            "expected_treatment=conflict_snapshot_prototype", self.runner
        )
        self.assertIn("expected_routed=4", self.runner)
        self.assertIn("expected_active_tolerated", self.runner)
        self.assertIn("expected_cross_tolerated", self.runner)
        self.assertIn(
            "dedicated_payload_bytes == snapshot_storage_bytes", self.runner
        )
        self.assertIn("hash_a=24951adf631ff822", self.runner)
        self.assertIn("hash_b=005c7757503cab01", self.runner)
        self.assertIn("native_arms=0", self.runner)
        self.assertIn("wall_timeout=none", self.runner)
        self.assertNotIn("timeout ", self.runner)
        oracle_guard = "if [[ $variant != all_safe && $prototype != 1 ]]"
        self.assertGreaterEqual(self.runner.count(oracle_guard), 4)


if __name__ == "__main__":
    unittest.main()
