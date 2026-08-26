"""Focused contract tests for the narrow CG direct4-product/q16 pair."""

from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
OVERLAP_STATE = (ROOT / "src/mem/MAA/SoaJitOverlapState.hh").read_text()
STREAM_ACCESS = (ROOT / "src/mem/MAA/StreamAccess.cc").read_text()
MAA_SOURCE = (ROOT / "src/mem/MAA/MAA.cc").read_text()
RUNNER_PATH = (
    ROOT / "experiments/scripts/run_cg_direct4_product_page_fed_q16.py"
)
RUNNER_TEXT = RUNNER_PATH.read_text()
SPEC = importlib.util.spec_from_file_location("cg_direct4_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def terminal_fields(treatment: str, windows: int = 3) -> dict[str, str]:
    pages = windows * 4
    words = windows * 16384
    direct = treatment in {
        "direct4_product_page_fed_q16",
        "direct4_product_page_fed_q16_pingpong",
    }
    values = {
        "full_windows": windows,
        "staged_index_words": words,
        "staged_value_words": 0,
        "product_words": words,
        "index_publish_pages": 0,
        "value_publish_pages": 0,
        "product_publish_pages": pages,
        "logical_alu_vectors": 0,
        "physical_alu_vectors": pages,
        "logical_page_windows": 0,
        "physical_page_product_windows": 0,
        "page_fed_product_windows": 0 if direct else windows,
        "direct4_product_page_fed_q16_windows": windows if direct else 0,
        "virtual_p_gather_windows": 0 if direct else windows,
        "physical_p_gather_pages": pages if direct else 0,
        "page_fed_admit_pages": pages,
        "page_fed_closes": windows,
        "q_spmv_eligible_windows": 2,
        "q_spmv_routed_windows": 2,
        "residual_spmv_eligible_windows": 1,
        "residual_spmv_routed_windows": 1,
        "external_coherent_backing_bytes": 262144 if direct else 524288,
        "physical_spd_payload_bytes": 524288,
        "logical_scheduler_reserved_lanes": 0,
        "logical_scheduler_reserved_lane_payload_bytes": 0,
        "host_payload_access": 0,
        "coherent_index_backing_bytes": 0,
        "virtual_p_backing_bytes": 0 if direct else 262144,
        "virtual_backing_traffic_eliminated": 1 if direct else 0,
        "p16_reorder_preserved": 0 if direct else 1,
        "q16_reorder_preserved": 1,
    }
    fields = {key: str(value) for key, value in values.items()}
    fields["p_gather_mode"] = "physical_4k_direct" if direct else "virtual_16k"
    return fields


def test_selector_and_eight_tile_build_are_narrow() -> None:
    assert 'return "direct4_product_page_fed_q16";' in SOURCE
    assert runner.DEFAULT_CG_NA == 1024
    assert runner.MAX_CG_NA == 32768
    assert runner.TREATMENTS == (
        ("control", "page_fed_product_soa_jit"),
        ("direct4_q16", "direct4_product_page_fed_q16"),
    )
    assert runner.SELECTED_TREATMENTS == (
        ("control", "page_fed_product_soa_jit", False),
        ("direct4_q16", "direct4_product_page_fed_q16", True),
    )
    assert runner.PUBLISHER_PINGPONG_TREATMENTS == (
        ("serial", "direct4_product_page_fed_q16", True),
        (
            "pingpong",
            "direct4_product_page_fed_q16_pingpong",
            True,
        ),
    )
    assert '"-DNUM_TILES_PER_CORE=8"' in RUNNER_TEXT
    assert '"-DCG_PHYSICAL_PAGE_PRODUCT_ONLY"' in RUNNER_TEXT
    assert '"-DCG_PAGE_FED_SOA_ONLY"' in RUNNER_TEXT
    assert '"native_runs": 0' in RUNNER_TEXT
    assert '"full_cg_runs": 0' in RUNNER_TEXT
    assert '"timeout": "none"' in RUNNER_TEXT


def test_direct4_pages_are_physical_and_publish_only_final_products() -> None:
    starts = [
        match.start()
        for match in re.finditer(
            r"if \(logical_page_full_window &&\s+"
            r"cg_uses_direct4_product_page_fed_q16\(\)\)",
            SOURCE,
        )
    ]
    assert len(starts) == 2
    for start in starts:
        end = SOURCE.index("maa_range_loop<int>", start)
        direct_page = SOURCE[start:end]
        assert "const int group = alternate_group ? t0 : t4;" in direct_page
        assert "const int index_tile = group;" in direct_page
        assert "const int product_tile = group + 3;" in direct_page
        assert "&colidx[page_base]" in direct_page
        assert "page_min_reg" in direct_page
        assert "page_max_reg" in direct_page
        assert "maa_indirect_load<float>" in direct_page
        assert "&a[page_base]" in direct_page
        assert "maa_alu_vector<float>" in direct_page
        assert "wait_ready(product_tile);" in direct_page
        assert "cg_direct4_publish_product_page" in direct_page
        assert "maa_indirect_load_virtual_index" not in direct_page
        assert "virtual_gather_backing_for_thread" not in direct_page


def test_product_responses_close_before_q16_open_and_ordered_admits() -> None:
    q_start = SOURCE.index(
        "// This arm intentionally gives up p-side 16K reorder:"
    )
    q_end = SOURCE.index(
        "#else\n                maa_const(k_base, r2);", q_start
    )
    q_path = SOURCE[q_start:q_end]
    publish = q_path.index("cg_direct4_publish_product_page")
    open_q16 = q_path.index("cg_page_fed_q16_open", publish)
    admit_loop = q_path.index(
        "for (int page_offset = 0; page_offset < TILE_SIZE", open_q16
    )
    ready = q_path.index("wait_ready(t0);", admit_loop)
    admit = q_path.index("cg_page_fed_admit_q_index_page", admit_loop)
    close = q_path.index("cg_page_fed_q16_close", admit)
    assert publish < open_q16 < admit_loop < ready < admit < close
    assert q_path[open_q16:close].count("wait_ready(t0);") >= 1
    residual_start = SOURCE.index(
        "cg_page_fed_q16_open(tid, curr_r, q_completion)", q_end
    )
    residual_end = SOURCE.index(
        "cg_page_fed_q16_close(tid, q_completion)", residual_start
    )
    residual_q16 = SOURCE[residual_start:residual_end]
    assert (
        residual_q16.index("maa_range_loop<int>")
        < residual_q16.index("wait_ready(t0);")
        < residual_q16.index("cg_page_fed_admit_q_index_page")
    )
    helper = SOURCE[
        SOURCE.index("cg_direct4_publish_product_page(") : SOURCE.index(
            "cg_page_fed_admit_q_index_page("
        )
    ]
    assert "wait_ready(completion_tile);" not in helper


def test_pingpong_source_and_completion_ownership_are_exact() -> None:
    assert (
        "The IF source reference remains held until the\n"
        "    // final WriteResp and therefore forbids source reuse"
        in STREAM_ACCESS
    )
    terminal = MAA_SOURCE.index(
        "if (publisher_completion != -1 && !logical_page_managed)"
    )
    ready = MAA_SOURCE.index("setTileReady(publisher_completion", terminal)
    release = MAA_SOURCE.index(
        "releaseResponseBearingPublishCompletion(", ready
    )
    assert terminal < ready < release

    starts = [
        match.start()
        for match in re.finditer(
            r"if \(logical_page_full_window &&\s+"
            r"cg_uses_direct4_product_page_fed_q16\(\)\)",
            SOURCE,
        )
    ]
    assert len(starts) == 2
    for start in starts:
        end = SOURCE.index("maa_range_loop<int>", start)
        producer = SOURCE[start:end]
        reuse_wait = producer.index("wait_ready(next_group + 2);")
        next_preload = producer.index("&colidx[next_page_base]", reuse_wait)
        issue = producer.index("cg_direct4_publish_product_page(")
        serial_wait = producer.index("if (!pingpong)", issue)
        assert reuse_wait < next_preload < issue < serial_wait
        assert "alternate_group ? r6 : r4" in producer
        assert "alternate_group ? r7 : r5" in producer
        assert "alternate_group ? r3 : r2" in producer
        assert "pingpong ? coefficient_tile : group" in producer

    for destination in ("curr_q", "curr_r"):
        open_text = f"cg_page_fed_q16_open(tid, {destination}, q_completion)"
        open_pos = SOURCE.index(open_text)
        prefix = SOURCE[max(0, open_pos - 900) : open_pos]
        assert prefix.rfind("wait_ready(t6);") < prefix.rfind(
            "wait_ready(t2);"
        )
        assert prefix.rfind("wait_ready(t2);") < prefix.rfind(
            "maa_const<int>(0, r6);"
        )
        assert "? t4" in prefix


def test_terminal_gate_records_the_tradeoff_and_exact_payload() -> None:
    for treatment in (
        "page_fed_product_soa_jit",
        "direct4_product_page_fed_q16",
        "direct4_product_page_fed_q16_pingpong",
    ):
        assert (
            runner.require_terminal_8(
                terminal_fields(treatment), treatment, 1024
            )
            == 3
        )
    bad = terminal_fields("direct4_product_page_fed_q16")
    bad["physical_spd_payload_bytes"] = "655360"
    with unittest.TestCase().assertRaisesRegex(
        RuntimeError, "terminal closure failed"
    ):
        runner.require_terminal_8(bad, "direct4_product_page_fed_q16", 1024)


def test_cg_na_default_and_explicit_4096_are_selected() -> None:
    default = runner.parse_args(["/tmp/cg-default"])
    explicit = runner.parse_args(["/tmp/cg-medium", "--cg-na", "4096"])
    assert default.cg_na == 1024
    assert explicit.cg_na == 4096
    cache_pair = runner.parse_args(
        ["/tmp/cg-cache", "--cg-na", "256", "--value-cache-pair"]
    )
    assert cache_pair.cg_na == 256
    assert cache_pair.value_cache_pair
    pingpong_pair = runner.parse_args(
        ["/tmp/cg-pingpong", "--cg-na", "4096", "--publisher-pingpong-pair"]
    )
    assert pingpong_pair.publisher_pingpong_pair

    captured: dict[str, object] = {}
    original_parse_arm = runner.base.parse_arm
    try:

        def capture(
            arm: Path, cg_na: int, treatment: str, page_fed: bool
        ) -> dict[str, object]:
            captured.update(
                arm=arm,
                cg_na=cg_na,
                treatment=treatment,
                page_fed=page_fed,
            )
            return {}

        runner.base.parse_arm = capture
        assert (
            runner.parse_arm(Path("arm"), 4096, "direct4_product_page_fed_q16")
            == {}
        )
        assert captured == {
            "arm": Path("arm"),
            "cg_na": 4096,
            "treatment": "direct4_product_page_fed_q16",
            "page_fed": True,
        }
        assert (
            runner.base.require_terminal(
                terminal_fields("direct4_product_page_fed_q16"),
                "direct4_product_page_fed_q16",
            )
            == 3
        )
    finally:
        runner.base.parse_arm = original_parse_arm


def test_cg_na_rejects_out_of_range_and_full_sizes() -> None:
    for forbidden in ("0", "32769", "150000"):
        with unittest.TestCase().assertRaises(SystemExit):
            runner.parse_args(["/tmp/cg-forbidden", "--cg-na", forbidden])
    with unittest.TestCase().assertRaisesRegex(
        RuntimeError, "forbidden CG_NA"
    ):
        runner.require_terminal_8(
            terminal_fields("direct4_product_page_fed_q16"),
            "direct4_product_page_fed_q16",
            150000,
        )
    with unittest.TestCase().assertRaises(SystemExit):
        runner.parse_args(
            [
                "/tmp/cg-too-large",
                "--cg-na",
                "4097",
                "--publisher-pingpong-pair",
            ]
        )


def test_fingerprint_parser_has_no_hidden_1024_size() -> None:
    assert re.search(r"^CG_NA\s*=\s*1024$", RUNNER_TEXT, re.M) is None
    assert "elements=1024" not in RUNNER_TEXT
    assert 'f"-DCG_NA={cg_na}"' in RUNNER_TEXT
    assert "base.parse_arm(arm, cg_na, treatment, True)" in RUNNER_TEXT
    assert '"selected_cg_na": cg_na' in RUNNER_TEXT


def test_config_gate_rejects_inherited_ten_tiles() -> None:
    common = [
        "page_fed_soa_jit=true",
        "num_maas=1",
        "num_indirect_units_per_maa=4",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "soa_jit_predicate_active_credits=16",
        "soa_jit_active_value_owners=32",
        "[system.mem_ctrls0]",
        "[system.mem_ctrls1]",
    ]
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.ini"
        config.write_text("\n".join(common + ["num_tiles_per_core=8"]) + "\n")
        runner.require_config_8(config, True)
        config.write_text("\n".join(common + ["num_tiles_per_core=10"]) + "\n")
        with unittest.TestCase().assertRaisesRegex(
            RuntimeError, "exactly one num_tiles_per_core=8"
        ):
            runner.require_config_8(config, True)


def test_hardened_delivery_gate_uses_eight_tiles() -> None:
    args = runner.restore_args(
        Path("guest"), Path("selector"), Path("cpt"), Path("arm")
    )
    assert args.count("--maa_num_tiles_per_core=8") == 1
    assert "--maa_num_tiles_per_core=10" not in args
    assert runner.HARDENED_REQUIRE_STATS is not runner.require_stats_8
    base_text = runner.BASE_PATH.read_text()
    assert 'values["IND_SoaJitValueDeliveries"] == words' in base_text
    assert 'values["IND_SoaJitValueReadIssues"]' in base_text
    assert '+ values["IND_SoaJitValueHits"]' in base_text
    assert '+ values["IND_SoaJitValueMergedWaiters"]' in base_text


def test_value_cache_pair_is_one_bounded_general_treatment() -> None:
    assert runner.VALUE_CACHE_TREATMENTS == (
        ("cache_off", "direct4_product_page_fed_q16", False),
        ("cache_on", "direct4_product_page_fed_q16", True),
    )
    off = runner.restore_args(
        Path("guest"), Path("selector"), Path("cpt"), Path("arm"), False
    )
    on = runner.restore_args(
        Path("guest"), Path("selector"), Path("cpt"), Path("arm"), True
    )
    option = "--maa_soa_jit_value_cache_enable"
    assert option not in off
    assert on.count(option) == 1
    assert [value for value in on if value != option] == off

    assert runner.FIXED_VALUE_OWNER_LINES == 128
    assert runner.ACTIVE_VALUE_OWNER_LINES == 32
    assert runner.VALUE_OWNER_LINE_BYTES == 64
    assert runner.INDIRECT_UNITS_PER_MAA == 4
    assert "static constexpr size_t CacheLines = MaxOwners;" in OVERLAP_STATE
    assert "static constexpr size_t MaxOwners = 128;" in OVERLAP_STATE
    assert "if (!cacheEnabled && line.waiterMask.none())" in OVERLAP_STATE
    assert "bool clearGeneration(uint64_t generation)" in OVERLAP_STATE
    assert '"new_payload_bytes": 0' in RUNNER_TEXT
    assert '"new_control_bytes": 0' in RUNNER_TEXT
    assert '"new_ports": 0' in RUNNER_TEXT
    assert '"physical_spd_payload_bytes": 524288' in RUNNER_TEXT


def test_value_cache_config_gate_and_normalization_fail_closed() -> None:
    common = [
        "page_fed_soa_jit=true",
        "num_maas=1",
        "num_indirect_units_per_maa=4",
        "num_tiles_per_core=8",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "soa_jit_predicate_active_credits=16",
        "soa_jit_active_value_owners=32",
        "[system.mem_ctrls0]",
        "[system.mem_ctrls1]",
    ]
    with tempfile.TemporaryDirectory() as directory:
        off = Path(directory) / "off.ini"
        on = Path(directory) / "on.ini"
        off.write_text(
            "\n".join(
                common
                + [
                    "soa_jit_value_cache_enable=false",
                    "host_paths=/tmp/cache_off/fs/proc",
                ]
            )
            + "\n"
        )
        on.write_text(
            "\n".join(
                common
                + [
                    "soa_jit_value_cache_enable=true",
                    "host_paths=/tmp/cache_on/fs/proc",
                ]
            )
            + "\n"
        )
        try:
            runner._expected_value_cache = False
            runner.require_config_8(off, True)
            with unittest.TestCase().assertRaisesRegex(
                RuntimeError, "value_cache_enable=false"
            ):
                runner.require_config_8(on, True)
            runner._expected_value_cache = True
            runner.require_config_8(on, True)
        finally:
            runner._expected_value_cache = None
        assert runner.normalized_cache_pair_config(off) == (
            runner.normalized_cache_pair_config(on)
        )
        on.write_text(on.read_text() + "soa_jit_apply_lanes=2\n")
        assert runner.normalized_cache_pair_config(off) != (
            runner.normalized_cache_pair_config(on)
        )


def test_value_cache_classification_requires_traffic_and_ticks() -> None:
    conserved = {
        "IND_SoaJitSelected": 16384,
        "IND_SoaJitValueDeliveries": 16384,
        "IND_SoaJitAReadIssues": 16,
        "IND_SoaJitAWriteIssues": 16,
        "STR_PublishIssues": 1024,
    }
    control = {
        "stats": {
            **conserved,
            "IND_SoaJitValueReadIssues": 16384,
            "IND_SoaJitValueHits": 0,
            "system.maa.port_cache_RD_packets": 17000,
            "simTicks": 1000,
        }
    }
    candidate = {
        "stats": {
            **conserved,
            "IND_SoaJitValueReadIssues": 1024,
            "IND_SoaJitValueHits": 15360,
            "system.maa.port_cache_RD_packets": 1640,
            "simTicks": 800,
        }
    }
    assert runner.classify_value_cache_pair(control, candidate) == (
        "ACCEPT_TRAFFIC_AND_PERFORMANCE"
    )
    candidate["stats"]["simTicks"] = 1001
    assert runner.classify_value_cache_pair(control, candidate) == (
        "REJECT_NO_MATCHED_BENEFIT"
    )
    candidate["stats"]["IND_SoaJitSelected"] -= 1
    with unittest.TestCase().assertRaisesRegex(
        RuntimeError, "changed conserved work"
    ):
        runner.classify_value_cache_pair(control, candidate)


def test_publisher_pingpong_classification_is_fail_closed() -> None:
    conserved = {
        "IND_SoaJitInstructions": 3,
        "IND_SoaJitTerminalCompletions": 3,
        "IND_SoaJitSelected": 49152,
        "IND_SoaJitAliasesApplied": 49152,
        "IND_SoaJitValueDeliveries": 49152,
        "IND_SoaJitAReadIssues": 30,
        "IND_SoaJitAReadResponses": 30,
        "IND_SoaJitAWriteIssues": 30,
        "IND_SoaJitAWriteResponses": 30,
        "IND_SoaJitPageFedOperations": 3,
        "IND_SoaJitPageFedAdmitCommands": 12,
        "IND_SoaJitPageFedCloseCommands": 3,
        "IND_SoaJitPageFedCommandResponses": 15,
        "IND_SoaJitPageFedAdmittedWords": 49152,
        "IND_SoaJitPageFedSpdIndexReads": 49152,
        "IND_SoaJitPageFedRowWrites": 49152,
        "IND_SoaJitPageFedCoherentIndexReadLines": 0,
        "IND_SoaJitPageFedCoherentIndexWriteLines": 0,
        "IND_SoaJitPageFedStateByteOperations": 48,
        "IND_SoaJitEpochDrains": 0,
        "IND_BoundedGlobalMergeFallbacks": 0,
        "STR_PublishIssues": 3072,
        "STR_PublishAccepts": 3072,
        "STR_PublishWriteResponses": 3072,
        "STR_PublishTerminals": 12,
        "STR_PublishRetries": 0,
    }
    serial = {
        "stats": {
            **conserved,
            "STR_PublishOverlapIssues": 0,
            "simTicks": 1000,
        }
    }
    pingpong = {
        "stats": {
            **conserved,
            "STR_PublishOverlapIssues": 10,
            "simTicks": 900,
        }
    }
    assert runner.classify_publisher_pingpong_pair(serial, pingpong) == (
        "ACCEPT_OVERLAP_AND_PERFORMANCE"
    )
    pingpong["stats"]["STR_PublishOverlapIssues"] = 0
    assert runner.classify_publisher_pingpong_pair(serial, pingpong) == (
        "REJECT_NO_MATCHED_BENEFIT"
    )
    pingpong["stats"]["STR_PublishOverlapIssues"] = 10
    pingpong["stats"]["simTicks"] = 1000
    assert runner.classify_publisher_pingpong_pair(serial, pingpong) == (
        "REJECT_NO_MATCHED_BENEFIT"
    )
    pingpong["stats"]["simTicks"] = 900
    pingpong["stats"]["STR_PublishTerminals"] -= 1
    with unittest.TestCase().assertRaisesRegex(
        RuntimeError, "changed conserved work"
    ):
        runner.classify_publisher_pingpong_pair(serial, pingpong)


def test_known_nozero_publisher_stats_decode_absence_as_zero() -> None:
    with tempfile.TemporaryDirectory() as directory:
        stats = Path(directory) / "stats.txt"
        stats.write_text(
            "---------- Begin Simulation Statistics ----------\n"
            "system.maa.S0_STR_PublishRetries 3\n"
            "---------- End Simulation Statistics   ----------\n"
        )
        assert (
            runner.optional_nozero_stat_sum(stats, "STR_PublishRetries") == 3
        )
        assert (
            runner.optional_nozero_stat_sum(stats, "STR_PublishOverlapIssues")
            == 0
        )


def test_frozen_artifacts_and_correctness_before_performance() -> None:
    assert runner.base.GEM5_SHA256 == (
        "606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427"
    )
    assert runner.base.RAMULATOR_SHA256 == (
        "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
    )
    mismatch_gate = RUNNER_TEXT.index(
        "correctness mismatch; simTicks comparison forbidden"
    )
    ticks = RUNNER_TEXT.index('control_ticks = control["stats"]["simTicks"]')
    assert mismatch_gate < ticks
    assert '"deterministic_reduction_records": 11' in RUNNER_TEXT
    assert '"native_runs": 0' in RUNNER_TEXT
    assert '"full_cg_runs": 0' in RUNNER_TEXT
    assert (
        "classify_publisher_pingpong_pair(control, candidate)" in RUNNER_TEXT
    )


def load_tests(loader, tests, pattern):  # type: ignore[no-untyped-def]
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value))
    return suite


if __name__ == "__main__":
    unittest.main()
