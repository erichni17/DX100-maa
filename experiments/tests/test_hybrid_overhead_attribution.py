import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/analysis/hybrid_overhead_attribution.py"
SPEC = importlib.util.spec_from_file_location("hybrid_overhead", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def physical_payload(itr: int) -> str:
    fields = {
        "schema": MODULE.PHYSICAL_SCHEMA,
        "event": "physical_admission",
        "itr": str(itr),
        "b_paddr": hex(0x1000 + itr * 4),
        "b_value": str(itr + 3),
        "a_paddr": hex(0x2000 + itr * 8),
        "a_line_paddr": "0x2000",
        "channel": "0",
        "rank": "0",
        "bank_group": "1",
        "bank": "2",
        "row": "3",
        "column": "4",
        "native_slice": str(itr),
        "grow_addr": hex(0x30 + itr),
        "wid": str(itr),
        "generation_available": "0",
        "generation": "0",
        "opcode": "14",
        "optype": "16",
        "if_id": "0",
        "cid": "0",
        "pc": "0x4000",
        "operation_tick": "100",
        "controller_managed": "0",
        "controller_action": "0",
        "controller_transaction": "0",
        "controller_page": "-1",
        "rt_config": "4",
        "aperture_slice_begin": "0",
        "aperture_slice_end": "16",
        "aperture_slices": "16",
        "provenance": "direct_index_descriptor_admission",
    }
    return " ".join(f"{key}={value}" for key, value in fields.items())


def execute_line(
    tick: int,
    occurrence: str,
    sequence: str,
    *,
    unit: str = "0",
    operation_tick: str = "100",
    state: str = "Request",
    itr: str = "0",
) -> str:
    return (
        f"{tick}: global: event=indirect_execute schema=2 unit={unit} "
        f"occurrence={occurrence} operation_tick={operation_tick} "
        f"sequence={sequence} state={state} itr={itr}\n"
    )


def counter_summary(
    source_issues: int = 0,
    *,
    unit: str = "0",
    operation_tick: str = "100",
    occurrence: str = "0",
    row_pressure: int = 0,
    offset_pressure: int = 0,
) -> dict:
    return {
        "schema": MODULE.ATTRIBUTION_SCHEMA,
        "event": "indirect_counter_summary",
        "unit": unit,
        "occurrence": occurrence,
        "operation_tick": operation_tick,
        "row_attempts": str(2 + row_pressure),
        "row_successes": "2",
        "offset_pressure": str(offset_pressure),
        "row_pressure": str(row_pressure),
        "source_issues": str(source_issues),
        "source_responses": "0",
        "combiner_words": "0",
        "write_issues": "0",
        "write_completions": "0",
    }


class HybridOverheadAttributionTest(unittest.TestCase):
    def write_trace(self, lines):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "trace.log"
        path.write_text("".join(lines))
        self.addCleanup(temp.cleanup)
        return path

    def make_shared_input_fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        pair = Path(temp.name)
        input_root = pair / "input"
        input_root.mkdir()
        implementation_commit = "1" * 40
        artifact_paths = {
            "gem5.opt": input_root / "gem5.opt",
            "workload": input_root
            / "workload_build/test_virtual_tile_consumer_T16384",
            "libramulator.so": input_root / "libramulator.so",
            "ramulator_provenance.json": input_root
            / "ramulator_provenance.json",
            "gem5.ldd.txt": input_root / "gem5.ldd.txt",
            "create_shared_checkpoint.sh": input_root
            / "create_shared_checkpoint.sh",
            "source_snapshot_manifest": input_root
            / f"source_snapshot_{implementation_commit[:7]}.sha256",
        }
        for name, path in artifact_paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if name not in {"ramulator_provenance.json", "gem5.ldd.txt"}:
                path.write_bytes(f"fixture:{name}\n".encode())
        library = artifact_paths["libramulator.so"]
        provenance = {
            "schema": "dx100.ramulator_provenance.v1",
            "outer_tree": "2" * 40,
            "source_tree": "3" * 40,
            "nested_gitlinks": {
                "argparse": "4" * 40,
                "spdlog": "5" * 40,
                "yaml-cpp": "6" * 40,
            },
            "normalized_dependency_sha256": "7" * 64,
            "elf_build_id": "abcdef",
            "frozen_library": {
                "path": str(library.resolve()),
                "sha256": MODULE.sha256(library),
            },
            "reference_worktree": "/reference",
        }
        artifact_paths["ramulator_provenance.json"].write_text(
            json.dumps(provenance)
        )
        artifact_paths["gem5.ldd.txt"].write_text(
            f"libramulator.so => {library.resolve()} (0x1230)\n"
        )
        manifest = {
            "schema": "dx100.hybrid_pair_frozen_input.v1",
            "baseline_commit": MODULE.BASELINE_COMMIT,
            "implementation_commit": implementation_commit,
            "git_tree_clean_before_build": True,
            "artifacts": {
                name: MODULE.sha256(path)
                for name, path in artifact_paths.items()
            },
            "ramulator": {
                key: provenance[key]
                for key in (
                    "outer_tree",
                    "source_tree",
                    "normalized_dependency_sha256",
                    "elf_build_id",
                )
            },
            "dynamic_link_check": MODULE.EXPECTED_DYNAMIC_LINK_CHECK,
        }
        manifest_path = input_root / "frozen_input_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        checkpoint_root = pair / "shared_checkpoint"
        checkpoint_records = {}
        for target in MODULE.CHECKPOINT_TARGETS:
            artifact = checkpoint_root / target.removeprefix("./")
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(f"checkpoint:{target}\n".encode())
            checkpoint_records[target] = MODULE.sha256(artifact)
        checkpoint_manifest = pair / "checkpoint_files.pre_treatment.sha256"
        checkpoint_manifest.write_text(
            "".join(
                f"{checkpoint_records[target]}  {target}\n"
                for target in MODULE.CHECKPOINT_TARGETS
            )
        )
        identity = MODULE.sha256(checkpoint_manifest)
        checkpoint_log = pair / "checkpoint_create.log"
        checkpoint_log.write_text(
            "VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0 "
            "logical_elements=16384 mem_size=2147483648\n"
            "Writing checkpoint\n"
            f"{MODULE.EXPECTED_CHECKPOINT_TERMINAL_REASON}\n"
        )
        (pair / "checkpoint_create.exit").write_text("0\n")
        terminal = {
            "schema": "dx100.deferred_checkpoint_terminal.v1",
            "exit_code": 0,
            "terminal_reason": MODULE.EXPECTED_CHECKPOINT_TERMINAL_REASON,
            "checkpoint_tick": MODULE.EXPECTED_CHECKPOINT_TICK,
            "checkpoint_write_markers": 1,
            "terminal_markers": 1,
            "populated_checkpoint_directories": 1,
            "treatment_selector_absent_at_checkpoint": True,
            "checkpoint_files_manifest_sha256": identity,
            "checkpoint_log_sha256": MODULE.sha256(checkpoint_log),
        }
        terminal.update(
            {
                field: checkpoint_records[target]
                for target, field in (
                    MODULE.CHECKPOINT_TERMINAL_HASH_FIELDS.items()
                )
            }
        )
        (pair / "checkpoint_terminal.json").write_text(json.dumps(terminal))
        return pair, identity, implementation_commit, manifest

    def make_completion_fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        run = Path(temp.name)
        (run / "virtual_tile_consumer_case.pass").touch()
        (run / "restore.log").write_text(
            "VIRTUAL_TILE_CONSUMER_RESULT mode=native_direct "
            "page_elements=16384 "
            f"hash={MODULE.EXPECTED_OUTPUT_HASH} errors=0\n"
            "ROI Ended\n"
            "Exiting @ tick 42 because m5_exit instruction encountered\n"
        )
        result = {
            "case": "native_direct_16k",
            "output_hash": MODULE.EXPECTED_OUTPUT_HASH,
        }
        return run, result

    def test_physical_schema_count_hash_and_explicit_unavailable_generation(
        self,
    ):
        path = self.write_trace(
            [
                f"100: global: {physical_payload(0)}\n",
                f"101: global: {physical_payload(1)}\n",
            ]
        )
        records = path.parent / "records.jsonl"
        result = MODULE.validate_physical(
            path, expected=2, aperture=16, records_output=records
        )
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["field_count"], len(MODULE.PHYSICAL_FIELDS))
        self.assertEqual(result["generation"]["unavailable_records"], 2)
        self.assertEqual(len(result["record_sha256"]), 64)
        serialized = [
            json.loads(line) for line in records.read_text().splitlines()
        ]
        self.assertEqual([record["itr"] for record in serialized], ["0", "1"])
        self.assertEqual(result["records"]["sha256"], MODULE.sha256(records))
        self.assertEqual(
            set(serialized[0]),
            MODULE.PHYSICAL_FIELDS | {"sim_tick", "trace_line"},
        )

    def test_physical_rejects_duplicate_missing_extra_and_out_of_range(self):
        duplicate = self.write_trace(
            [
                f"100: global: {physical_payload(0)}\n",
                f"101: global: {physical_payload(0)}\n",
            ]
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "duplicate/out-of-range"
        ):
            MODULE.validate_physical(duplicate, expected=2, aperture=16)

        malformed = self.write_trace(
            [
                f"100: global: {physical_payload(0)} extra=1\n",
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "extra"):
            MODULE.validate_physical(malformed, expected=1, aperture=16)

        missing_payload = physical_payload(0).replace("channel=0 ", "")
        missing = self.write_trace([f"100: global: {missing_payload}\n"])
        with self.assertRaisesRegex(MODULE.AuditError, "missing"):
            MODULE.validate_physical(missing, expected=1, aperture=16)

        out_of_range = self.write_trace(
            [
                f"100: global: {physical_payload(0)}\n",
                f"101: global: {physical_payload(2)}\n",
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "out-of-range"):
            MODULE.validate_physical(out_of_range, expected=2, aperture=16)

        malformed_token = self.write_trace(
            [
                f"100: global: {physical_payload(0)} broken\n",
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "malformed token"):
            MODULE.validate_physical(malformed_token, expected=1, aperture=16)

    def test_versioned_trace_rejects_unknown_event_and_duplicate_field(self):
        unknown = self.write_trace(
            ["100: global: event=surprise schema=2 occurrence=0 value=3\n"]
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "unknown versioned event"
        ):
            MODULE.strict_events(unknown)
        duplicate = self.write_trace(
            [
                "100: global: event=indirect_execute schema=2 unit=0 unit=0 "
                "occurrence=0 operation_tick=100 sequence=0 state=Idle itr=0\n"
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "duplicate field"):
            MODULE.strict_events(duplicate)

    def test_stall_events_require_unique_execute_sequence(self):
        valid = self.write_trace(
            [
                execute_line(100, "0", "0", state="Idle", itr="11"),
                "100: global: event=indirect_stall schema=2 unit=0 "
                "occurrence=1 operation_tick=100 sequence=0 "
                "reason=row_table_full itr=11 "
                "slice=1 grow=0x20\n",
                execute_line(100, "2", "1", itr="11"),
                "100: global: event=indirect_stall schema=2 unit=0 "
                "occurrence=3 operation_tick=100 sequence=1 "
                "reason=row_table_full itr=11 "
                "slice=1 grow=0x20\n",
            ]
        )
        self.assertEqual(len(MODULE.strict_events(valid)), 4)

        missing_sequence = self.write_trace(
            [
                execute_line(100, "0", "0", state="Idle", itr="11"),
                "100: global: event=indirect_stall schema=2 unit=0 "
                "occurrence=1 operation_tick=100 reason=row_table_full "
                "itr=11 slice=1 grow=0x20\n",
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "malformed stall"):
            MODULE.strict_events(missing_sequence)

        duplicate = self.write_trace(
            [
                execute_line(100, "0", "0", state="Idle", itr="11"),
                "100: global: event=indirect_stall schema=2 unit=0 "
                "occurrence=1 operation_tick=100 sequence=0 "
                "reason=row_table_full itr=11 "
                "slice=1 grow=0x20\n",
                "100: global: event=indirect_stall schema=2 unit=0 "
                "occurrence=1 operation_tick=100 sequence=0 "
                "reason=row_table_full itr=11 "
                "slice=1 grow=0x20\n",
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "duplicate versioned"):
            MODULE.strict_events(duplicate)

        changed_payload_same_occurrence = self.write_trace(
            [
                execute_line(100, "0", "0", state="Idle", itr="11"),
                "100: global: event=indirect_stall schema=2 unit=0 "
                "occurrence=1 operation_tick=100 sequence=0 "
                "reason=row_table_full itr=11 "
                "slice=1 grow=0x20\n",
                execute_line(101, "1", "1", itr="12"),
            ]
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "occurrence discontinuity"
        ):
            MODULE.strict_events(changed_payload_same_occurrence)

    def test_every_repeatable_event_schema_has_source_occurrence(self):
        for name, fields in MODULE.EVENT_FIELDS.items():
            if fields is None:
                continue
            self.assertIn("occurrence", fields, name)
            if "unit" in fields:
                self.assertIn("operation_tick", fields, name)

        old_schema = self.write_trace(
            [
                "100: global: event=indirect_execute schema=1 unit=0 "
                "occurrence=0 operation_tick=100 sequence=0 state=Idle itr=0\n"
            ]
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "unknown versioned schema"
        ):
            MODULE.strict_events(old_schema)

    def test_rejects_missing_schema_and_bad_occurrence_domains(self):
        missing_schema = self.write_trace(
            [
                "100: global: event=indirect_execute unit=0 occurrence=0 "
                "operation_tick=100 sequence=0 state=Idle itr=0\n"
            ]
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "unknown versioned schema"
        ):
            MODULE.strict_events(missing_schema)

        bad_traces = {
            "gap": [
                execute_line(100, "0", "0", state="Idle"),
                execute_line(101, "2", "1"),
            ],
            "reorder": [execute_line(100, "1", "0", state="Idle")],
            "overflow": [execute_line(100, str(1 << 64), "0", state="Idle")],
            "unit alias": [
                execute_line(100, "0", "0", unit="00", state="Idle")
            ],
        }
        for label, lines in bad_traces.items():
            with self.subTest(label=label):
                with self.assertRaises(MODULE.AuditError):
                    MODULE.strict_events(self.write_trace(lines))

    def test_rejects_duplicate_negative_and_unlinked_execute_sequences(self):
        duplicate = self.write_trace(
            [
                execute_line(100, "0", "0", state="Idle"),
                execute_line(101, "1", "0"),
            ]
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "sequence discontinuity"
        ):
            MODULE.strict_events(duplicate)

        negative = self.write_trace(
            [execute_line(100, "0", "-1", state="Idle")]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "canonical unsigned"):
            MODULE.strict_events(negative)

        unlinked = self.write_trace(
            [
                execute_line(100, "0", "0", state="Idle"),
                "100: global: event=indirect_stall schema=2 unit=0 "
                "occurrence=1 operation_tick=100 sequence=1 "
                "reason=row_table_full itr=0 slice=0 grow=0x0\n",
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "owning execute"):
            MODULE.strict_events(unlinked)

    def test_counter_events_reconcile_or_fail_closed(self):
        summary = counter_summary()
        self.assertEqual(MODULE.reconcile_counter_events([summary]), [summary])
        rendered = MODULE.normalized_counter_summary(
            {**summary, "line": 7, "sim_tick": 100}
        )
        self.assertEqual(rendered["unit"], 0)
        self.assertNotIn("line", rendered)
        self.assertNotIn("sim_tick", rendered)
        bad = counter_summary(source_issues=1)
        with self.assertRaisesRegex(
            MODULE.AuditError, "counter/event mismatch"
        ):
            MODULE.reconcile_counter_events([bad])

        pressure_summary = counter_summary(row_pressure=1, offset_pressure=1)
        pressure_events = [
            pressure_summary,
            {
                "event": "indirect_stall",
                "unit": "0",
                "operation_tick": "100",
                "reason": "row_table_full",
            },
            {
                "event": "indirect_stall",
                "unit": "0",
                "operation_tick": "100",
                "reason": "offset_epoch_full",
            },
        ]
        self.assertEqual(
            MODULE.reconcile_counter_events(pressure_events),
            [pressure_summary],
        )
        second_summary = counter_summary(
            unit="1", operation_tick="200", occurrence="1"
        )
        self.assertEqual(
            MODULE.reconcile_counter_events([second_summary, summary]),
            [summary, second_summary],
        )

    def test_rejects_cross_unit_and_cross_operation_substitution(self):
        for label, event in (
            (
                "unit",
                {
                    "event": "source_issue",
                    "unit": "1",
                    "operation_tick": "100",
                },
            ),
            (
                "operation",
                {
                    "event": "source_issue",
                    "unit": "0",
                    "operation_tick": "101",
                },
            ),
        ):
            summary = counter_summary(source_issues=1)
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    MODULE.AuditError, "same-scope summary"
                ):
                    MODULE.reconcile_counter_events([summary, event])

        for label, other_summary, event in (
            (
                "unit-with-summary",
                counter_summary(unit="1", operation_tick="100"),
                {
                    "event": "source_issue",
                    "unit": "1",
                    "operation_tick": "100",
                },
            ),
            (
                "operation-with-summary",
                counter_summary(unit="0", operation_tick="101"),
                {
                    "event": "source_issue",
                    "unit": "0",
                    "operation_tick": "101",
                },
            ),
        ):
            summary = counter_summary(source_issues=1)
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    MODULE.AuditError, "counter/event mismatch"
                ):
                    MODULE.reconcile_counter_events(
                        [summary, other_summary, event]
                    )

        issue = {
            "sim_tick": 100,
            "line": 1,
            "unit": "0",
            "operation_tick": "100",
            "addr": "0x20",
        }
        for label, response in (
            ("unit", {**issue, "sim_tick": 101, "unit": "1"}),
            (
                "operation",
                {**issue, "sim_tick": 101, "operation_tick": "101"},
            ),
        ):
            with self.subTest(fifo=label):
                with self.assertRaisesRegex(
                    MODULE.AuditError, "same-scope issue"
                ):
                    MODULE.fifo_latencies([issue], [response], "addr")

    def test_stage_cycles_and_sim_ticks_are_separate_and_reconcile(self):
        grouped = {
            "indirect_stage_interval": [
                {
                    "unit": "0",
                    "operation_tick": "100",
                    "stage": "decode",
                    "start": "100",
                    "end": "412",
                    "sim_ticks": "312",
                    "cycles": "1",
                }
            ]
        }
        summary = {
            "unit": "0",
            "operation_tick": "100",
            "decode_sim_ticks": "312",
            "fill_sim_ticks": "0",
            "build_sim_ticks": "0",
            "request_sim_ticks": "0",
            "response_sim_ticks": "0",
            "total_sim_ticks": "312",
        }
        result = MODULE.stage_audit(grouped, [summary])
        self.assertEqual(result["sim_ticks"]["decode"], 312)
        self.assertEqual(result["cycles"]["decode"], 1)

    def test_controller_audit_uses_source_action_names_and_generation(self):
        grouped = {name: [] for name in MODULE.EVENT_FIELDS}
        grouped["transparent_submit"] = [
            {
                "sim_tick": 100,
                "generation": "1",
                "logical": "16384",
                "page": "4096",
                "pages": "4",
            }
        ]
        action_names = {1: "stream_fill", 2: "compute", 3: "stream_store"}
        tick = 100
        for page in range(4):
            for action in range(1, 4):
                tick += 1
                grouped["transparent_issue"].append(
                    {
                        "sim_tick": tick,
                        "generation": "1",
                        "page": str(page),
                        "action": str(action),
                        "action_name": action_names[action],
                        "offset": str(page * 4096),
                        "elements": "4096",
                        "dependency": "controller_order_and_tile_ready",
                    }
                )
                tick += 1
                grouped["transparent_complete"].append(
                    {
                        "sim_tick": tick,
                        "generation": "1",
                        "page": str(page),
                        "action": str(action),
                        "action_name": action_names[action],
                    }
                )
        grouped["transparent_retire"] = [
            {"sim_tick": tick, "generation": "1", "pages": "4"}
        ]
        result = MODULE.controller_audit(grouped, "transparent_4k")
        self.assertEqual(result["action_count"], 12)

        grouped["transparent_complete"][0]["generation"] = "2"
        with self.assertRaisesRegex(MODULE.AuditError, "identity mismatch"):
            MODULE.controller_audit(grouped, "transparent_4k")

    def test_shared_input_semantics_are_bound_to_hashed_artifacts(self):
        (
            pair,
            identity,
            implementation_commit,
            manifest,
        ) = self.make_shared_input_fixture()
        audited = MODULE.audit_shared_pair_input(
            pair, identity, implementation_commit
        )
        self.assertTrue(audited["ramulator_semantics_bound_to_provenance"])

        manifest_path = pair / "input/frozen_input_manifest.json"
        forged_ramulator = json.loads(json.dumps(manifest))
        forged_ramulator["ramulator"]["outer_tree"] = "f" * 40
        manifest_path.write_text(json.dumps(forged_ramulator))
        with self.assertRaisesRegex(MODULE.AuditError, "Ramulator semantics"):
            MODULE.audit_shared_pair_input(
                pair, identity, implementation_commit
            )

        forged_link_claim = json.loads(json.dumps(manifest))
        forged_link_claim["dynamic_link_check"] = "forged semantic claim"
        manifest_path.write_text(json.dumps(forged_link_claim))
        with self.assertRaisesRegex(
            MODULE.AuditError, "dynamic-link semantics"
        ):
            MODULE.audit_shared_pair_input(
                pair, identity, implementation_commit
            )

    def test_checkpoint_identity_requires_complete_duplicate_free_targets(
        self,
    ):
        pair, identity, _, _ = self.make_shared_input_fixture()
        run = pair / "native_direct_16k"
        run.mkdir()
        manifest = run / "shared_checkpoint_files.sha256"
        authoritative = pair / "checkpoint_files.pre_treatment.sha256"
        manifest.write_bytes(authoritative.read_bytes())
        (run / "checkpoint.path").write_text(
            str((pair / "shared_checkpoint").resolve()) + "\n"
        )
        identity_path = run / "shared_checkpoint_identity.sha256"

        def bind_current_manifest():
            identity_path.write_text(
                f"{MODULE.sha256(manifest)}  {manifest.resolve()}\n"
            )

        bind_current_manifest()
        self.assertEqual(MODULE.checkpoint_identity(run), identity)

        lines = authoritative.read_text().splitlines()
        manifest.write_text("\n".join(lines[:-1]) + "\n")
        bind_current_manifest()
        with self.assertRaisesRegex(MODULE.AuditError, "targets missing"):
            MODULE.checkpoint_identity(run)

        manifest.write_text("\n".join(lines + [lines[0]]) + "\n")
        bind_current_manifest()
        with self.assertRaisesRegex(MODULE.AuditError, "duplicate"):
            MODULE.checkpoint_identity(run)

        wrong_hash = "0" * 64 + lines[0][64:]
        manifest.write_text("\n".join([wrong_hash, *lines[1:]]) + "\n")
        bind_current_manifest()
        with self.assertRaisesRegex(MODULE.AuditError, "hash mismatch"):
            MODULE.checkpoint_identity(run)

    def test_run_completion_requires_exact_terminal_marker_counts(self):
        run, result = self.make_completion_fixture()
        audited = MODULE.audit_run_completion(run, result)
        self.assertEqual(
            audited["observed_marker_counts"],
            MODULE.RUN_TERMINAL_MARKER_COUNTS,
        )

        log = run / "restore.log"
        complete_log = log.read_text()
        log.write_text(
            complete_log.replace(
                "Exiting @ tick 42 because m5_exit instruction encountered\n",
                "",
            )
        )
        with self.assertRaisesRegex(MODULE.AuditError, "terminal m5_exit"):
            MODULE.audit_run_completion(run, result)

        log.write_text(
            complete_log
            + "Exiting @ tick 43 because m5_exit instruction encountered\n"
        )
        with self.assertRaisesRegex(MODULE.AuditError, "terminal m5_exit"):
            MODULE.audit_run_completion(run, result)

        log.write_text(complete_log + "ROI Started\n")
        with self.assertRaisesRegex(MODULE.AuditError, "ROI marker"):
            MODULE.audit_run_completion(run, result)

        log.write_text(complete_log + "ROI\n")
        with self.assertRaisesRegex(MODULE.AuditError, "ROI marker"):
            MODULE.audit_run_completion(run, result)

        log.write_text(complete_log + "Exiting @ tick\n")
        with self.assertRaisesRegex(MODULE.AuditError, "terminal m5_exit"):
            MODULE.audit_run_completion(run, result)

    def test_run_completion_binds_log_hash_errors_and_exact_oracle(self):
        run, result = self.make_completion_fixture()
        log = run / "restore.log"
        log.write_text(
            log.read_text().replace(MODULE.EXPECTED_OUTPUT_HASH, "123")
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "run/result correctness mismatch"
        ):
            MODULE.audit_run_completion(run, result)

        log.write_text(
            log.read_text().replace("hash=123 errors=0", "hash=123 errors=1")
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "run/result correctness mismatch"
        ):
            MODULE.audit_run_completion(run, result)

        forged = {**result, "output_hash": "123"}
        with self.assertRaisesRegex(MODULE.AuditError, "exact output oracle"):
            MODULE.audit_run_completion(run, forged)

    def test_ramulator_provenance_binds_one_frozen_library(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        library = root / "libramulator.so"
        library.write_bytes(b"frozen-library")
        provenance = root / "ramulator.json"
        provenance.write_text(
            json.dumps(
                {
                    "schema": "dx100.ramulator_provenance.v1",
                    "outer_tree": "1" * 40,
                    "source_tree": "2" * 40,
                    "nested_gitlinks": {
                        "argparse": "3" * 40,
                        "spdlog": "4" * 40,
                        "yaml-cpp": "5" * 40,
                    },
                    "normalized_dependency_sha256": "6" * 64,
                    "elf_build_id": "abcdef",
                    "frozen_library": {
                        "path": str(library.resolve()),
                        "sha256": MODULE.sha256(library),
                    },
                    "reference_worktree": "/reference",
                }
            )
        )
        result = MODULE.audit_ramulator_provenance(
            provenance,
            {"path": str(library.resolve()), "sha256": MODULE.sha256(library)},
        )
        self.assertEqual(result["elf_build_id"], "abcdef")
        library.write_bytes(b"changed")
        with self.assertRaisesRegex(
            MODULE.AuditError, "frozen library mismatch"
        ):
            MODULE.audit_ramulator_provenance(
                provenance,
                {
                    "path": str(library.resolve()),
                    "sha256": MODULE.sha256(library),
                },
            )

    def test_dynamic_link_audit_normalizes_only_load_addresses(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        frozen = str((root / "libramulator.so").resolve())
        first = root / "first.ldd"
        second = root / "second.ldd"
        first.write_text(
            f"libramulator.so => {frozen} (0xabc0)\n"
            "/lib/libc.so.6 => /lib/libc.so.6 (0xdef0)\n"
        )
        second.write_text(
            f"libramulator.so => {frozen} (0x1110)\n"
            "/lib/libc.so.6 => /lib/libc.so.6 (0x2220)\n"
        )
        a = MODULE.audit_dynamic_links(first, frozen)
        b = MODULE.audit_dynamic_links(second, frozen)
        self.assertNotEqual(a["raw_sha256"], b["raw_sha256"])
        self.assertEqual(a["normalized_sha256"], b["normalized_sha256"])

        wrong = root / "wrong.ldd"
        wrong.write_text(
            "libramulator.so => /mutable/libramulator.so (0x1230)\n"
        )
        with self.assertRaisesRegex(MODULE.AuditError, "resolution mismatch"):
            MODULE.audit_dynamic_links(wrong, frozen)


if __name__ == "__main__":
    unittest.main()
