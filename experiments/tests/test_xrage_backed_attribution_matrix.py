#!/usr/bin/env python3

import configparser
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = (
    ROOT / "experiments/scripts/run_xrage_backed_attribution_matrix.py"
)
ANALYZER_PATH = (
    ROOT / "experiments/analysis/analyze_xrage_backed_attribution_matrix.py"
)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("xrage_backed_runner", RUNNER_PATH)
analyzer = load("xrage_backed_analyzer", ANALYZER_PATH)


class XrageBackedAttributionMatrixTest(unittest.TestCase):
    def test_matrix_is_exactly_four_arms_and_backed_delta_is_physical(self):
        self.assertEqual(
            [arm["name"] for arm in runner.ARMS],
            ["native16", "native4", "backed16", "backed4"],
        )
        backed16, backed4 = runner.ARMS[-2:]
        differing = {key for key in backed16 if backed16[key] != backed4[key]}
        self.assertEqual(differing, {"name", "physical"})
        self.assertEqual(backed16["checkpoint_group"], "backed")
        self.assertEqual(backed16["guest_arm"], "backedx3")

    def test_backed_restore_commands_normalize_to_physical_only(self):
        common = dict(
            gem5=Path("/frozen/gem5.opt"),
            config=Path("/frozen/configs/deprecated/example/se.py"),
            ramulator=Path("/frozen/ramulator.yaml"),
            checkpoint=Path("/frozen/checkpoints/backed/gem5"),
            binary=Path("/frozen/spatter_maa_xrage_runtime_verify_16K"),
            guest_options="-f /frozen/xrage.json --maa-arm backedx3",
            logical=16384,
        )
        command16 = runner.restore_command(
            outdir=Path("/out/backed16"), physical=16384, **common
        )
        command4 = runner.restore_command(
            outdir=Path("/out/backed4"), physical=4096, **common
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for name, command in (("cap16", command16), ("cap4", command4)):
                path = root / f"{name}.json"
                path.write_text(json.dumps(command), encoding="utf-8")
                paths.append(path)
            normalized16, physical16 = analyzer.normalized_backed_command(
                paths[0]
            )
            normalized4, physical4 = analyzer.normalized_backed_command(
                paths[1]
            )
        self.assertEqual(normalized16, normalized4)
        self.assertEqual((physical16, physical4), (16384, 4096))
        self.assertIn("--maa_direct_retirement_line_handoff", command16)
        self.assertIn("--maa_transparent_spd_mode=0", command16)
        self.assertNotIn("--maa_transparent_spd_mode=3", command16)

    def test_guest_path_is_direct_index_materialize_alu_stream_store(self):
        source = (
            ROOT / "benchmarks/spatter/src/Spatter/Configuration.cc"
        ).read_text(encoding="utf-8")
        backed = source.split('maa_arm == "backedx3"', 1)[1].split(
            'maa_arm == "direct4fusedprefetch"', 1
        )[0]
        ordered = (
            "maa_indirect_load_virtual_index<double>",
            "maa_stream_load_virtual_page<double>",
            "maa_alu_scalar<double>",
            "maa_stream_store<double>",
        )
        positions = [backed.index(marker) for marker in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("maa_virtual_tile_alu_scalar_store", backed)
        self.assertIn("reg5, reg6, reg7", backed)
        main = (ROOT / "benchmarks/spatter/src/main.cc").read_text(
            encoding="utf-8"
        )
        self.assertIn('cl.maa_arm != "backedx3"', main)

    def test_build_records_both_guest_geometries(self):
        script = (
            ROOT / "experiments/scripts/build_xrage_runtime_attribution.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("spatter_maa_xrage_runtime_verify_16K", script)
        self.assertIn("spatter_maa_xrage_runtime_verify_4K", script)
        self.assertIn('"${targets[@]/#/$build/}"', script)

    def test_exact_payload_capacity_accounting(self):
        parser = configparser.RawConfigParser()
        parser.read_dict(
            {
                "system.maa": {
                    "num_cores": "4",
                    "num_tiles_per_core": "8",
                    "physical_tile_elements": "16384",
                    "num_maas": "1",
                    "num_indirect_units_per_maa": "1",
                    "virtual_index_buffer_lines": "128",
                    "virtual_response_word_pool": "1024",
                    "virtual_combine_words": "4096",
                }
            }
        )
        cap16 = analyzer.hardware_capacity(parser["system.maa"], True)
        parser["system.maa"]["physical_tile_elements"] = "4096"
        cap4 = analyzer.hardware_capacity(parser["system.maa"], True)
        self.assertEqual(cap16["physical_spd_payload_bytes"], 2 * 1024 * 1024)
        self.assertEqual(cap4["physical_spd_payload_bytes"], 512 * 1024)
        self.assertEqual(cap16["direct_index_feeder_bytes"], 8 * 1024)
        self.assertEqual(cap16["source_response_pool_bytes"], 8 * 1024)
        self.assertEqual(cap16["destination_combiner_bytes"], 32 * 1024)
        self.assertEqual(cap16["materializer_line_buffer_bytes"], 4 * 1024)
        self.assertEqual(
            cap16["active_payload_capacity_bytes"]
            - cap4["active_payload_capacity_bytes"],
            1536 * 1024,
        )

    def test_hardware_report_boundary_rejects_area_overclaim(self):
        boundary = analyzer.HARDWARE_REPORT_BOUNDARY
        self.assertIn(
            "payload-capacity subtotal only",
            boundary["active_payload_capacity_bytes_semantics"],
        )
        self.assertTrue(
            boundary[
                "backed_pair_retains_identical_logical16_row_offset_metadata"
            ]
        )
        self.assertEqual(
            boundary["excluded_from_active_payload_capacity_bytes"],
            [
                "descriptor, header, and readiness bits",
                "nonpayload tags and control beyond separately emitted materializer controls",
                "ports, arbitration, and wiring",
                "SRAM periphery",
                "synthesized area, power, and timing",
            ],
        )
        self.assertIn(
            "not total DX100 hardware cost",
            boundary["prohibited_interpretation"],
        )
        source = ANALYZER_PATH.read_text(encoding="utf-8")
        self.assertIn("active payload-capacity subtotal", source)
        self.assertIn("retain identical logical16", source)
        self.assertIn("is not total DX100 hardware cost", source)

    def test_request_digest_and_direct_index_trace_are_fail_closed(self):
        digest_lines = "\n".join(
            f"0: global: unit=0 instruction_tick={tick} count={count} "
            f"fnv=0x{count:016x} mix=0x{count + 1:016x}"
            for tick, count in ((10, 2), (20, 1))
        )
        issue_lines = "\n".join(
            "0: global: unit=0 instruction_tick=10 sequence="
            f"{sequence} addr=0x{0x1000 + sequence * 64:x} "
            "bounded=1 virtual=1 direct_index=1"
            for sequence in range(2)
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mechanism.log"
            path.write_text(
                digest_lines + "\n" + issue_lines + "\n", encoding="utf-8"
            )
            digests = analyzer.digest_records(path)
            issue = analyzer.issue_trace_summary(path)
        self.assertEqual(len(digests[0]), 2)
        self.assertEqual(issue["instructions"], 1)
        self.assertEqual(issue["direct_index_requests"], 2)
        self.assertEqual(issue["non_direct_index_requests"], 0)

    def test_materializer_trace_exposes_static_capacity(self):
        line = (
            "event=page_materialization_submit schema=1 "
            "line_buffer_bytes=4096 control_bytes=12345 "
            "direct_stage_control_bytes=192 page_spd_bytes=131072 "
            "charged_two_page_spd_bytes=262144\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mechanism.log"
            path.write_text(line * 2, encoding="utf-8")
            capacity = analyzer.materializer_capacity(path)
        self.assertEqual(capacity["submit_records"], 2)
        self.assertEqual(capacity["line_buffer_bytes"], 4096)
        self.assertEqual(capacity["control_bytes"], 12345)


if __name__ == "__main__":
    unittest.main()
