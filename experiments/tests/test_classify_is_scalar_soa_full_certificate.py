"""Adversarial unit tests for the sealed NAS IS full successor certificate."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT / "experiments/scripts/classify_is_scalar_soa_full_certificate.py"
)
SPEC = importlib.util.spec_from_file_location("is_certificate", SCRIPT)
assert SPEC and SPEC.loader
CERT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CERT)


class IsScalarSoaFullCertificateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "historical"
        (self.root / "run").mkdir(parents=True)
        self.write("checkpoint.exit", "0\n")
        self.write("terminal.status", "PASS\n")
        self.write("run/restore.exit", "0\n")
        self.write("manifest.txt", self.manifest())
        self.write("runtime_gem5_recovery.manifest", self.recovery())
        self.write(
            "result.tsv",
            "action\tsimTicks\tinstructions\tterminals\tselected\trejected\tindex_lines\tindex_words\tpredicate_issues\tpredicate_responses\tvalue_issues\tvalue_responses\ta_read_issues\ta_read_responses\ta_write_issues\ta_write_responses\taliases\nfull\t379831843258\t2048\t2048\t33554432\t0\t2099200\t33554432\t0\t0\t0\t0\t31020345\t31020345\t31020345\t31020345\t33554432\n",
        )
        self.write(
            "run/restore.log",
            "IS_SCALAR_SOA_JIT_SELECTION compiled=1 treatment=scalar_soa_jit legacy_default=0\nIS_SCALAR_SOA_JIT_TERMINAL treatment=scalar_soa_jit logical=16384 scalar=1 predicate=null min=0 max=exact_count stride=1 generations=2048 full_windows=2048 tail_words=0 index_words=33554432 predicate_words=0 value_words=0 host_spd_reads=0 staging_bytes=0 result=PASS\nROI End!!!\nsuccessfull: passed verification 6\nExiting @ tick 99 because m5_exit instruction encountered\n",
        )
        self.write("run/stats.txt", self.stats())
        self.write(
            "run/config.ini",
            "num_cores=4\nnum_tiles_per_core=8\nnum_tile_elements=16384\nphysical_tile_elements=4096\nnum_offset_table_entries=16384\nnum_offset_table_epoch_entries=16384\nnum_initial_row_table_slices=32\nnum_memory_channels=2\n[system.mem_ctrls0]\n[system.mem_ctrls1]\n",
        )
        self.raw = {
            name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
            for name in (
                "checkpoint.exit",
                "terminal.status",
                "run/restore.exit",
                "manifest.txt",
                "runtime_gem5_recovery.manifest",
                "result.tsv",
                "run/restore.log",
                "run/stats.txt",
                "run/config.ini",
            )
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, value: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def manifest(self) -> str:
        source = ROOT / CERT.SOURCE_RELATIVE
        return (
            "action=full\nsource_commit="
            + CERT.SOURCE_COMMIT
            + "\nsource_path="
            + str(source)
            + "\nsource_sha256="
            + CERT.SOURCE_SHA256
            + "\ngem5_sha256="
            + CERT.GEM5_SHA256
            + "\nguest_sha256="
            + CERT.GUEST_SHA256
            + "\ninput_sha256="
            + CERT.INPUT_SHA256
            + "\nfrozen_native_sha256="
            + CERT.BASELINE_SHA256
            + "\nlogical_elements=16384\nphysical_tile_elements=4096\nmemory_channels=2\nrow_table_slices=32\nnative_runs=0\n"
        )

    def recovery(self) -> str:
        return (
            "schema=dx100.runtime_executable_recovery.v1\nreason=lead_build_path_replaced_after_process_start\nunit=dx100-is-scalar-soa-full-a44aaa60-r5\nmain_pid=1753022\ngem5_pid=1755099\ngem5_pid_start_ticks=289995712\nlive_exe_link=/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/build/X86/gem5.opt (deleted)\nlive_exe_sha256="
            + CERT.GEM5_SHA256
            + "\narchived_gem5_path=/data1/nier/dx100-binaries/gem5-"
            + CERT.GEM5_SHA256
            + ".opt\narchived_gem5_sha256="
            + CERT.GEM5_SHA256
            + "\ncmdline_sha256=9e31231374914268a8989dd62ab09ce20ad4ffa1e640e612c40f67297cdeba42\ncgroup=/user.slice/user-114457255.slice/user@114457255.service/app.slice/dx100-is-scalar-soa-full-a44aaa60-r5.service\nsimulation_state_changed=false\n"
        )

    def stats(self) -> str:
        values = {
            "IND_SoaJitInstructions": 2048,
            "IND_SoaJitTerminalCompletions": 2048,
            "IND_SoaJitSelected": 33554432,
            "IND_SoaJitAliasesApplied": 33554432,
            "IND_SoaJitPredicateRejected": 0,
            "IND_SoaJitPredicateLineReads": 0,
            "IND_SoaJitPredicateLineResponses": 0,
            "IND_SoaJitValueReadIssues": 0,
            "IND_SoaJitValueReadResponses": 0,
            "IND_SoaJitAReadIssues": 31020345,
            "IND_SoaJitAReadResponses": 31020345,
            "IND_SoaJitAWriteIssues": 31020345,
            "IND_SoaJitAWriteResponses": 31020345,
            "IND_DescriptorSpoolControlBytes": 0,
            "IND_DescriptorSpoolBackingBytes": 0,
            "cpu_spd_data_read_deferrals": 0,
            "cpu_spd_data_read_retry_signals": 0,
            "cpu_spd_data_read_retry_attempts": 0,
            "cpu_spd_data_read_retry_acceptances": 0,
        }
        return (
            CERT.BEGIN
            + "\nsimTicks 379831843258\n"
            + "".join(f"x_{key} {value}\n" for key, value in values.items())
            + CERT.END
            + "\n"
        )

    def validate(self) -> dict:
        return CERT.validate_evidence(
            self.root,
            ROOT,
            raw_hashes=self.raw,
            external_hashes={},
            use_independent_classifier=False,
        )

    def rejected(self) -> None:
        with self.assertRaises(CERT.CertificateError):
            self.validate()

    def test_valid_fixture_is_correctness_only(self) -> None:
        result = self.validate()
        self.assertEqual(result["verdict"], "PASS_FULL_IS_CORRECTNESS")
        self.assertFalse(result["performance_promoted"])
        self.assertEqual(result["physical_spd_payload_bytes"], 524288)

    def test_rejects_missing_verification(self) -> None:
        self.write(
            "run/restore.log",
            (self.root / "run/restore.log")
            .read_text()
            .replace("successfull: passed verification 6\n", ""),
        )
        self.rejected()

    def test_rejects_altered_result_row(self) -> None:
        self.write(
            "result.tsv",
            (self.root / "result.tsv")
            .read_text()
            .replace("31020345", "31020344", 1),
        )
        self.rejected()

    def test_rejects_bad_recovery_identity(self) -> None:
        self.write(
            "runtime_gem5_recovery.manifest",
            self.recovery().replace(
                "simulation_state_changed=false",
                "simulation_state_changed=true",
            ),
        )
        self.rejected()

    def test_rejects_mutable_source_without_git_reconstruction(self) -> None:
        self.write(
            "manifest.txt",
            self.manifest().replace(
                str(ROOT / CERT.SOURCE_RELATIVE),
                str(self.root / "mutable.cpp"),
            ),
        )
        self.write("mutable.cpp", "not the committed source\n")
        self.rejected()

    def test_rejects_changed_geometry(self) -> None:
        self.write(
            "run/config.ini",
            (self.root / "run/config.ini")
            .read_text()
            .replace(
                "physical_tile_elements=4096", "physical_tile_elements=8192"
            ),
        )
        self.rejected()

    def test_rejects_value_or_host_or_staging_traffic(self) -> None:
        for change in ("value_words=1", "host_spd_reads=1", "staging_bytes=1"):
            with self.subTest(change=change):
                self.write(
                    "run/restore.log",
                    (self.root / "run/restore.log")
                    .read_text()
                    .replace(change.split("=")[0] + "=0", change),
                )
                self.rejected()
                self.write(
                    "run/restore.log",
                    "IS_SCALAR_SOA_JIT_SELECTION compiled=1 treatment=scalar_soa_jit legacy_default=0\nIS_SCALAR_SOA_JIT_TERMINAL treatment=scalar_soa_jit logical=16384 scalar=1 predicate=null min=0 max=exact_count stride=1 generations=2048 full_windows=2048 tail_words=0 index_words=33554432 predicate_words=0 value_words=0 host_spd_reads=0 staging_bytes=0 result=PASS\nROI End!!!\nsuccessfull: passed verification 6\nExiting @ tick 99 because m5_exit instruction encountered\n",
                )

    def test_rejects_timing_promotion_missing_checkpoint_or_hash_change(
        self,
    ) -> None:
        self.write(
            "result.tsv",
            (self.root / "result.tsv")
            .read_text()
            .replace("379831843258", "1"),
        )
        self.rejected()
        self.write(
            "result.tsv",
            "action\tsimTicks\tinstructions\tterminals\tselected\trejected\tindex_lines\tindex_words\tpredicate_issues\tpredicate_responses\tvalue_issues\tvalue_responses\ta_read_issues\ta_read_responses\ta_write_issues\ta_write_responses\taliases\nfull\t379831843258\t2048\t2048\t33554432\t0\t2099200\t33554432\t0\t0\t0\t0\t31020345\t31020345\t31020345\t31020345\t33554432\n",
        )
        (self.root / "checkpoint.exit").unlink()
        self.rejected()

    def test_rejects_premature_gate(self) -> None:
        output = pathlib.Path(self.tmp.name) / "output"
        output.mkdir()
        (output / "gate.complete").write_text("PASS_FULL_IS_CORRECTNESS\n")
        with self.assertRaises(CERT.CertificateError):
            CERT.publish(output, self.validate(), self.root, ROOT)

    def test_validate_output_requires_complete_sealed_files(self) -> None:
        output = pathlib.Path(self.tmp.name) / "out"
        output.mkdir()
        (output / "gate.complete").write_text("PASS_FULL_IS_CORRECTNESS\n")
        with self.assertRaises(CERT.CertificateError):
            CERT.validate_output(output, self.validate())


if __name__ == "__main__":
    unittest.main()
