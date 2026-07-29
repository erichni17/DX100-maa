#!/usr/bin/env python3
"""Validate one raw native SPARTA batch through the fused-cell C++ model."""

import argparse
import hashlib
import json
import math
import pathlib
import struct
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = HERE / "sparta_fused_cell_native_batch_test.cc"
MODEL = ROOT / "src/mem/LANLMAA/SpartaFusedCellModel.hh"
CHANNELS = 6
ABI_SHA256 = "a34d45451975837189db2e6de81630684a863cb740d06c69008f7800bfc5acc7"


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bits_to_float(bits):
    return struct.unpack(">d", bytes.fromhex(bits))[0]


def float_to_bits(value):
    return struct.pack(">d", value).hex()


def validate_batch(batch):
    if batch.get("schema") != "sparta-lanl-maa-thermal-grid-batch-v1":
        raise ValueError("unsupported SPARTA batch schema")
    extension = batch.get("native_record_extension")
    if (
        not extension
        or extension.get("schema") != "sparta-cpu-native-record-v1"
    ):
        raise ValueError("missing native-record extension")
    if extension.get("abi_sha256") != ABI_SHA256:
        raise ValueError("native-record ABI mismatch")
    if (
        extension.get("one_part_bytes") != 104
        or extension.get("species_bytes") != 192
        or extension.get("child_info_bytes") != 64
    ):
        raise ValueError("native-record layout mismatch")

    particle_count = batch.get("native_particle_count")
    cell_count = batch.get("cell_count")
    cells = extension.get("cells", [])
    particles = extension.get("particles", [])
    next_indices = extension.get("next", [])
    species = extension.get("species", [])
    if not (1 <= particle_count <= 64 and 1 <= cell_count <= 64):
        raise ValueError("batch exceeds fused-cell geometry")
    if len(cells) != cell_count or len(particles) != particle_count:
        raise ValueError("native-record extent mismatch")
    if len(next_indices) != particle_count or not species:
        raise ValueError("native next/species extent mismatch")
    if [cell["cell"] for cell in cells] != list(range(cell_count)):
        raise ValueError("native cells are not canonical")
    if [item["particle_index"] for item in particles] != list(
        range(particle_count)
    ):
        raise ValueError("native particles are not canonical")
    if [item["species"] for item in species] != list(range(len(species))):
        raise ValueError("native species are not canonical")

    visited = set()
    list_order = []
    for cell in cells:
        count = cell["count"]
        index = cell["first"]
        if count < 0 or count > particle_count:
            raise ValueError("invalid native cell count")
        if count == 0 and index != -1:
            raise ValueError("empty native cell has a head")
        for _ in range(count):
            if index < 0 or index >= particle_count or index in visited:
                raise ValueError("invalid/duplicate native particle link")
            record = particles[index]
            if record["cell"] != cell["cell"]:
                raise ValueError("native particle is in the wrong cell list")
            visited.add(index)
            list_order.append(index)
            index = next_indices[index]
        if index != -1:
            raise ValueError("native cell list has a late terminal")
    if visited != set(range(particle_count)):
        raise ValueError("native cell lists do not cover every particle")

    target_group = batch["target_mixture_group"]
    group_bit = extension["group_bit"]
    if group_bit <= 0 or target_group < 0:
        raise ValueError("invalid native group selector")
    selected = []
    contributions = {}
    for index in list_order:
        particle = particles[index]
        species_index = particle["species"]
        if species_index < 0 or species_index >= len(species):
            raise ValueError("native particle species is out of range")
        species_record = species[species_index]
        if species_record["group"] != target_group:
            continue
        if not (cells[particle["cell"]]["mask"] & group_bit):
            continue
        mass = bits_to_float(species_record["mass_bits"])
        velocity = [bits_to_float(bits) for bits in particle["velocity_bits"]]
        if not math.isfinite(mass) or not all(map(math.isfinite, velocity)):
            raise ValueError("native particle field is nonfinite")
        vx, vy, vz = velocity
        values = [
            1.0,
            mass,
            mass * vx,
            mass * vy,
            mass * vz,
            mass * ((vx * vx + vy * vy) + vz * vz),
        ]
        if not all(map(math.isfinite, values)):
            raise ValueError("native contribution is nonfinite")
        selected.append(index)
        contributions[index] = [float_to_bits(value) for value in values]

    items = batch.get("items", [])
    if [item["particle_index"] for item in items] != selected:
        raise ValueError("native eligibility/list order disagrees with items")
    for item in items:
        if item["contribution_bits"] != contributions[item["particle_index"]]:
            raise ValueError(
                "native fields do not reproduce contribution bits"
            )

    expected = [["0000000000000000"] * CHANNELS for _ in range(cell_count)]
    for cell in batch.get("nonzero_cell_tallies", []):
        if cell["batch_bits"] != cell["sparta_bits"]:
            batch_values = list(map(bits_to_float, cell["batch_bits"]))
            sparta_values = list(map(bits_to_float, cell["sparta_bits"]))
            for reference, actual in zip(batch_values, sparta_values):
                scale = max(abs(reference), 1.0)
                if abs(actual - reference) / scale > 1.0e-12:
                    raise ValueError("native batch exceeds SPARTA tolerance")
        expected[cell["cell"]] = cell["batch_bits"]
    if len(items) != batch.get("eligible_particle_count"):
        raise ValueError("eligible-particle count mismatch")
    return {
        "extension": extension,
        "cells": cells,
        "particles": particles,
        "next": next_indices,
        "species": species,
        "expected": expected,
        "selected": selected,
    }


