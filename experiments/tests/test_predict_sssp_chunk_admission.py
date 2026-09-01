import array
import json
import pathlib
import struct
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "experiments/tools/predict_sssp_chunk_admission.cc"


def write_directed_fixture(path: pathlib.Path, variant: str) -> None:
    nodes = 69_633
    outgoing = [[] for _ in range(nodes)]
    for vertex in range(1, 4_097):
        outgoing[0].append((vertex, 1))
    for vertex in range(1, 4_097):
        base = 4_097 + (vertex - 1) * 16
        for lane in range(16):
            destination = base + lane
            if (
                variant in ("active_source", "overlap")
                and vertex == 1_025
                and lane == 0
            ):
                destination = 1
            elif lane == 0 and (
                (variant == "cross_owner" and vertex in (1_025, 2_049))
                or (variant == "overlap" and vertex == 2_049)
            ):
                destination = 1 if variant == "overlap" else 20_481
            outgoing[vertex].append((destination, 1))

    incoming = [[] for _ in range(nodes)]
    for source, neighbors in enumerate(outgoing):
        for destination, weight in neighbors:
            incoming[destination].append((source, weight))

    def csr(adjacency):
        offsets = array.array("i", [0])
        neighbors = array.array("i")
        for entries in adjacency:
            for vertex, weight in entries:
                neighbors.extend((vertex, weight))
            offsets.append(len(neighbors) // 2)
        return offsets.tobytes(), neighbors.tobytes()

    out_offsets, out_neighbors = csr(outgoing)
    in_offsets, in_neighbors = csr(incoming)
    edges = len(out_neighbors) // 8
    with path.open("wb") as graph:
        graph.write(struct.pack("<?ii", True, edges, nodes))
        graph.write(out_offsets)
        graph.write(out_neighbors)
        graph.write(in_offsets)
        graph.write(in_neighbors)


class SsspChunkAdmissionPredictorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory()
        cls.temp = pathlib.Path(cls.workspace.name)
        cls.binary = cls.temp / "predict_sssp_chunk_admission"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-O3",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(SOURCE),
                "-o",
                str(cls.binary),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def predict(self, variant, policy="reject-hazards"):
        graph = self.temp / f"{variant}.wsg"
        if not graph.exists():
            write_directed_fixture(graph, variant)
        completed = subprocess.run(
            [
                str(self.binary),
                "--input",
                str(graph),
                "--source",
                "0",
                "--delta",
                "1",
                "--threads",
                "4",
                "--admission-policy",
                policy,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_all_safe_routes_exactly_four_of_four(self):
        result = self.predict("all_safe")
        self.assertEqual(result["schema"], 3)
        self.assertEqual(result["tool_version"], "3")
        self.assertEqual(result["admission_policy"], "reject-hazards")
        self.assertEqual(result["totals"]["eligible_windows"], 4)
        self.assertEqual(result["totals"]["routed_windows"], 4)
        self.assertEqual(result["totals"]["unsafe_eligible_windows"], 0)
        self.assertEqual(result["totals"]["reason_covered_unsafe_windows"], 0)
        self.assertTrue(result["totals"]["counts_close"])
        self.assertEqual(result["totals"]["active_source_rejected_windows"], 0)
        self.assertEqual(result["totals"]["cross_owner_rejected_windows"], 0)

    def test_snapshot_policy_tolerates_active_source(self):
        result = self.predict("active_source", "snapshot-tolerant")
        totals = result["totals"]
        self.assertEqual(result["admission_policy"], "snapshot-tolerant")
        self.assertEqual(totals["eligible_windows"], 4)
        self.assertEqual(totals["routed_windows"], 4)
        self.assertEqual(totals["unsafe_eligible_windows"], 0)
        self.assertEqual(totals["active_source_observed_windows"], 1)
        self.assertEqual(totals["active_source_tolerated_windows"], 1)
        self.assertEqual(totals["active_source_rejected_windows"], 0)
        self.assertEqual(totals["cross_owner_observed_windows"], 0)

    def test_snapshot_policy_tolerates_cross_owner(self):
        result = self.predict("cross_owner", "snapshot-tolerant")
        totals = result["totals"]
        self.assertEqual(totals["eligible_windows"], 4)
        self.assertEqual(totals["routed_windows"], 4)
        self.assertEqual(totals["unsafe_eligible_windows"], 0)
        self.assertEqual(totals["cross_owner_observed_windows"], 2)
        self.assertEqual(totals["cross_owner_tolerated_windows"], 2)
        self.assertEqual(totals["cross_owner_rejected_windows"], 0)
        self.assertEqual(totals["active_source_observed_windows"], 0)

    def test_active_source_routes_exactly_three_of_four(self):
        result = self.predict("active_source")
        self.assertEqual(result["totals"]["eligible_windows"], 4)
        self.assertEqual(result["totals"]["routed_windows"], 3)
        self.assertEqual(result["totals"]["unsafe_eligible_windows"], 1)
        self.assertEqual(result["totals"]["reason_covered_unsafe_windows"], 1)
        self.assertTrue(result["totals"]["counts_close"])
        self.assertEqual(result["totals"]["active_source_rejected_windows"], 1)
        self.assertEqual(result["totals"]["cross_owner_rejected_windows"], 0)

    def test_cross_owner_routes_exactly_two_of_four(self):
        result = self.predict("cross_owner")
        self.assertEqual(result["totals"]["eligible_windows"], 4)
        self.assertEqual(result["totals"]["routed_windows"], 2)
        self.assertEqual(result["totals"]["unsafe_eligible_windows"], 2)
        self.assertEqual(result["totals"]["reason_covered_unsafe_windows"], 2)
        self.assertTrue(result["totals"]["counts_close"])
        self.assertEqual(result["totals"]["active_source_rejected_windows"], 0)
        self.assertEqual(result["totals"]["cross_owner_rejected_windows"], 2)

    def test_reason_coverage_is_exact_without_summing_overlapping_reasons(
        self,
    ):
        result = self.predict("overlap")
        totals = result["totals"]
        self.assertEqual(totals["unsafe_eligible_windows"], 2)
        self.assertEqual(totals["active_source_rejected_windows"], 2)
        self.assertEqual(totals["cross_owner_rejected_windows"], 2)
        self.assertEqual(
            totals["reason_covered_unsafe_windows"],
            totals["unsafe_eligible_windows"],
        )
        self.assertLess(
            totals["reason_covered_unsafe_windows"],
            totals["active_source_rejected_windows"]
            + totals["cross_owner_rejected_windows"],
        )
        self.assertTrue(
            all(row["counts_close"] for row in result["iterations"])
        )

    def test_repeated_prediction_is_deterministic(self):
        first = self.predict("all_safe")
        second = self.predict("all_safe")
        first.pop("elapsed_seconds")
        second.pop("elapsed_seconds")
        self.assertEqual(first, second)

    def test_rejects_truncated_serialized_graph(self):
        graph = self.temp / "truncated.wsg"
        graph.write_bytes(struct.pack("<?ii", False, 1, 2))
        completed = subprocess.run(
            [str(self.binary), "--input", str(graph), "--source", "0"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("size mismatch", completed.stderr)


if __name__ == "__main__":
    unittest.main()
