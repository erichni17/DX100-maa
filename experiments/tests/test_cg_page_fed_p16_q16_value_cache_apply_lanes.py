"""Static adversarial contract for bounded page-fed p16/q16 lane A/B."""

from __future__ import annotations

import importlib.util
import inspect
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = (
    ROOT
    / "experiments/scripts/run_cg_page_fed_p16_q16_value_cache_apply_lanes.py"
)
TEXT = PATH.read_text(encoding="utf-8")
SPEC = importlib.util.spec_from_file_location("page_fed_lanes", PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_only_bounded_sizes_and_conditional_confirmation() -> None:
    assert runner.SCREEN_NA == 256 and runner.CONFIRM_NA == 1024
    assert runner.LANES == (1, 4)
    with unittest.TestCase().assertRaises(SystemExit):
        runner.parse_args(["/tmp/out", "--cg-na", "150000"])
    with unittest.TestCase().assertRaises(SystemExit):
        runner.parse_args(["/tmp/out", "--cg-na", "1024"])


def test_one_guest_checkpoint_and_two_exact_lane_commands() -> None:
    source = inspect.getsource(runner.run)
    assert source.count("base.base.run_logged(cp_args") == 1
    assert source.count("base.base.run_logged(command") == 1
    assert "for lanes in LANES" in source
    for lanes in runner.LANES:
        command = runner.restore_command(
            Path("guest"),
            Path("selector"),
            Path("checkpoint"),
            Path("arm"),
            lanes,
        )
        assert command.count("--maa_soa_jit_value_cache_enable") == 1
        assert command.count("--maa_soa_jit_active_value_owners=32") == 1
        assert command.count(f"--maa_soa_jit_apply_lanes={lanes}") == 1


def test_preserves_page_fed_both_reorders_and_all_fixed_backing() -> None:
    assert runner.TREATMENT == "page_fed_product_soa_jit"
    for token in (
        '"p16_reorder_preserved": True',
        '"q16_reorder_preserved": True',
        '"physical_spd_payload_bytes": 524288',
        '"external_coherent_backing_bytes": 524288',
        '"virtual_p_backing_bytes": 262144',
        '"product_backing_bytes": 262144',
        '"coherent_q_index_backing_bytes": 0',
        '"host_payload_access": 0',
        '"new_payload_bytes": 0',
        '"new_control_bytes": 0',
        '"new_ports": 0',
    ):
        assert token in TEXT


def test_exact_lane_four_high_water_and_conserved_ledgers_fail_closed() -> (
    None
):
    source = inspect.getsource(runner.parse_arm)
    assert 'ApplyLaneHighWater"] == instructions * lanes' in source
    assert 'CONSERVE_STATS + ("IND_SoaJitContextStalls",)' in source
    for name in (
        "IND_SoaJitValueDeliveries",
        "IND_SoaJitAReadIssues",
        "IND_SoaJitPageFedAdmittedWords",
        "STR_PublishIssues",
        "IND_SoaJitValueReadIssues",
        "system.maa.port_cache_RD_packets",
    ):
        assert name in runner.CONSERVE_STATS
    assert "IND_SoaJitContextStalls" not in runner.CONSERVE_STATS
    assert '"context_stalls"' in TEXT
    assert "fingerprint_line" in TEXT and "reduction_evidence" in TEXT
    assert 'len(arm["reduction_evidence"]) == 11' in TEXT


def test_direct4_is_reconciliation_not_p_stage_attribution() -> None:
    assert "no p-stage timing attribution" in TEXT
    assert '"p16_reorder_preserved": False' in inspect.getsource(
        runner.validate_direct4_reconciliation
    )
    assert "direct4_product_page_fed_q16" in TEXT


def load_tests(loader, tests, pattern):  # type: ignore[no-untyped-def]
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value))
    return suite