def encode_image(batch, validated):
    extension = validated["extension"]
    image = bytearray(b"LMAANR1\0")
    image.extend(
        struct.pack(
            "<IIIIiI",
            batch["cell_count"],
            batch["native_particle_count"],
            len(validated["species"]),
            extension["group_bit"],
            batch["target_mixture_group"],
            CHANNELS,
        )
    )
    for cell in validated["cells"]:
        image.extend(
            struct.pack("<iiI", cell["count"], cell["first"], cell["mask"])
        )
    for index in validated["next"]:
        image.extend(struct.pack("<i", index))
    for species in validated["species"]:
        image.extend(
            struct.pack("<iQ", species["group"], int(species["mass_bits"], 16))
        )
    for particle in validated["particles"]:
        image.extend(struct.pack("<ii", particle["species"], particle["cell"]))
        for bits in particle["velocity_bits"]:
            image.extend(struct.pack("<Q", int(bits, 16)))
    for cell in validated["expected"]:
        for bits in cell:
            image.extend(struct.pack("<Q", int(bits, 16)))
    return bytes(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=pathlib.Path)
    parser.add_argument("--legacy-batch", required=True, type=pathlib.Path)
    parser.add_argument("--outdir", required=True, type=pathlib.Path)
    arguments = parser.parse_args()
    batch_path = arguments.batch.resolve(strict=True)
    legacy_path = arguments.legacy_batch.resolve(strict=True)
    outdir = arguments.outdir.resolve()
    if outdir.exists():
        raise RuntimeError(f"refusing to reuse output directory: {outdir}")
    outdir.mkdir(parents=True)
    report_path = outdir / "report.json"
    report = {
        "schema": "lanl-maa-sparta-fused-cell-native-batch-v1",
        "status": "running",
        "batch_path": str(batch_path),
        "batch_sha256": file_sha256(batch_path),
        "legacy_batch_path": str(legacy_path),
        "legacy_batch_sha256": file_sha256(legacy_path),
        "source_sha256": file_sha256(SOURCE),
        "model_sha256": file_sha256(MODEL),
        "commands": [],
        "claim_boundary": (
            "Simulator-independent exact native-record reference validation "
            "only; no gem5 timing, live descriptor, physical cost, or speedup."
        ),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    try:
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        stripped = dict(batch)
        stripped.pop("native_record_extension", None)
        if stripped != legacy:
            raise RuntimeError(
                "native extension changed the legacy batch object"
            )
        validated = validate_batch(batch)
        image_path = outdir / "native_batch.bin"
        image_path.write_bytes(encode_image(batch, validated))
        binary_path = outdir / "sparta_fused_cell_native_batch_test"
        compile_command = [
            "g++",
            "-O2",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(ROOT / "src"),
            str(SOURCE),
            "-o",
            str(binary_path),
        ]
        run_command = [str(binary_path), str(image_path)]
        report["commands"] = [compile_command, run_command]
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        subprocess.run(compile_command, check=True)
        completed = subprocess.run(
            run_command, check=True, capture_output=True, text=True
        )
        metrics = json.loads(completed.stdout)
        if metrics["particle_visits"] != batch["native_particle_count"]:
            raise RuntimeError("native model particle visits did not close")
        if metrics["eligible_particles"] != len(validated["selected"]):
            raise RuntimeError("native model eligible count did not close")
        if metrics["write_acknowledgements"] != metrics["coherent_writes"]:
            raise RuntimeError(
                "native model write acknowledgements did not close"
            )
        report.update(
            {
                "status": "validated",
                "native_record_schema": validated["extension"]["schema"],
                "native_record_abi_sha256": ABI_SHA256,
                "legacy_object_exact": True,
                "native_fields_reproduce_contribution_bits": True,
                "native_membership_exact_cover": True,
                "model_outputs_bit_exact_to_batch": True,
                "image_sha256": file_sha256(image_path),
                "binary_sha256": file_sha256(binary_path),
                "metrics": metrics,
            }
        )
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print("LANLMAA SPARTA fused-cell native batch: PASS")
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = str(error)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        raise


if __name__ == "__main__":
    main()
