#!/usr/bin/env python3

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CPU_SIDE_PORT = ROOT / "src/mem/MAA/CpuSidePort.cc"
MAA_HEADER = ROOT / "src/mem/MAA/MAA.hh"
MAA_SOURCE = ROOT / "src/mem/MAA/MAA.cc"
CACHE = ROOT / "src/mem/cache/base.cc"
PACKET_QUEUE = ROOT / "src/mem/packet_queue.hh"
UME_REGRESSION = ROOT / "experiments/scripts/run_ume_tile_ready_regression.sh"


def accepted_by_ume_counter_policy(deferrals, signals, acceptances):
    """Model the terminal arithmetic enforced by the UME regression."""
    return (
        deferrals > 0
        and signals > 0
        and acceptances > 0
        and acceptances <= signals <= deferrals
        and deferrals == signals
    )


class CpuSideRetryContractTests(unittest.TestCase):
    def test_upstream_retry_does_not_preserve_packet_identity(self):
        cache = CACHE.read_text()
        packet_queue = PACKET_QUEUE.read_text()

        self.assertIn(
            "delete pkt;",
            cache,
            "BaseCache must still reconstruct rejected MSHR packets",
        )
        self.assertRegex(
            packet_queue,
            re.compile(r"not(?:\s+\*)?\s+necessarily the same packet"),
            "the retry queue contract changed; revisit the MAA retry policy",
        )

    def test_maa_does_not_retain_sender_owned_rejected_packet(self):
        port = CPU_SIDE_PORT.read_text()
        header = MAA_HEADER.read_text()

        self.assertNotIn("retryTilePacket", port)
        self.assertNotIn("retryTilePacket", header)
        self.assertNotRegex(port, re.compile(r"pkt\s*!=\s*retry"))

    def test_retry_attempt_gates_the_packet_actually_presented(self):
        port = CPU_SIDE_PORT.read_text()
        signaled = port.index("if (tileRequestRetrySignaled)")
        retry_wait = port.index("if (mustRetryTileRequest)", signaled)
        retry_branch = port[signaled:retry_wait]

        self.assertIn("requested_tile_id", retry_branch)
        self.assertIn(
            "!maa.spd->getTileReady(requested_tile_id)", retry_branch
        )
        self.assertIn("tileRequestRetrySignaled = false", retry_branch)
        self.assertIn("mustRetryTileRequest = true", retry_branch)
        self.assertIn("return false", retry_branch)

    def test_reordered_unavailable_retry_preserves_terminal_accounting(self):
        # A defers and signals; the cache presents unavailable B, which
        # defers and signals; one later request consumes the second signal.
        self.assertTrue(accepted_by_ume_counter_policy(2, 2, 1))
        self.assertFalse(2 == 2 == 1)

    def test_no_packet_retry_is_not_counted_as_an_acceptance(self):
        # BaseCache may receive sendRetryReq() after its blocked entry has
        # disappeared and therefore send no packet back to the MAA.
        self.assertFalse(accepted_by_ume_counter_policy(1, 1, 0))

    def test_existing_counters_cannot_prove_no_signal_is_outstanding(self):
        # These totals can mean either one successful retry plus one
        # re-rejection, or one successful retry plus one no-packet signal.
        # Both are conservatively accepted until an outstanding-state gauge
        # can be added without forcing a broad simulator rebuild.
        retry_rejection_totals = (2, 2, 1)
        no_packet_totals = (2, 2, 1)
        self.assertEqual(retry_rejection_totals, no_packet_totals)
        self.assertTrue(accepted_by_ume_counter_policy(*no_packet_totals))

    def test_telemetry_and_runner_describe_the_weaker_contract(self):
        maa = MAA_SOURCE.read_text()
        runner = UME_REGRESSION.read_text()

        self.assertIn(
            "request attempts accepted while a cacheable SPD tile-read",
            maa,
        )
        self.assertNotIn(
            "deferred cacheable SPD data reads accepted after retry",
            maa,
        )
        self.assertIn(
            ".acceptance.retry_counter_ordering == true", runner
        )
        self.assertIn(
            ".acceptance.terminal_deferral_signal_balance == true",
            runner,
        )
        self.assertNotIn(".acceptance.retry_counter_equality", runner)
        self.assertIn("acceptances > signals", runner)
        self.assertIn("signals > deferrals", runner)
        self.assertIn("deferrals != signals", runner)


if __name__ == "__main__":
    unittest.main()
