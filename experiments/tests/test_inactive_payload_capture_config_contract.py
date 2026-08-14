import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class InactivePayloadCaptureConfigContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.options = (ROOT / "configs/common/Options.py").read_text()
        cls.config = (ROOT / "configs/common/MAAConfig.py").read_text()
        cls.params = (ROOT / "src/mem/MAA/MAA.py").read_text()
        cls.maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        cls.capture = (
            ROOT / "src/mem/MAA/InactiveProducerLinePayloadCapture.hh"
        ).read_text()

    def test_cli_accepts_only_first_owner(self):
        option = self.options.split(
            '"--maa_inactive_page_payload_capture_conflict_policy",', 1
        )[1].split('"--maa_virtual_index_buffer_lines",', 1)[0]
        self.assertIn('choices=("first-owner",)', option)
        self.assertNotIn("latest-owner", option)

    def test_simobject_config_fails_closed_for_latest_owner(self):
        self.assertIn(
            'p.inactive_page_payload_capture_conflict_policy != "first-owner"',
            self.maa,
        )
        self.assertIn("latest-owner is not supported", self.maa)
        self.assertIn(
            '"inactive_page_payload_capture_conflict_policy"] = getattr(',
            self.config,
        )
        self.assertIn("only first-owner is supported", self.params)

    def test_capture_has_no_selectable_latest_owner_path(self):
        self.assertNotIn("ConflictPolicy", self.capture)
        self.assertNotIn("Overwritten", self.capture)
        self.assertIn(
            'conflictPolicyName() { return "first-owner"; }', self.capture
        )


if __name__ == "__main__":
    unittest.main()
