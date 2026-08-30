#!/usr/bin/env python3
"""Adversarial unit tests for schema-v2 UMT factorial evidence identity."""

import copy
import os
import pathlib
import tempfile
import unittest

import umt_factorial_evidence as EVIDENCE


class UmtFactorialEvidenceTest(unittest.TestCase):
    @staticmethod
    def fixture(directory, tokens=24, width=1):
        directory = pathlib.Path(directory)
        variant = EVIDENCE.CELL_VARIANTS[(tokens, width)]
        source = directory / "source"
        identity = directory / "identity"
        build = source / "build" / variant
        headers = build / "config"
        kconfig = build / "gem5.build" / "config"
        build_opts = source / "build_opts" / variant
        target = build / "gem5.opt"
        gem5 = identity / "gem5.opt"
        manifest_path = identity / "build-manifest.json"
        headers.mkdir(parents=True)
        kconfig.parent.mkdir(parents=True)
        build_opts.parent.mkdir(parents=True)
        identity.mkdir()
        assignments = (
            f"LANL_MAA_UMT_COMPUTE_TOKENS={tokens}\n"
            f"LANL_MAA_UMT_FP_ISSUE_WIDTH={width}\n"
        )
        build_opts.write_text(assignments, encoding="utf-8")
        kconfig.write_text(assignments, encoding="utf-8")
        target.write_bytes(b"gem5-cell")
        started_at = "2026-08-29T00:00:00+00:00"
        ended_at = "2026-08-29T00:01:00+00:00"
        target_mtime_ns = (
            EVIDENCE.timestamp_ns(
                EVIDENCE.parse_manifest_timestamp(started_at, "started_at")
            )
            + 30_000_000_000
        )
        os.utime(target, ns=(target_mtime_ns, target_mtime_ns))
        gem5.write_bytes(target.read_bytes())
        stdout = identity / "build.stdout"
        stderr = identity / "build.stderr"
        stdout.write_text(
            f" [    LINK]  -> {variant}/gem5.opt\n", encoding="utf-8"
        )
        stderr.write_text("", encoding="utf-8")
        generated = {}
        for label, symbol in EVIDENCE.CONFIG_SYMBOLS.items():
            value = tokens if label == "compute_tokens" else width
            path = headers / f"{symbol.lower()}.hh"
            path.write_text(f"#define {symbol} {value}\n", encoding="utf-8")
            generated[label] = {
                "path": str(path.resolve()),
                "sha256": EVIDENCE.sha256(path),
                "symbol": symbol,
                "value": value,
            }
        manifest = {
            "schema": EVIDENCE.BUILD_MANIFEST_SCHEMA,
            "status": "passed",
            "cell": {
                "compute_tokens": tokens,
                "fp_issue_width": width,
                "variant": variant,
            },
            "source_commit": "1" * 40,
            "source_tree": "2" * 40,
            "source_clean_before_and_after": True,
            "source_identity_unchanged": True,
            "command": [
                str(pathlib.Path("/usr/bin/scons").resolve()),
                "--ignore-style",
                f"build/{variant}/gem5.opt",
                "-j4",
            ],
            "returncode": 0,
            "started_at": started_at,
            "ended_at": ended_at,
            "required_relink_observed": True,
            "build_opts": str(build_opts.resolve()),
            "build_opts_sha256": EVIDENCE.sha256(build_opts),
            "kconfig_state": str(kconfig.resolve()),
            "kconfig_state_sha256": EVIDENCE.sha256(kconfig),
            "generated_config_headers": generated,
            "target": str(target.resolve()),
            "target_size": target.stat().st_size,
            "target_mtime_ns": target.stat().st_mtime_ns,
            "gem5_sha256": EVIDENCE.sha256(target),
            "frozen_gem5": str(gem5.resolve()),
            "frozen_gem5_sha256": EVIDENCE.sha256(gem5),
            "stdout_sha256": EVIDENCE.sha256(stdout),
            "stderr_sha256": EVIDENCE.sha256(stderr),
            "builder_sha256": "5" * 64,
            "claim_boundary": "local exact cell build",
        }
        return manifest, manifest_path, gem5

    def test_all_four_cells_derive_exact_width_and_cost(self):
        expected = {
            (24, 1): (24, 64, 0, 0, 1169, 13412, 54372),
            (24, 2): (48, 128, 24, 64, 1169, 13412, 54372),
            (32, 1): (32, 64, 0, 0, 1170, 17182, 58142),
            (32, 2): (64, 128, 32, 64, 1170, 17182, 58142),
        }
        for (tokens, width), values in expected.items():
            with self.subTest(tokens=tokens, width=width):
                with tempfile.TemporaryDirectory() as temporary:
                    manifest, manifest_path, gem5 = self.fixture(
                        temporary, tokens, width
                    )
                    cell = EVIDENCE.validate_build_manifest_document(manifest)
                    file_cell, _root = EVIDENCE.validate_build_manifest_files(
                        manifest, manifest_path, gem5
                    )
                    self.assertEqual(cell, file_cell)
                    stats = EVIDENCE.static_cell_stats(cell)
                    self.assertEqual(
                        (
                            stats[
                                "descriptorUmtStateFpIssueSelectionCandidate"
                                "Inputs"
                            ],
                            stats["descriptorUmtStateFpIssueOperandRouteBits"],
                            stats[
                                "descriptorUmtStateIncrementalFpIssueSelection"
                                "CandidateInputs"
                            ],
                            stats[
                                "descriptorUmtStateIncrementalFpIssueOperand"
                                "RouteBits"
                            ],
                            stats[
                                "descriptorUmtStateInstrumentationLogical"
                                "BitsFloor"
                            ],
                            stats[
                                "descriptorUmtStateAuxiliaryLogicalBitsFloor"
                            ],
                            stats[
                                "descriptorUmtStatePhysicalStorePlusLogical"
                                "AuxiliaryBitsFloor"
                            ],
                        ),
                        values,
                    )

    def test_manifest_missing_field_and_mismatched_cell_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, _manifest_path, _gem5 = self.fixture(temporary)
            missing = copy.deepcopy(manifest)
            del missing["cell"]
            with self.assertRaisesRegex(RuntimeError, "missing or unknown"):
                EVIDENCE.validate_build_manifest_document(missing)
            changed = copy.deepcopy(manifest)
            changed["cell"]["variant"] = "X86_UMT_T32_W1"
            with self.assertRaisesRegex(RuntimeError, "variant mismatches"):
                EVIDENCE.validate_build_manifest_document(changed)

    def test_manifest_header_metadata_must_match_cell(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, _manifest_path, _gem5 = self.fixture(temporary)
            changed = copy.deepcopy(manifest)
            changed["generated_config_headers"]["compute_tokens"]["value"] = 32
            with self.assertRaisesRegex(RuntimeError, "header mismatches"):
                EVIDENCE.validate_build_manifest_document(changed)

    def test_generated_header_content_must_match_manifest_cell(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, manifest_path, gem5 = self.fixture(temporary)
            header_document = manifest["generated_config_headers"][
                "compute_tokens"
            ]
            header = pathlib.Path(header_document["path"])
            symbol = header_document["symbol"]
            header.write_text(f"#define {symbol} 32\n", encoding="utf-8")
            header_document["sha256"] = EVIDENCE.sha256(header)
            with self.assertRaisesRegex(
                RuntimeError, "mismatches manifest cell"
            ):
                EVIDENCE.validate_build_manifest_files(
                    manifest, manifest_path, gem5
                )

    def test_build_logs_are_required_and_rehashed(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, manifest_path, gem5 = self.fixture(temporary)
            stdout = manifest_path.parent / "build.stdout"
            stdout.unlink()
            with self.assertRaisesRegex(RuntimeError, "stdout log is absent"):
                EVIDENCE.validate_build_manifest_files(
                    manifest, manifest_path, gem5
                )

        with tempfile.TemporaryDirectory() as temporary:
            manifest, manifest_path, gem5 = self.fixture(temporary)
            stderr = manifest_path.parent / "build.stderr"
            stderr.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "stderr log hash"):
                EVIDENCE.validate_build_manifest_files(
                    manifest, manifest_path, gem5
                )

    def test_fake_log_hash_cannot_bless_real_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, manifest_path, gem5 = self.fixture(temporary)
            manifest["stdout_sha256"] = "f" * 64
            with self.assertRaisesRegex(RuntimeError, "stdout log hash"):
                EVIDENCE.validate_build_manifest_files(
                    manifest, manifest_path, gem5
                )

    def test_hashed_stdout_must_contain_exact_cell_link_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, manifest_path, gem5 = self.fixture(temporary)
            stdout = manifest_path.parent / "build.stdout"
            stdout.write_text(
                "[    LINK]  -> X86/gem5.opt\n", encoding="utf-8"
            )
            manifest["stdout_sha256"] = EVIDENCE.sha256(stdout)
            with self.assertRaisesRegex(RuntimeError, "cell-specific relink"):
                EVIDENCE.validate_build_manifest_files(
                    manifest, manifest_path, gem5
                )

    def test_target_stat_fields_are_bound_to_the_artifact(self):
        for name in ("target_size", "target_mtime_ns"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary:
                    manifest, manifest_path, gem5 = self.fixture(temporary)
                    manifest[name] += 1
                    stat_label = name.removeprefix("target_").removesuffix(
                        "_ns"
                    )
                    with self.assertRaisesRegex(
                        RuntimeError, f"target {stat_label}"
                    ):
                        EVIDENCE.validate_build_manifest_files(
                            manifest, manifest_path, gem5
                        )

    def test_target_mtime_must_fall_inside_build_interval(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, manifest_path, gem5 = self.fixture(temporary)
            manifest["ended_at"] = "2026-08-29T00:00:20+00:00"
            with self.assertRaisesRegex(RuntimeError, "outside the build"):
                EVIDENCE.validate_build_manifest_files(
                    manifest, manifest_path, gem5
                )

    def test_w1_is_exact_zero_and_w2_must_be_positive(self):
        w1 = EVIDENCE.FactorialCell(24, 1, "X86_UMT_T24_W1")
        w2 = EVIDENCE.FactorialCell(24, 2, "X86_UMT_T24_W2")
        self.assertEqual(
            EVIDENCE.validate_dual_issue(
                {"descriptorUmtStateDualIssueCycles": 0}, w1, True
            )["expected"],
            "exactly_zero",
        )
        with self.assertRaisesRegex(RuntimeError, "W1 cell"):
            EVIDENCE.validate_dual_issue(
                {"descriptorUmtStateDualIssueCycles": 1}, w1, True
            )
        with self.assertRaisesRegex(RuntimeError, "did not exercise"):
            EVIDENCE.validate_dual_issue(
                {"descriptorUmtStateDualIssueCycles": 0}, w2, True
            )
        self.assertEqual(
            EVIDENCE.validate_dual_issue(
                {"descriptorUmtStateDualIssueCycles": 7}, w2, True
            )["expected"],
            "positive",
        )


if __name__ == "__main__":
    unittest.main()
