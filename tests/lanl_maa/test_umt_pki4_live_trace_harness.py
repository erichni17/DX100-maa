import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import normalize_umt_pki4_live_trace as live
import umt_ingress_micro_harness as ingress


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


normalizer = load_module(
    "live_trace_pinned_normalizer",
    live.NORMALIZER,
)
fixtures = load_module(
    "live_trace_pinned_fixtures",
    live.SOURCE / "tests/lanl_maa/test_umt_pki4_conformance_normalizer.py",
)


def trace_text(records):
    rows = []
    for record in records:
        value = {key: item for key, item in record.items() if key != "_line"}
        rows.append(normalizer.PREFIX + json.dumps(value, sort_keys=True))
    return "\n".join(rows) + "\n"


def snapshot_fixture(root, payload):
    root = pathlib.Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = root / "gem5.stderr"
    source.write_bytes(payload)
    source_status = source.stat()
    source_digest = hashlib.sha256(payload).hexdigest()
    terminal_path = root / ingress.ARM_EVIDENCE_DIRECTORY / "arm-terminal.json"
    terminal_path.parent.mkdir()
    terminal = {
        "outputs": {
            "gem5.stderr": {
                "path": str(source),
                "device": source_status.st_dev,
                "inode": source_status.st_ino,
                "sha256": source_digest,
                "reservation_identity_match": True,
            }
        }
    }
    terminal_path.write_text(
        json.dumps(terminal, sort_keys=True) + "\n", encoding="utf-8"
    )
    arm_report = {
        "execution": {"terminal_sha256": live.sha256(terminal_path)},
        "raw_sha256": {"gem5.stderr": source_digest},
    }
    snapshot = root / "analysis/pki4-canonical-v3" / live.SNAPSHOT_NAME
    return source, snapshot, arm_report


