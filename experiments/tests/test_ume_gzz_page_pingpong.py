"""Contracts for the default-off GZZ page ping-pong experiment."""

import unittest
from pathlib import Path

from experiments.scripts import run_ume_gzz_page_pingpong as runner


class UmeGzzPagePingPongTest(unittest.TestCase):
    def test_candidate_uses_eighth_provisioned_tile(self) -> None:
        source = (runner.ROOT / "benchmarks/UME/gradzatz.cpp").read_text()
        for token in (
            "UME_GZZ_PAGE_CONSUMER_PINGPONG",
            "page_alternate_tiles",
            "physical_tiles_per_core=8 pingpong=1",
        ):
            self.assertIn(token, source)

    def test_runner_is_candidate_only_and_fail_closed(self) -> None:
        source = Path(runner.__file__).read_text()
        self.assertEqual(runner.ARM.name, "strict_bounded_hybrid")
        self.assertIn("matched.validate(AUTHORITY)", source)
        self.assertIn("base.classify_arm(root, ARM, manifest)", source)
        self.assertNotIn("native16.selector", source)


if __name__ == "__main__":
    unittest.main()
