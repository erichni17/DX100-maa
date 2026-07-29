#!/usr/bin/env python3
"""Fail-closed tests for the native SPARTA batch ingestion boundary."""

import copy
import importlib.util
import json
import pathlib
import struct
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
RUNNER_PATH = HERE / "run_sparta_tally_cpu_smoke.py"
SPEC = importlib.util.spec_from_file_location(
    "sparta_tally_runner", RUNNER_PATH
)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def bits(value):
    return struct.pack(">d", value).hex()


def valid_document():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    items = []
    for item in range(64):
        items.append(
            {
                "particle_index": (item * 13 + 7) % 64,
                "particle_id": 1000 + item,
                "cell": item // 32,
                "contribution_bits": [bits(value) for value in values],
            }
        )
    tally_bits = [bits(value * 32) for value in values]
    return {
        "schema": RUNNER.NATIVE_BATCH_SCHEMA,
        "source_revision": RUNNER.NATIVE_BATCH_SOURCE_REVISION,
        "rank": 0,
        "timestep": 0,
        "native_particle_count": 64,
        "eligible_particle_count": 64,
        "item_count": 64,
        "cell_count": 2,
        "target_mixture_group": 0,
        "max_abs_tally_error": 0,
        "max_rel_tally_error": 0,
        "application_tally_matches_batch": True,
        "items": items,
        "nonzero_cell_tallies": [
            {
                "cell": cell,
                "batch_bits": list(tally_bits),
                "sparta_bits": list(tally_bits),
            }
            for cell in range(2)
        ],
    }


class NativeBatchTest(unittest.TestCase):
    def load(self, document):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "batch.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return RUNNER.load_native_batch(path)

    def test_valid_batch_and_header(self):
        batch = self.load(valid_document())
        self.assertEqual(batch["cell_count"], 2)
        self.assertEqual(len(batch["indices"]), 64)
        self.assertEqual(len(batch["contribution_bits"]), 384)
        self.assertEqual(len(batch["particle_cells"]), 64)
        self.assertEqual(len(batch["particle_contribution_bits"]), 384)
        self.assertEqual(batch["cell_counts"], [32, 32])
        self.assertEqual(len(batch["cell_first"]), 2)
        self.assertEqual(len(batch["particle_next"]), 64)
        self.assertEqual(len(batch["expected_bits"]), 12)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "batch.h"
            RUNNER.write_native_header(path, batch)
            text = path.read_text(encoding="utf-8")
            self.assertIn("sparta_native_indices[64]", text)
            self.assertIn("sparta_native_contribution_bits[384]", text)
            self.assertIn("sparta_native_particle_cells[64]", text)
            self.assertIn(
                "sparta_native_particle_contribution_bits[384]", text
            )
            self.assertIn("sparta_native_cell_first[2]", text)
            self.assertIn("sparta_native_cell_count[2]", text)
            self.assertIn("sparta_native_particle_next[64]", text)
            self.assertIn("sparta_native_expected_bits[12]", text)

    def test_native_membership_reconstructs_export_order(self):
        batch = self.load(valid_document())
        staged_particles = []
        for cell in range(batch["cell_count"]):
            particle = batch["cell_first"][cell]
            visited = 0
            while particle >= 0:
                self.assertEqual(batch["particle_cells"][particle], cell)
                staged_particles.append(particle)
                particle = batch["particle_next"][particle]
                visited += 1
            self.assertEqual(visited, batch["cell_counts"][cell])
        expected = [(item * 13 + 7) % 64 for item in range(64)]
        self.assertEqual(staged_particles, expected)

    def test_rejects_unsorted_cells(self):
        document = valid_document()
        document["items"][0]["cell"] = 1
        with self.assertRaisesRegex(ValueError, "not sorted"):
            self.load(document)

    def test_rejects_nonfinite_contribution(self):
        document = valid_document()
        document["items"][0]["contribution_bits"][1] = "7ff8000000000000"
        with self.assertRaisesRegex(ValueError, "finite"):
            self.load(document)

    def test_rejects_tally_mismatch(self):
        document = valid_document()
        document["nonzero_cell_tallies"][0]["sparta_bits"][0] = bits(31.0)
        with self.assertRaisesRegex(ValueError, "recomputation"):
            self.load(document)

    def test_rejects_revision_and_shape_drift(self):
        revision = valid_document()
        revision["source_revision"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "revision"):
            self.load(revision)
        shape = copy.deepcopy(valid_document())
        shape["unreviewed"] = True
        with self.assertRaisesRegex(ValueError, "top-level shape"):
            self.load(shape)

    def test_accepts_nonzero_timestep_and_rejects_negative(self):
        document = valid_document()
        document["timestep"] = 64
        self.assertEqual(self.load(document)["timestep"], 64)
        document["timestep"] = -1
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            self.load(document)

    def test_reads_bounded_tally_diagnostics(self):
        line = (
            "LANL_MAA_TALLY_MISMATCH element=0x00000008 "
            "observed=0x3ff0000000000001 expected=0x3ff0000000000000\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stderr.log"
            path.write_text("gem5 warning\n" + line, encoding="utf-8")
            result = RUNNER.read_tally_diagnostics(path)
        self.assertEqual(result["bit_mismatch_count"], 1)
        self.assertEqual(result["mismatch_elements"], [8])
        self.assertEqual(result["max_ulp_distance"], 1)
        self.assertLess(
            result["max_relative_error"], RUNNER.NATIVE_RELATIVE_TOLERANCE
        )

    def test_rejects_changed_zero_tally(self):
        line = (
            "LANL_MAA_TALLY_MISMATCH element=0x00000000 "
            "observed=0x0000000000000001 expected=0x0000000000000000\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stderr.log"
            path.write_text(line, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exact zero"):
                RUNNER.read_tally_diagnostics(path)


if __name__ == "__main__":
    unittest.main()
