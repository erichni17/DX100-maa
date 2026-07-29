import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_xrage_direct_index_smoke.sh"
RECOVERY = ROOT / "experiments/scripts/recover_xrage_checkpoint.sh"


class XrageRunnerAbiTest(unittest.TestCase):
    def test_rejects_guest_and_logical_aperture_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            gem5 = tmp / "gem5.opt"
            binary = tmp / "spatter_maa_runtime_16K"
            input_json = tmp / "input.json"
            for executable in (gem5, binary):
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
                executable.chmod(0o755)
            input_json.write_text("[]\n", encoding="ascii")
            output = tmp / "output"
            environment = os.environ.copy()
            environment.update(
                {
                    "DX100_ROOT_OVERRIDE": str(ROOT),
                    "XRAGE_ARM": "fused_4k",
                    "MAA_GUEST_ABI_TILE_ELEMENTS": "16384",
                }
            )

            result = subprocess.run(
                [
                    str(RUNNER),
                    str(gem5),
                    str(binary),
                    str(input_json),
                    str(output),
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "must equal the gem5 logical aperture", result.stderr
            )
            self.assertFalse(output.exists())

    def test_checkpoint_retarget_is_explicit_and_pre_maa_only(self):
        script = RECOVERY.read_text(encoding="utf-8")
        self.assertIn("XRAGE_ALLOW_PRE_MAA_RETARGET", script)
        self.assertIn("--cpu-type AtomicSimpleCPU", script)
        self.assertIn("checkpoint already configures MAA", script)
        self.assertIn("checkpoint_retargeted=%s", script)
        self.assertIn("checkpoint_original_physical=%s", script)


if __name__ == "__main__":
    unittest.main()
