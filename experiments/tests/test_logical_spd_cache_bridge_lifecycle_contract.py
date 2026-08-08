#!/usr/bin/env python3
"""Source contract for the deliberately bridge-local lifecycle slice."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_HH = ROOT / "src/mem/MAA/LogicalSPDCacheGem5Bridge.hh"
BRIDGE_CC = ROOT / "src/mem/MAA/LogicalSPDCacheGem5Bridge.cc"
HOST_TEST = ROOT / "tests/maa/logical_spd_cache_bridge_lifecycle_test.cc"
RUNNER = (
    ROOT / "experiments/scripts/run_logical_spd_cache_bridge_lifecycle_unit.sh"
)


class LogicalSpdBridgeLifecycleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hh = BRIDGE_HH.read_text()
        cls.cc = BRIDGE_CC.read_text()
        cls.host = HOST_TEST.read_text()
        cls.runner = RUNNER.read_text()
        cls.bridge = cls.hh + cls.cc

    def test_admission_is_live_and_native_drain_boundary_is_explicit(
        self,
    ) -> None:
        self.assertIn(
            "bool admissionClosed() const { return admissionsClosed; }",
            self.hh,
        )
        self.assertIn("void closeAdmission()", self.hh)
        self.assertIn("void reopenAdmission()", self.hh)
        self.assertIn(
            "bool nativeDrainIntegrated() const { return false; }", self.hh
        )
        self.assertIn("CHECK(!bridge.admissionClosed())", self.host)
        self.assertIn("bridge.closeAdmission()", self.host)
        self.assertIn("bridge.reopenAdmission()", self.host)
        self.assertIn("CHECK(!bridge.nativeDrainIntegrated())", self.host)
        for required in (
            "registerSource(",
            "admit(",
            "sendPrepared(",
            "recvReqRetry(",
            "receive(",
            "driveCompute(",
            "completeOperation(",
        ):
            self.assertIn(required, self.bridge)
        for forbidden in (
            "sendTimingReq",
            "recvTimingReq",
            "PacketPtr",
            "AddrRange",
        ):
            self.assertNotIn(forbidden, self.bridge)

        for evidence in (
            "checkLiveAdmissionFillComputeDirtyWritebackAndReset",
            "Transport::Status::SendRefused",
            "Transport::Status::Invalid",
            "Slice::Pages * Transport::LinesPerPage",
            "bridge.completeOperation(completed)",
        ):
            self.assertIn(evidence, self.host)

    def test_one_runtime_and_finite_owner_per_maa(self) -> None:
        self.assertIn(
            "std::vector<std::unique_ptr<LogicalSPDCacheRuntime>> runtimes",
            self.hh,
        )
        self.assertIn("CallbackToken owner{}", self.hh)
        self.assertNotIn("std::map", self.bridge)
        self.assertNotIn("std::unordered_map", self.bridge)
        self.assertIn("runtimeIdentity", self.hh)
        self.assertIn("generation", self.hh)
        self.assertIn("nextCallbackIdentity", self.hh)
        self.assertIn("IncarnationSource", self.hh)
        self.assertIn("std::atomic<uint64_t> next", self.hh)

    def test_lifecycle_guards_are_present(self) -> None:
        for evidence in (
            "quiescent(std::size_t maaId) const",
            "requestAbort(std::size_t maaId)",
            "progressAbort(std::size_t maaId)",
            "reset(std::size_t maaId)",
            "teardown(std::size_t maaId)",
            "destructionSafe(std::size_t maaId) const",
            "dirtyFlushPending(std::size_t maaId) const",
        ):
            self.assertIn(evidence, self.hh)
        self.assertIn("if (!quiescent(maaId))", self.cc)
        self.assertIn("if (!authority.sealed()", self.cc)
        self.assertIn("!authority.destructionSafe()", self.cc)

    def test_exact_callback_and_fail_closed_mapping(self) -> None:
        for evidence in (
            "token.generation != state.owner.generation",
            "token.runtimeIdentity != state.runtimeIdentity",
            "token.runtimeIdentity != state.owner.runtimeIdentity",
            "token.identity != state.owner.identity",
            "Runtime::Slice::Status::ProductionStop",
            "Runtime::Slice::Status::Poisoned",
            "return failClosed(maaId)",
        ):
            self.assertIn(evidence, self.cc)
        self.assertIn("wrongGeneration", self.host)
        self.assertIn("wrongRuntime", self.host)
        self.assertIn("wrongIdentity", self.host)
        self.assertIn("acknowledgeCallback(dirty.token)", self.host)
        self.assertIn("correlationSnapshot().abortFlush", self.host)
        self.assertIn("Transport::LinesPerPage - 1", self.host)
        self.assertIn("CHECK(!authority->abortCompleted())", self.host)
        self.assertIn("checkImpossibleBridgeStateFailsClosed", self.host)
        self.assertIn("CHECK(bridge.productionStopped(0))", self.host)

    def test_combined_dirty_truth_and_reconstruction_collision_regressions(
        self,
    ) -> None:
        self.assertIn("callbackDirtyFlush", self.hh)
        self.assertIn(
            "runtimes[maaId]->correlationSnapshot().abortFlush", self.cc
        )
        self.assertIn(
            "CHECK(bridge.acknowledgeCallback(callback.token) == Status::Busy)",
            self.host,
        )
        self.assertIn(
            "CHECK(authority->correlationSnapshot().abortFlush)", self.host
        )
        self.assertIn(
            "checkDestroyedBridgeTokenCannotAuthenticateReconstruction",
            self.host,
        )
        self.assertIn(
            "successor.token.runtimeIdentity != stale.runtimeIdentity",
            self.host,
        )
        self.assertIn(
            "reconstructed.acknowledgeCallback(stale) == Status::Stale",
            self.host,
        )

    def test_identity_overflow_is_explicitly_fail_closed(self) -> None:
        self.assertIn("reserveRuntimeIdentity", self.hh)
        self.assertIn("std::overflow_error", self.cc)
        self.assertIn(
            "candidate == std::numeric_limits<uint64_t>::max()", self.cc
        )
        self.assertIn("nextCallbackIdentity == 0", self.cc)
        self.assertIn("checkFiniteIdentityBoundariesFailClosed", self.host)
        self.assertIn("partialConstructionExhausted", self.host)
        self.assertIn("std::numeric_limits<uint64_t>::max()", self.host)

    def test_injectable_partial_construction_and_dual_host_gate(self) -> None:
        self.assertIn("RuntimeFactory factory", self.hh)
        self.assertIn("injected construction failure", self.host)
        self.assertIn("-O2", self.runner)
        self.assertIn("-fsanitize=address,undefined", self.runner)
        self.assertIn("detect_leaks=0:halt_on_error=1", self.runner)


if __name__ == "__main__":
    unittest.main()