class Pki4LiveTraceHarnessTest(unittest.TestCase):
    def test_post_terminal_path_explicitly_scopes_legacy_callback_restarts(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "arm"
            analysis = root / "analysis/pki4-canonical-v3"
            arguments = types.SimpleNamespace(
                root=str(root),
                case="d32-g16",
                contract=str(pathlib.Path(temporary) / "contract.json"),
                contract_sha256="0" * 64,
                output=str(analysis / "normalization-summary-v1.json"),
                full_canonical_output=str(analysis / "full-canonical-v3.json"),
                shard_root=str(analysis / "sampled-complete-epochs"),
                hash_epoch_count=4,
            )
            with mock.patch.object(
                live.ingress,
                "analyze_arm",
                side_effect=RuntimeError("bounded stop after call capture"),
            ) as analyzer:
                with self.assertRaisesRegex(RuntimeError, "bounded stop"):
                    live.normalize_arm(arguments)
            self.assertTrue(
                analyzer.call_args.kwargs["allow_descriptor_callback_restart"]
            )

    def test_snapshot_rejects_mutation_during_streamed_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = b"a" * (live.SNAPSHOT_CHUNK_BYTES * 2 + 17)
            source, snapshot, report = snapshot_fixture(temporary, payload)
            source_inode = source.stat().st_ino
            original_read = live.os.read
            changed = False

            def mutate_after_first_source_read(descriptor, count):
                nonlocal changed
                block = original_read(descriptor, count)
                if (
                    block
                    and not changed
                    and os.fstat(descriptor).st_ino == source_inode
                ):
                    changed = True
                    with source.open("r+b") as stream:
                        stream.seek(0)
                        stream.write(b"z")
                        stream.flush()
                        os.fsync(stream.fileno())
                return block

            with mock.patch.object(
                live.os, "read", side_effect=mutate_after_first_source_read
            ):
                with self.assertRaisesRegex(RuntimeError, "changed|hash"):
                    live.capture_terminal_validated_snapshot(
                        temporary, report, snapshot
                    )
            self.assertFalse(snapshot.exists())

    def test_snapshot_rejects_replaced_path_and_symlink(self):
        for kind in ("replacement", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                payload = b"terminal validated bytes\n"
                source, snapshot, report = snapshot_fixture(tmp, payload)
                replacement = pathlib.Path(tmp) / "replacement"
                replacement.write_bytes(payload)
                source.unlink()
                if kind == "replacement":
                    os.replace(replacement, source)
                else:
                    source.symlink_to(replacement)
                with self.assertRaisesRegex(
                    RuntimeError, "identity|without following"
                ):
                    live.capture_terminal_validated_snapshot(
                        tmp, report, snapshot
                    )
                self.assertFalse(snapshot.exists())

    def test_snapshot_rejects_terminal_source_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, snapshot, report = snapshot_fixture(
                temporary, b"terminal validated bytes\n"
            )
            with source.open("r+b") as stream:
                stream.seek(0)
                stream.write(b"X")
                stream.flush()
                os.fsync(stream.fileno())
            with self.assertRaisesRegex(RuntimeError, "hash"):
                live.capture_terminal_validated_snapshot(
                    temporary, report, snapshot
                )
            self.assertFalse(snapshot.exists())

    def test_snapshot_post_normalization_mutation_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, snapshot, report = snapshot_fixture(
                temporary, b"terminal validated bytes\n"
            )
            evidence = live.capture_terminal_validated_snapshot(
                temporary, report, snapshot
            )
            descriptor = live.open_verified_snapshot(evidence)
            try:
                with snapshot.open("r+b") as stream:
                    stream.seek(0)
                    stream.write(b"X")
                    stream.flush()
                    os.fsync(stream.fileno())
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    live.verify_snapshot_unchanged(evidence, descriptor)
            finally:
                os.close(descriptor)

    def test_failed_action_snapshot_resume_is_exact_and_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = b"terminal validated bytes\n"
            source, snapshot, report = snapshot_fixture(temporary, payload)
            captured = live.capture_terminal_validated_snapshot(
                temporary, report, snapshot
            )
            resumed = live.reuse_terminal_validated_snapshot(
                temporary, report, snapshot, captured["sha256"]
            )
            self.assertEqual(
                resumed["publication_mode"],
                "reused_existing_terminal_validated_snapshot",
            )
            self.assertEqual(resumed["inode"], captured["inode"])
            self.assertEqual(resumed["sha256"], captured["sha256"])
            with self.assertRaisesRegex(RuntimeError, "terminal-bound"):
                live.reuse_terminal_validated_snapshot(
                    temporary, report, snapshot, "0" * 64
                )

            with snapshot.open("r+b") as stream:
                stream.seek(0)
                stream.write(b"X")
                stream.flush()
                os.fsync(stream.fileno())
            with self.assertRaisesRegex(RuntimeError, "hash/size"):
                live.reuse_terminal_validated_snapshot(
                    temporary, report, snapshot, captured["sha256"]
                )

    def test_snapshot_publication_is_bound_and_no_clobber(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, snapshot, report = snapshot_fixture(
                temporary, b"terminal validated bytes\n"
            )
            evidence = live.capture_terminal_validated_snapshot(
                temporary, report, snapshot
            )
            self.assertEqual(
                evidence["sha256"], report["raw_sha256"]["gem5.stderr"]
            )
            self.assertEqual(
                evidence["source"]["device"], source.stat().st_dev
            )
            self.assertEqual(evidence["source"]["inode"], source.stat().st_ino)
            frozen = snapshot.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                live.capture_terminal_validated_snapshot(
                    temporary, report, snapshot
                )
            self.assertEqual(snapshot.read_bytes(), frozen)

    def test_existing_contract_adds_d64_g31_with_exact_guest_mode(self):
        self.assertEqual(
            ingress.BUILD_PROOF_PATH,
            pathlib.Path(
                "/data1/nier/dx100-runs/"
                "2026-08-31-umt-pki4-conformance-build-v19-live/identity/"
                "pki4-conformance-build-proof-v19.json"
            ),
        )
        self.assertEqual(
            ingress.CASES["d64-g31"],
            {"abi": "D64", "groups": 31, "mode": "wave_d64"},
        )
        self.assertIn(
            "instrumented_build_proof_schema", ingress.CONTRACT_FIELDS
        )
        self.assertEqual(
            ingress.SCHEMA_BUILD_PROOF,
            "lanl-maa-umt-pki4-dual-gem5-build-proof-v19",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "d64-g31"
            command = ingress.case_command(
                ingress.CANONICAL_GEM5, root, "d64-g31"
            )
            self.assertIn("--groups=31", command)
            self.assertIn("--umt-mode=wave_d64", command)
        self.assertEqual(
            ingress.verify_guest_compatibility_source(),
            list(ingress.GUEST_COMPATIBILITY_PREFIX),
        )

    def test_freeze_rejects_noncanonical_and_missing_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            args = types.SimpleNamespace(
                campaign_root=str(root / "campaign"),
                output=str(root / "campaign" / ingress.CONTRACT_FILENAME),
                gem5=str(ingress.CANONICAL_GEM5),
                gem5_sha256="0" * 64,
                instrumented_build_proof=str(root / "wrong-proof.json"),
                instrumented_build_proof_sha256="0" * 64,
            )
            with mock.patch.object(ingress, "verify_harness_identity"):
                with self.assertRaisesRegex(RuntimeError, "exact future"):
                    ingress.freeze_contract(args)
            args.instrumented_build_proof = str(ingress.BUILD_PROOF_PATH)
            with (
                mock.patch.object(ingress, "verify_harness_identity"),
                mock.patch.object(
                    ingress,
                    "verify_hash",
                    return_value=ingress.CANONICAL_GEM5.resolve(),
                ),
                mock.patch.object(
                    ingress,
                    "read_build_proof",
                    side_effect=RuntimeError("missing exact v19 proof"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "missing exact"):
                    ingress.freeze_contract(args)

    def test_post_terminal_provenance_pins_repaired_replay(self):
        postprocessor = {
            "source_root": "/reviewed/postprocessor",
            "source_commit": "1" * 40,
            "source_tree": "2" * 40,
            "reviewed_file_sha256": {},
        }
        with mock.patch.object(
            live.ingress,
            "verify_harness_identity",
            return_value=postprocessor,
        ):
            value = live.verify_provenance()
            self.assertEqual(value["post_terminal_harness"], postprocessor)
            replay = value["approved_replay"]
            self.assertEqual(replay["source_commit"], live.REPLAY_COMMIT)
            self.assertEqual(replay["source_tree"], live.REPLAY_TREE)
            self.assertEqual(
                replay["independent_rereview"]["sha256"],
                live.REPLAY_REVIEW_SHA256,
            )
            self.assertFalse(replay["executed_by_this_action"])
            with mock.patch.object(live, "REPLAY_REVIEW_SHA256", "0" * 64):
                with self.assertRaisesRegex(RuntimeError, "approval"):
                    live.verify_provenance()

    def test_committed_normalizer_rejects_truncation_abort_and_bad_d64(self):
        wrong_schema = fixtures.valid_records()
        wrong_schema[0]["schema_version"] = 2
        serialized = trace_text(wrong_schema).splitlines(True)
        with self.assertRaises(normalizer.ConformanceError):
            normalizer.parse_trace(serialized)

        truncated = fixtures.valid_records(include_reset=False)[:-1]
        with self.assertRaises(normalizer.ConformanceError):
            normalizer.validate_and_normalize(truncated)

        aborted = fixtures.valid_records(include_reset=False)
        aborted[-1]["callback_aborted"] = True
        with self.assertRaisesRegex(
            normalizer.ConformanceError, "excluded from promotion"
        ):
            normalizer.validate_and_normalize(aborted)

        malformed = fixtures.d64_g31_tail_records(0)
        malformed[2]["waiter_count"] -= 1
        with self.assertRaises(normalizer.ConformanceError):
            normalizer.validate_and_normalize(malformed)

    def test_complete_epoch_selection_is_deterministic_and_anchored(self):
        complete = list(range(1, 11))
        first = live.select_epochs(complete, "a" * 64, 3)
        second = live.select_epochs(complete, "a" * 64, 3)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 5)
        self.assertIn(1, first)
        self.assertIn(10, first)

    def test_epoch_extraction_never_accepts_open_or_partial_epoch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            open_trace = root / "open.jsonl"
            open_trace.write_text(
                trace_text(fixtures.valid_records(include_reset=False)),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "complete"):
                live.discover_epochs(open_trace)

            complete_trace = root / "complete.jsonl"
            complete_trace.write_text(
                trace_text(fixtures.valid_records(include_reset=True)),
                encoding="utf-8",
            )
            discovery = live.discover_epochs(complete_trace)
            self.assertEqual(discovery["complete_epochs"], [1])
            paths = live.extract_epoch_traces(
                complete_trace, discovery, [1], root / "shards"
            )
            extracted = normalizer.parse_trace(
                paths[1].read_text(encoding="utf-8").splitlines(True)
            )
            result = normalizer.validate_and_normalize(
                extracted, allow_final_open_epoch=False
            )
            self.assertEqual(
                result["trace_closure"], "all_epochs_reset_closed"
            )

    def test_d64_g31_legacy_lifecycle_rejects_bad_tail_release(self):
        suite = ingress.parse_debug_file_text
        source = []
        digest = 1
        for callback_id, kind, count in (
            (1, "source", 8),
            (2, "denominator", 7),
        ):
            for lane in range(count):
                source.append(
                    "UMT_INGRESS kind=%s cycle=%d callback=%d lane=%d "
                    "packet=0x10 line=0x10 abi=5 stage=0 group=%d corner=0 "
                    "order=%d waiters=%d token=18446744073709551615 "
                    "pre=0x%x post=0x%x next_engine_tick=%d"
                    % (
                        kind,
                        10 + callback_id,
                        callback_id,
                        lane,
                        lane,
                        lane,
                        count,
                        digest,
                        digest + 1,
                        11 + callback_id,
                    )
                )
                digest += 1
        source.extend(
            "UMT_INGRESS kind=d64_%s cycle=%d line=%s abi=5 stage=0 "
            "group=%d corner=0 waiters=%d pre=0x1 post=0x1"
            % (kind, cycle, line, group, waiters)
            for line, group, base_cycle, waiter_range in (
                ("0x10", 0, 20, range(1, 9)),
                ("0x50", 24, 30, range(1, 8)),
            )
            for waiters in waiter_range
            for kind, cycle in (
                (("hold", base_cycle + waiters),)
                if waiters != waiter_range[-1]
                else (("release", base_cycle + waiters),)
            )
        )
        events = suite("\n".join(source))
        ingress.validate_trace(events, "d64-g31")
        events[-1]["waiters"] = 6
        with self.assertRaisesRegex(RuntimeError, "lifecycle|7-word"):
            ingress.validate_trace(events, "d64-g31")


if __name__ == "__main__":
    unittest.main()
