#!/usr/bin/env python3
"""Fail-closed tests for raw native SPARTA fused-cell ingestion."""

import copy
import importlib.util
import pathlib
import struct
import unittest

HERE = pathlib.Path(__file__).resolve().parent
RUNNER_PATH = HERE / "run_sparta_fused_cell_native_batch.py"
SPEC = importlib.util.spec_from_file_location(
    "sparta_fused_native", RUNNER_PATH
)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def bits(value):
    return struct.pack(">d", value).hex()


def contribution(mass, velocity):
    vx, vy, vz = velocity
    return [
        1.0,
        mass,
        mass * vx,
        mass * vy,
        mass * vz,
        mass * ((vx * vx + vy * vy) + vz * vz),
    ]


def valid_document():
    mass = 2.0
    particles = [
        (0, (1.0, 0.0, 0.0)),
        (1, (0.0, 1.0, 0.0)),
        (0, (-1.0, 0.0, 1.0)),
        (1, (0.0, -1.0, 1.0)),
    ]
    order = [0, 2, 1, 3]
    items = []
    tallies = [[0.0] * RUNNER.CHANNELS for _ in range(2)]
    for index in order:
        cell, velocity = particles[index]
        values = contribution(mass, velocity)
        items.append(
            {
                "particle_index": index,
                "particle_id": 100 + index,
                "cell": cell,
                "contribution_bits": list(map(bits, values)),
            }
        )
        for channel, value in enumerate(values):
            tallies[cell][channel] += value
    return {
        "schema": "sparta-lanl-maa-thermal-grid-batch-v1",
        "source_revision": "c" * 40,
        "rank": 0,
        "timestep": 56,
        "native_particle_count": 4,
        "eligible_particle_count": 4,
        "item_count": 4,
        "cell_count": 2,
        "target_mixture_group": 0,
        "max_abs_tally_error": 0.0,
        "max_rel_tally_error": 0.0,
        "application_tally_matches_batch": True,
        "native_record_extension": {
            "schema": "sparta-cpu-native-record-v1",
            "abi_sha256": RUNNER.ABI_SHA256,
            "group_bit": 1,
            "one_part_bytes": 104,
            "species_bytes": 192,
            "child_info_bytes": 64,
            "cells": [
                {"cell": 0, "count": 2, "first": 0, "mask": 1},
                {"cell": 1, "count": 2, "first": 1, "mask": 1},
            ],
            "next": [2, 3, -1, -1],
            "species": [{"species": 0, "group": 0, "mass_bits": bits(mass)}],
            "particles": [
                {
                    "particle_index": index,
                    "particle_id": 100 + index,
                    "species": 0,
                    "cell": cell,
                    "velocity_bits": list(map(bits, velocity)),
                }
                for index, (cell, velocity) in enumerate(particles)
            ],
        },
        "items": items,
        "nonzero_cell_tallies": [
            {
                "cell": cell,
                "batch_bits": list(map(bits, tally)),
                "sparta_bits": list(map(bits, tally)),
            }
            for cell, tally in enumerate(tallies)
        ],
    }


class SpartaFusedNativeBatchTest(unittest.TestCase):
    def test_valid_native_document_and_binary_image(self):
        document = valid_document()
        validated = RUNNER.validate_batch(document)
        self.assertEqual(validated["selected"], [0, 2, 1, 3])
        image = RUNNER.encode_image(document, validated)
        self.assertTrue(image.startswith(b"LMAANR1\0"))
        self.assertGreater(len(image), 8)

    def test_rejects_abi_and_layout_drift(self):
        document = valid_document()
        document["native_record_extension"]["abi_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "ABI"):
            RUNNER.validate_batch(document)
        document = valid_document()
        document["native_record_extension"]["one_part_bytes"] = 128
        with self.assertRaisesRegex(ValueError, "layout"):
            RUNNER.validate_batch(document)

    def test_rejects_duplicate_wrong_cell_and_late_terminal(self):
        duplicate = valid_document()
        duplicate["native_record_extension"]["next"][0] = 0
        with self.assertRaisesRegex(ValueError, "duplicate"):
            RUNNER.validate_batch(duplicate)
        wrong_cell = valid_document()
        wrong_cell["native_record_extension"]["particles"][0]["cell"] = 1
        with self.assertRaisesRegex(ValueError, "wrong cell"):
            RUNNER.validate_batch(wrong_cell)
        late = valid_document()
        late["native_record_extension"]["next"][2] = 3
        with self.assertRaisesRegex(ValueError, "late terminal"):
            RUNNER.validate_batch(late)

    def test_rejects_field_and_contribution_drift(self):
        document = valid_document()
        document["native_record_extension"]["particles"][0]["velocity_bits"][
            0
        ] = bits(2.0)
        with self.assertRaisesRegex(ValueError, "contribution bits"):
            RUNNER.validate_batch(document)
        document = valid_document()
        document["items"][0]["contribution_bits"][0] = bits(2.0)
        with self.assertRaisesRegex(ValueError, "contribution bits"):
            RUNNER.validate_batch(document)

    def test_rejects_nonfinite_native_fields(self):
        document = copy.deepcopy(valid_document())
        document["native_record_extension"]["particles"][0]["velocity_bits"][
            0
        ] = "7ff8000000000000"
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            RUNNER.validate_batch(document)


if __name__ == "__main__":
    unittest.main()
