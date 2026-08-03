import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class HybridTailInstrumentationContractTest(unittest.TestCase):
    def test_controller_exposes_fail_closed_blocker_domain(self):
        source = (ROOT / "src/mem/MAA/TransparentSPDController.hh").read_text()
        for blocker in (
            "ProducerNotReady",
            "StreamBusy",
            "ALUBusy",
            "SlotOwned",
            "Serialization",
            "InstructionFileFull",
        ):
            self.assertIn(blocker, source)
        self.assertIn("Blocker blocker() const", source)

    def test_summary_and_acceptance_are_versioned(self):
        maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        self.assertIn("event=transparent_blocker_summary schema=2", maa)
        self.assertIn("event=transparent_blocker_snapshot schema=2", maa)
        self.assertIn("point=all_pages_ready", maa)
        self.assertIn("event=transparent_consumer_accept schema=2", maa)
        self.assertIn("finishTransparentBlockerTracking", maa)

    def test_only_successful_transport_marks_acceptance(self):
        port = (ROOT / "src/mem/MAA/Port.cc").read_text()
        stream = (ROOT / "src/mem/MAA/StreamAccess.cc").read_text()
        self.assertEqual(port.count("it->paddr, true);"), 2)
        self.assertIn("if (transportAccepted && my_instruction->controllerManaged", stream)


if __name__ == "__main__":
    unittest.main()
