#!/usr/bin/env python3
"""Add the historical strict-off hybrid to the frozen API matrix.

The accepted feeder-matched successor is immutable authority.  This runner
launches exactly one short restore from its underlying equal-work checkpoint:
``original_hybrid64`` is the historical ``transparent 4096`` treatment with
the 64-line feeder and ``virtual_strict_two_phase`` left at its default-off
value.  Existing arm names are preserved, including ``hybrid64`` for the
strict two-pass treatment.

The selected matched-depth view is native16_f64, native4_f64,
original_hybrid64, and hybrid64.  A successful output is sealed read-only;
``validate`` independently rehashes and reclassifies it.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import (
    Mapping,
    Sequence,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import (  # noqa: E402
    run_hybrid_equal_work_micro_matrix as base,
)
from experiments.scripts import (  # noqa: E402
    run_hybrid_feeder_matched_native_controls as matched,
)

MATCHED_PREDECESSOR = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "hybrid-feeder-matched-native-controls-20260828-20260828-105718-"
    "247e11b9/evidence/hybrid-feeder-matched-native-controls-r1"
)
HISTORICAL_ORIGINAL = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "hybrid-overhead-attribution-20260803-145457-f54ef7d1/"
    "pair_evidence/transparent_4k"
)
MATCHED_HASHES = {
    "artifacts.sha256": (
        "26361a0457f07684542cc993449d8dd26a4881c2fda6692f9b0e6808fe891ae2"
    ),
    "gate.complete": (
        "da432fb4afc0b01929daf33462e6c8ae0f0876bff1b7ef74d61059f0e99eea40"
    ),
    "matrix.tsv": (
        "208e638158fab6b440a02f6d85c60969fd1071283fefd80561ecc1beb65ec748"
    ),
    "result.json": (
        "458bdbc0b6546dab353f07cc5b9588f7caec06c2bdee6ba6a0059392550eec95"
    ),
}
HISTORICAL_HASHES = {
    "invocation.sh.txt": (
        "16f8e096b368f8ff66f23e17e01d274ab6a1e58313f54a1ae74b084cf4ed55a5"
    ),
    "restore.log": (
        "ceed46c5e7a67a02072704f535b503e7fdb08e438fe550455c31d0937106ea99"
    ),
    "result.tsv": (
        "c6325a8bf5c0d05c8f6ddacda2d01a9ff1a3ce968db319910765c3d8fcccb8d3"
    ),
    "run/config.ini": (
        "3ddd86fdbc3fc45bf50850905d3f290a4c032f5ff19dff674957131897be03a4"
    ),
    "run/stats.txt": (
        "188ea3d453e89077d02af00c19b1abec1e2d93ec7d3fdb03161f67877f44460d"
    ),
}
SOURCE_BLOBS = {
    "configs/common/MAAConfig.py": (
        "0110cf5c584dbb4b52154ddfeb736267b2c03a14"
    ),
    "configs/common/Options.py": ("23e12d3a6ddfbab3f089c198df095e354e533e9f"),
    "src/mem/MAA/IndirectAccess.cc": (
        "70c18986046234d706094dae7a09f1d369b8d3b1"
    ),
}

ORIGINAL_ARM = base.ArmSpec(
    "original_hybrid64",
    "transparent",
    4_096,
    16_384,
    4_096,
    64,
    False,
    1,
    4,
    4,
)
ALL_ARM_NAMES = (*matched.ALL_ARM_NAMES, ORIGINAL_ARM.name)
SELECTED_ARM_NAMES = (
    "native16_f64",
    "native4_f64",
    ORIGINAL_ARM.name,
    "hybrid64",
)
STRICT_OPTION = "--maa_virtual_strict_two_phase"
ORIGINAL_ROW_SLICES = 16
ORIGINAL_ROW_ROWS_PER_SLICE = 32
ORIGINAL_ROW_LINE_SLOTS = 8_192
STRICT_EVENTS = (
    "strict_two_phase_begin",
    "strict_two_phase_admission_closed",
    "strict_two_phase_timing",
)
WORK_FIELDS = (
    "simInsts",
    "indirect_ops",
    "stream_writes",
    "scalar_ops",
    "index_words",
)


class MatrixError(RuntimeError):
    """Fail-closed API generation-matrix error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MatrixError(message)


def sha256_file(path: Path) -> str:
    return base.sha256_file(path)


def committed_runner() -> dict[str, str]:
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    committed = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{commit}:{relative}"]
    )
    require(
        committed == Path(__file__).read_bytes(), "runner is not committed"
    )
    status = subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--short"], text=True
    )
    require(not status, "refusing evidence launch from a dirty worktree")
    return {
        "runner_source_commit": commit,
        "runner_sha256": hashlib.sha256(committed).hexdigest(),
    }


def committed_text(commit: str, relative: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{commit}:{relative}"],
        text=True,
    )


def verify_source_contract() -> dict[str, object]:
    commit = base.SIMULATOR_SOURCE_COMMIT
    for relative, expected_blob in SOURCE_BLOBS.items():
        blob = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", f"{commit}:{relative}"],
            text=True,
        ).strip()
        require(
            blob == expected_blob, f"frozen source blob changed: {relative}"
        )

    options = committed_text(commit, "configs/common/Options.py")
    indirect = committed_text(commit, "src/mem/MAA/IndirectAccess.cc")
    config = committed_text(commit, "configs/common/MAAConfig.py")
    fragments = {
        "Options.py store_true": (
            '"--maa_virtual_strict_two_phase",\n'
            '        action="store_true",'
        ),
        "Options.py default-off contract": (
            '"Default-off strict reference: fetch one logical 16K B/index "'
        ),
        "MAAConfig propagation": (
            'opts["virtual_strict_two_phase"] = getattr('
        ),
        "operation predicate": (
            "return maa->virtual_strict_two_phase && isVirtualLoad() &&\n"
            "           isDirectIndexLoad() && !isSoaJitRmw();"
        ),
        "strict admission fence": (
            '"I[%d] strict A build opened before final Row/Offset "'
        ),
        "ordinary pressure drain": (
            '"event=fill_drain unit=%d itr=%d expected=%d "'
        ),
        "ordinary macro signature": (
            '"event=hybrid_producer_macro schema=1 unit=%d "'
        ),
    }
    texts = {
        "Options.py store_true": options,
        "Options.py default-off contract": options,
        "MAAConfig propagation": config,
        "operation predicate": indirect,
        "strict admission fence": indirect,
        "ordinary pressure drain": indirect,
        "ordinary macro signature": indirect,
    }
    for name, fragment in fragments.items():
        require(
            fragment in texts[name], f"missing frozen source contract: {name}"
        )
    return {
        "simulator_source_commit": commit,
        "source_blobs": SOURCE_BLOBS,
        "strict_cli_default": False,
        "strict_scope": ("virtual direct-index loads excluding SoA-JIT RMW"),
        "strict_semantics": (
            "retain all Row/Offset descriptors and fence A issue until "
            "global B/index admission closes"
        ),
        "strict_off_semantics": (
            "ordinary transparent producer with bounded pressure drains "
            "permitted"
        ),
    }


def verify_historical_original() -> dict[str, object]:
    for relative, expected in HISTORICAL_HASHES.items():
        path = HISTORICAL_ORIGINAL / relative
        require(path.is_file(), f"missing historical artifact: {path}")
        require(
            sha256_file(path) == expected,
            f"historical artifact changed: {relative}",
        )
    config = base.parse_config(HISTORICAL_ORIGINAL / "run/config.ini")
    expected_config = {
        "num_tile_elements": "16384",
        "physical_tile_elements": "4096",
        "virtual_index_buffer_lines": "4",
    }
    for key, expected in expected_config.items():
        require(config.get(key) == expected, f"historical config {key}")
    lines = (
        (HISTORICAL_ORIGINAL / "restore.log")
        .read_text(encoding="utf-8", errors="strict")
        .splitlines()
    )
    exact_result = (
        "VIRTUAL_TILE_CONSUMER_RESULT mode=transparent page_elements=4096 "
        f"hash={base.EXPECTED_OUTPUT_HASH} errors=0"
    )
    require(lines.count(exact_result) == 1, "historical exact output")
    require(lines.count("ROI Ended") == 1, "historical ROI close")
    require(
        sum(bool(base.M5_EXIT_RE.fullmatch(line)) for line in lines) == 1,
        "historical m5_exit",
    )
    with (HISTORICAL_ORIGINAL / "result.tsv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    require(len(rows) == 1, "historical result row count")
    row = rows[0]
    expected_row = {
        "case": "transparent_4k",
        "output_hash": base.EXPECTED_OUTPUT_HASH,
        "index_words": "16384",
        "pages_ready": "4",
        "stream_writes": "4",
    }
    for key, expected in expected_row.items():
        require(row.get(key) == expected, f"historical result {key}")
    require(
        int(row["write_issues"]) > 0
        and row["write_issues"] == row["write_completions"],
        "historical retirement closure",
    )
    stats = base.first_stats_section(HISTORICAL_ORIGINAL / "run/stats.txt")
    require(
        base.exact_stat(stats, "simTicks") == int(row["simTicks"]),
        "historical simTicks mismatch",
    )
    require(
        "virtual_strict_two_phase" not in config,
        "historical artifact unexpectedly has the later strict option",
    )
    return {
        "path": str(HISTORICAL_ORIGINAL),
        "case_label": "transparent_4k",
        "treatment": "transparent 4096",
        "strict_option_present": False,
        "virtual_strict_two_phase": False,
        "terminal": True,
        "exact_output": True,
        "artifact_sha256": HISTORICAL_HASHES,
        "performance_comparable_to_frozen_matrix": False,
        "noncomparability_reason": (
            "historical binary/checkpoint and non-treatment hardware differ; "
            "it establishes label/semantics only"
        ),
    }


def verify_matched_predecessor() -> dict[str, object]:
    for relative, expected in MATCHED_HASHES.items():
        path = MATCHED_PREDECESSOR / relative
        require(path.is_file(), f"missing matched predecessor: {path}")
        require(
            sha256_file(path) == expected,
            f"matched predecessor changed: {relative}",
        )
    result = matched.validate(MATCHED_PREDECESSOR)
    require(result.get("decision") == "ACCEPT_ALL_SIX_ARMS", "six-arm gate")
    require(result.get("terminal") is True, "six-arm predecessor terminal")
    return result


def verify_frozen_inputs() -> dict[str, object]:
    authority = matched.verify_predecessor()
    gem5 = matched.PREDECESSOR / "input/gem5.opt"
    ramulator = matched.PREDECESSOR / "input/libramulator.so"
    require(sha256_file(gem5) == base.EXPECTED_GEM5_SHA256, "frozen gem5")
    require(
        sha256_file(ramulator) == base.EXPECTED_RAMULATOR_SHA256,
        "frozen Ramulator",
    )
    environment = os.environ.copy()
    library = str(ramulator.parent.resolve())
    prior = environment.get("LD_LIBRARY_PATH")
    environment["LD_LIBRARY_PATH"] = f"{library}:{prior}" if prior else library
    ldd = subprocess.check_output(
        ["ldd", str(gem5)], env=environment, text=True
    )
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(match is not None, "gem5 does not resolve libramulator.so")
    require(
        Path(match.group(1)).resolve() == ramulator.resolve(),
        "gem5 resolved a different Ramulator library",
    )
    return {
        "runtime_python_sha256": authority["runtime_python_sha256"],
        "gem5_ldd_sha256": hashlib.sha256(ldd.encode()).hexdigest(),
    }


def preflight() -> dict[str, object]:
    predecessor = verify_matched_predecessor()
    require(
        not matched.live_checkpoint_users(),
        "frozen checkpoint has a live restore owner",
    )
    committed = committed_runner()
    source = verify_source_contract()
    historical = verify_historical_original()
    frozen = verify_frozen_inputs()
    require(
        predecessor["gem5_sha256"] == base.EXPECTED_GEM5_SHA256,
        "gem5 authority mismatch",
    )
    return {
        **committed,
        **source,
        "historical_original": historical,
        "gem5_sha256": predecessor["gem5_sha256"],
        "ramulator_sha256": base.EXPECTED_RAMULATOR_SHA256,
        "workload_sha256": predecessor["workload_sha256"],
        "checkpoint_identity": predecessor["checkpoint_identity"],
        "matched_predecessor_result_sha256": MATCHED_HASHES["result.json"],
        "matched_predecessor_ledger_sha256": MATCHED_HASHES[
            "artifacts.sha256"
        ],
        "predecessor_selector_sha256_before": sha256_file(
            matched.PREDECESSOR_SELECTOR
        ),
        "runtime_python_sha256": frozen["runtime_python_sha256"],
        "gem5_ldd_sha256": frozen["gem5_ldd_sha256"],
        "bubblewrap_sha256": sha256_file(matched.BWRAP),
        "selector_isolation": (
            "read-only per-arm bind overlay at the predecessor absolute path"
        ),
    }


def normalized_treatment_command(command: Sequence[str]) -> list[str]:
    return [
        token
        for token in command
        if not token.startswith("--outdir=")
        and not token.startswith("--maa_num_initial_row_table_slices=")
        and not token.startswith("--maa_num_row_table_rows_per_slice=")
        and token != STRICT_OPTION
    ]


def command_for(run: Path) -> list[str]:
    command_path = matched.PREDECESSOR / "arms/hybrid64/command.json"
    prior = json.loads(command_path.read_text())
    require(
        isinstance(prior, list), "strict predecessor command is not a list"
    )
    command = list(prior)
    matched.replace_outdir(command, run)
    matched.set_option(
        command,
        "--maa_num_initial_row_table_slices",
        ORIGINAL_ROW_SLICES,
    )
    matched.set_option(
        command,
        "--maa_num_row_table_rows_per_slice",
        ORIGINAL_ROW_ROWS_PER_SLICE,
    )
    require(command.count(STRICT_OPTION) == 1, "strict option multiplicity")
    command.remove(STRICT_OPTION)
    require(
        normalized_treatment_command(command)
        == normalized_treatment_command(prior),
        "strict-off command changed beyond output, strict flag, and the "
        "declared capacity-equivalent historical RowTable geometry",
    )
    require(
        "--maa_virtual_index_buffer_lines=64" in command,
        "strict-off feeder is not 64 lines",
    )
    require(
        command[0] == str(matched.PREDECESSOR / "input/gem5.opt"),
        "strict-off gem5 changed",
    )
    require(
        f"--checkpoint-dir={matched.PREDECESSOR / 'checkpoint'}" in command,
        "strict-off checkpoint changed",
    )
    require(
        str(matched.PREDECESSOR / "input/workload") in command,
        "strict-off workload changed",
    )
    require(
        command.count(f"deferred {matched.PREDECESSOR_SELECTOR}") == 1,
        "strict-off selector path changed",
    )
    return command


def int_field(event: Mapping[str, str], key: str, arm: str) -> int:
    value = event.get(key, "")
    require(re.fullmatch(r"[0-9]+", value) is not None, f"{arm}: macro {key}")
    return int(value)


def validate_original_macro_order(event: Mapping[str, str], arm: str) -> None:
    b_first = int_field(event, "b_first_issue_tick", arm)
    b_last = int_field(event, "b_last_issue_tick", arm)
    b_response = int_field(event, "b_last_response_tick", arm)
    row_first = int_field(event, "row_offset_first_insert_tick", arm)
    row_last = int_field(event, "row_offset_last_insert_tick", arm)
    a_first = int_field(event, "a_first_issue_tick", arm)
    a_last = int_field(event, "a_last_issue_tick", arm)
    a_response = int_field(event, "a_last_response_tick", arm)
    backing_first = int_field(event, "backing_first_issue_tick", arm)
    backing_last = int_field(event, "backing_last_issue_tick", arm)
    backing_ack = int_field(event, "backing_last_ack_tick", arm)
    page_first = int_field(event, "page_first_ready_tick", arm)
    page_last = int_field(event, "page_last_ready_tick", arm)
    complete = int_field(event, "complete_tick", arm)
    require(0 < b_first <= b_last <= b_response, f"{arm}: B order")
    require(0 < row_first <= row_last, f"{arm}: Row order")
    require(
        0 < a_first <= a_last <= a_response,
        f"{arm}: A issue/response order",
    )
    require(
        a_first <= backing_first <= backing_last <= backing_ack,
        f"{arm}: backing order",
    )
    require(
        0 < page_first <= page_last <= complete and backing_ack <= complete,
        f"{arm}: page/terminal order",
    )
    require(
        a_first < b_response and a_first < row_last,
        f"{arm}: ordinary generation did not overlap A with later B/Row "
        "admission",
    )


def classify_original(root: Path) -> dict[str, object]:
    arm = ORIGINAL_ARM
    arm_root = root / "arms" / arm.name
    require((arm_root / "restore.exit").read_text() == "0\n", "restore rc")
    base.validate_process_record(arm_root / "process.json")
    restore_lines = (
        (arm_root / "restore.log")
        .read_text(encoding="utf-8", errors="strict")
        .splitlines()
    )
    require(
        sum(bool(base.M5_EXIT_RE.fullmatch(line)) for line in restore_lines)
        == 1,
        f"{arm.name}: m5_exit marker",
    )
    require(restore_lines.count("ROI Ended") == 1, f"{arm.name}: ROI close")
    treatment_line = (
        "VIRTUAL_TILE_CONSUMER_TREATMENT mode=transparent "
        "page_elements=4096 source=deferred_file_v1"
    )
    require(
        restore_lines.count(treatment_line) == 1,
        f"{arm.name}: treatment mismatch",
    )
    lowered = "\n".join(restore_lines).lower()
    require(
        not re.search(
            r"panic|fatal|assert|abort|segmentation fault|error:", lowered
        ),
        f"{arm.name}: fatal text",
    )
    matches = [base.RESULT_RE.fullmatch(line) for line in restore_lines]
    matches = [match for match in matches if match is not None]
    require(len(matches) == 1, f"{arm.name}: exact result count")
    result_match = matches[0]
    require(result_match["mode"] == "transparent", f"{arm.name}: result mode")
    require(int(result_match["page"]) == 4096, f"{arm.name}: result page")
    require(
        result_match["hash"] == base.EXPECTED_OUTPUT_HASH,
        f"{arm.name}: output hash",
    )

    config = base.parse_config(arm_root / "run/config.ini")
    expected_config = {
        "num_tile_elements": "16384",
        "physical_tile_elements": "4096",
        "num_initial_row_table_slices": str(ORIGINAL_ROW_SLICES),
        "num_row_table_rows_per_slice": str(ORIGINAL_ROW_ROWS_PER_SLICE),
        "num_row_table_entries_per_subslice_row": "8",
        "num_offset_table_entries": "16384",
        "num_offset_table_epoch_entries": "16384",
        "virtual_index_buffer_lines": "64",
        "virtual_masked_writes": "true",
        "virtual_strict_two_phase": "false",
        "virtual_index_partitions": "1",
        "virtual_index_range_passes": "false",
        "virtual_index_descriptor_spool": "false",
        "virtual_descriptor_spool_read_ahead": "false",
        "virtual_bounded_global_merge": "false",
        "virtual_idealized_write_ack": "false",
        "virtual_native_issue_order": "false",
        "virtual_combine_slots": "16",
        "virtual_combine_words": "0",
        "virtual_combine_ways": "0",
        "virtual_combine_banks": "0",
        "virtual_response_slots": "8",
        "virtual_response_word_pool": "0",
        "virtual_words_per_cycle": "1",
        "virtual_max_outstanding_writes": "32",
        "no_reorder": "false",
        "reconfigure_row_table": "false",
    }
    for key, expected in expected_config.items():
        require(config.get(key) == expected, f"{arm.name}: config {key}")

    stats = base.first_stats_section(arm_root / "run/stats.txt")
    counters = {
        "simTicks": base.exact_stat(stats, "simTicks"),
        "simInsts": base.exact_stat(stats, "simInsts"),
        "indirect_ops": base.exact_stat(stats, "system.maa.numInst_INDRD"),
        "stream_writes": base.exact_stat(stats, "system.maa.numInst_STRWR"),
        "scalar_ops": base.exact_stat(stats, "system.maa.numInst_ALUS"),
        "index_words": base.summed_stat(stats, "IND_VirtIndexWords"),
        "index_hwm": base.summed_stat(stats, "IND_VirtIndexWordHighWater"),
        "index_line_hwm": base.summed_stat(
            stats, "IND_VirtIndexLineHighWater"
        ),
        "write_issues": base.summed_stat(stats, "IND_VirtWriteIssues"),
        "write_completions": base.summed_stat(
            stats, "IND_VirtWriteCompletions"
        ),
        "full_writes": base.summed_stat(stats, "IND_VirtFullLineWrites"),
        "partial_writes": base.summed_stat(stats, "IND_VirtPartialWrites"),
        "strict_operations": base.summed_stat(
            stats, "IND_StrictTwoPhaseOperations"
        ),
        "strict_b_fetch_lines": base.summed_stat(
            stats, "IND_StrictTwoPhaseBFetchLines"
        ),
        "strict_descriptors": base.summed_stat(
            stats, "IND_StrictTwoPhaseDescriptors"
        ),
        "strict_a_issues": base.summed_stat(
            stats, "IND_StrictTwoPhaseAIssues"
        ),
        "strict_backing_issues": base.summed_stat(
            stats, "IND_StrictTwoPhaseBackingIssues"
        ),
        "strict_pages_ready": base.summed_stat(
            stats, "IND_StrictTwoPhasePagesReady"
        ),
        "offset_epoch_drains": base.summed_stat(stats, "IND_NumOTEpochDrain"),
    }
    expected_counters = {
        "indirect_ops": 1,
        "stream_writes": 4,
        "scalar_ops": 4,
        "index_words": 16_384,
        "strict_operations": 0,
        "strict_b_fetch_lines": 0,
        "strict_descriptors": 0,
        "strict_a_issues": 0,
        "strict_backing_issues": 0,
        "strict_pages_ready": 0,
        "offset_epoch_drains": 0,
    }
    for key, expected in expected_counters.items():
        require(counters[key] == expected, f"{arm.name}: counter {key}")
    require(counters["simInsts"] > 0, f"{arm.name}: empty guest work")
    require(
        0 < counters["index_line_hwm"] <= 64,
        f"{arm.name}: line feeder bound",
    )
    require(
        0 < counters["index_hwm"] <= 64 * base.WORDS_PER_INDEX_LINE,
        f"{arm.name}: word feeder bound",
    )
    base.validate_masked_retirement(counters, arm.name)

    trace_path = arm_root / "run/hybrid_trace.log"
    trace_lines = trace_path.read_text(
        encoding="utf-8", errors="strict"
    ).splitlines()
    for event in STRICT_EVENTS:
        require(
            all(base.parse_event(line, event) is None for line in trace_lines),
            f"{arm.name}: unexpected {event}",
        )
    macro = base.exactly_one_event(trace_lines, "hybrid_producer_macro")
    expected_macro = {
        "schema": "1",
        "generation": "1",
        "b_lines": "1025",
        "b_bytes": "65600",
        "b_retries": "0",
        "b_queue_high_water": "64",
        "offset_pressure_events": "0",
        "pages_ready": "4",
        "backing_semantic_bytes": "131072",
    }
    for key, expected in expected_macro.items():
        require(macro.get(key) == expected, f"{arm.name}: macro {key}")
    require(
        int_field(macro, "row_insert_attempts", arm.name) >= 16_384,
        f"{arm.name}: incomplete row attempts",
    )
    require(
        int_field(macro, "row_offset_insertions", arm.name) == 16_384,
        f"{arm.name}: incomplete row insertions",
    )
    row_pressure_events = int_field(macro, "row_pressure_events", arm.name)
    require(
        row_pressure_events > 0,
        f"{arm.name}: macro RowTable pressure inactive",
    )
    a_lines = int_field(macro, "a_lines", arm.name)
    a_bytes = int_field(macro, "a_bytes", arm.name)
    require(
        0 < a_lines <= base.TOTAL_ELEMENTS and a_bytes == a_lines * 64,
        f"{arm.name}: A source line/byte accounting",
    )
    require(
        int_field(macro, "backing_line_issues", arm.name)
        == counters["write_issues"],
        f"{arm.name}: macro backing issues",
    )
    require(
        int_field(macro, "backing_transport_bytes", arm.name)
        == counters["write_issues"] * 64,
        f"{arm.name}: macro transport bytes",
    )
    validate_original_macro_order(macro, arm.name)
    fill_drains = sum(
        base.parse_event(line, "fill_drain") is not None
        for line in trace_lines
    )
    require(fill_drains > 0, f"{arm.name}: ordinary pressure drain inactive")
    counters["row_pressure_events"] = row_pressure_events
    counters["fill_drains"] = fill_drains
    counters["a_lines"] = a_lines
    return {
        "name": arm.name,
        "classification": "ACCEPT",
        "reason": (
            "terminal, exact output/work, strict-off absence, bounded "
            "hardware, and macro order gates pass"
        ),
        "spec": asdict(arm),
        "output_hash": result_match["hash"],
        "counters": counters,
        "strict_trace": None,
        "strict_admission": None,
        "macro_trace": macro,
        "fill_drains": fill_drains,
        "command_sha256": sha256_file(arm_root / "command.json"),
        "config_sha256": sha256_file(arm_root / "run/config.ini"),
        "restore_log_sha256": sha256_file(arm_root / "restore.log"),
        "stats_sha256": sha256_file(arm_root / "run/stats.txt"),
        "trace_sha256": sha256_file(trace_path),
    }


def comparison(
    reference: str,
    candidate: str,
    arms: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    return matched.comparison(reference, candidate, arms)


def classify_matrix(root: Path) -> dict[str, object]:
    predecessor = verify_matched_predecessor()
    source = verify_source_contract()
    historical = verify_historical_original()
    manifest = json.loads((root / "manifest.json").read_text())
    require(
        manifest.get("schema") == "dx100.api_hybrid_generation_matrix.v1",
        "manifest schema",
    )
    for key in ("gem5_sha256", "workload_sha256", "checkpoint_identity"):
        require(manifest.get(key) == predecessor.get(key), f"manifest {key}")
    require(
        manifest.get("matched_predecessor_result_sha256")
        == MATCHED_HASHES["result.json"],
        "manifest predecessor hash",
    )
    require(
        manifest.get("new_restores_launched") == 1
        and manifest.get("accepted_arms_rerun") == 0
        and manifest.get("full_application_runs") == 0,
        "manifest launch scope",
    )
    require(
        manifest.get("new_arm") == asdict(ORIGINAL_ARM),
        "manifest new arm",
    )
    require(manifest.get("source_blobs") == source["source_blobs"], "source")
    require(
        manifest.get("historical_original") == historical,
        "historical original authority",
    )
    require(
        manifest.get("original_row_table_slices") == ORIGINAL_ROW_SLICES
        and manifest.get("original_row_table_rows_per_slice")
        == ORIGINAL_ROW_ROWS_PER_SLICE
        and manifest.get("original_row_line_slots") == ORIGINAL_ROW_LINE_SLOTS,
        "original bounded geometry",
    )

    arm_root = root / "arms" / ORIGINAL_ARM.name
    arm_manifest = json.loads((arm_root / "arm.json").read_text())
    require(arm_manifest.get("spec") == asdict(ORIGINAL_ARM), "arm spec")
    require(
        arm_manifest.get("workload_sha256") == predecessor["workload_sha256"],
        "arm workload identity",
    )
    require(
        arm_manifest.get("checkpoint_identity")
        == predecessor["checkpoint_identity"],
        "arm checkpoint identity",
    )
    strict_treatment = matched.PREDECESSOR / "arms/hybrid64/treatment.txt"
    require(
        arm_manifest.get("treatment_sha256") == sha256_file(strict_treatment),
        "arm treatment identity",
    )
    command = json.loads((arm_root / "command.json").read_text())
    expected_command = command_for(arm_root / "run")
    require(command == expected_command, "strict-off command changed")
    wrapper = json.loads((arm_root / "wrapped_command.json").read_text())
    require(
        wrapper
        == matched.wrapped_command(root, arm_root / "treatment.txt", command),
        "selector isolation changed",
    )
    process = json.loads((arm_root / "process.json").read_text())
    wrapper_hash = hashlib.sha256(
        json.dumps(wrapper, separators=(",", ":")).encode()
    ).hexdigest()
    require(
        process.get("command_sha256") == wrapper_hash,
        "process command identity",
    )

    original = classify_original(root)
    arms: dict[str, dict[str, object]] = copy.deepcopy(predecessor["arms"])
    arms[ORIGINAL_ARM.name] = original
    require(tuple(arms) == ALL_ARM_NAMES, "seven-arm join changed")
    require(
        len({str(item["output_hash"]) for item in arms.values()}) == 1,
        "joined output hashes differ",
    )
    strict = arms["hybrid64"]
    for field in WORK_FIELDS:
        require(
            original["counters"][field] == strict["counters"][field],
            f"strict-off/strict work differs: {field}",
        )
    strict_trace = strict.get("strict_trace")
    require(isinstance(strict_trace, dict), "hybrid64 strict trace absent")
    require(
        strict["spec"]["strict"] is True
        and strict["counters"]["strict_operations"] == 1
        and strict_trace.get("order_ok") == "1"
        and strict_trace.get("terminal") == "1",
        "hybrid64 is not accepted strict two-pass",
    )
    require(
        original["spec"]["strict"] is False
        and original["counters"]["strict_operations"] == 0
        and original["strict_trace"] is None,
        "original hybrid is not strict-off",
    )
    require(
        original["counters"]["a_lines"]
        >= strict["counters"]["strict_a_issues"],
        "ordinary generations unexpectedly reduce A source-line work",
    )

    comparisons = {
        "original_hybrid64_vs_native16_f64": comparison(
            "native16_f64", ORIGINAL_ARM.name, arms
        ),
        "original_hybrid64_vs_native4_f64": comparison(
            "native4_f64", ORIGINAL_ARM.name, arms
        ),
        "hybrid64_vs_original_hybrid64": comparison(
            ORIGINAL_ARM.name, "hybrid64", arms
        ),
        "hybrid64_vs_native16_f64": comparison(
            "native16_f64", "hybrid64", arms
        ),
        "hybrid64_vs_native4_f64": comparison("native4_f64", "hybrid64", arms),
    }
    return {
        "schema": "dx100.api_hybrid_generation_matrix.result.v1",
        "terminal": True,
        "decision": "ACCEPT_MATCHED_FOUR_ARM_MATRIX",
        "performance_metric": "simTicks",
        "repetitions_per_arm": 1,
        "same_binary": True,
        "same_guest": True,
        "same_checkpoint_input": True,
        "matched_feeder_lines": 64,
        "selected_matrix_arms": list(SELECTED_ARM_NAMES),
        "workload_sha256": predecessor["workload_sha256"],
        "gem5_sha256": predecessor["gem5_sha256"],
        "checkpoint_identity": predecessor["checkpoint_identity"],
        "matched_predecessor_result_sha256": MATCHED_HASHES["result.json"],
        "arms": arms,
        "comparisons": comparisons,
        "ordinary_a_line_amplification_vs_strict": (
            original["counters"]["a_lines"]
            / strict["counters"]["strict_a_issues"]
        ),
        "mechanism_decision": {
            "hybrid1_is_strict_two_pass": True,
            "hybrid64_is_strict_two_pass": True,
            "historical_transparent_4k_is_strict_off": True,
            "original_hybrid64_is_strict_off": True,
            "arm_labels_preserved": True,
            "strict_off_positive_activation": True,
            "strict_off_overlaps_a_with_later_b_row_admission": True,
            "strict_off_pressure_drains_observed": original["fill_drains"],
        },
        "limitations": [
            "one deterministic gem5 observation per arm",
            "microbenchmark evidence only; no full application was launched",
            "speed comparisons apply only to the exact frozen binary/config",
            "native4_f64 is four 4K operations in the shared T16K logical "
            "aperture, not a true T4096/API-aperture run",
            "original_hybrid64 uses an 8,192-line RowTable capacity equivalent "
            "to the historical one-channel geometry while preserving the "
            "matrix's two channels; the strict arm uses 16,384 lines, so the "
            "performance comparison is arm-level, not an isolated flag A/B",
            "feeder and storage bounds are not synthesized area/power/Fmax "
            "evidence",
        ],
    }


def write_matrix(root: Path, result: Mapping[str, object]) -> None:
    fields = (
        "strict",
        "simTicks",
        "simInsts",
        "indirect_ops",
        "stream_writes",
        "scalar_ops",
        "index_words",
        "index_hwm",
        "write_issues",
        "write_completions",
        "strict_operations",
        "offset_epoch_drains",
        "row_pressure_events",
        "fill_drains",
    )
    with (root / "matrix.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(("arm", "classification", "output_hash", *fields))
        for name in SELECTED_ARM_NAMES:
            arm = result["arms"][name]
            counters = arm["counters"]
            values = {
                "strict": str(arm["spec"]["strict"]).lower(),
                **counters,
            }
            writer.writerow(
                (
                    name,
                    arm["classification"],
                    arm["output_hash"],
                    *(values.get(field, 0) for field in fields),
                )
            )


def successor_artifacts(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path != root / "artifacts.sha256"
    )


def write_ledger(root: Path) -> None:
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root)}"
        for path in successor_artifacts(root)
    ]
    (root / "artifacts.sha256").write_text("\n".join(lines) + "\n")


def validate_ledger(root: Path) -> None:
    ledger = root / "artifacts.sha256"
    require(ledger.is_file(), "missing artifact ledger")
    seen: set[str] = set()
    for line in ledger.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        require(
            re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "bad digest"
        )
        require(relative not in seen, f"duplicate ledger path: {relative}")
        seen.add(relative)
        require(
            sha256_file(root / relative) == digest,
            f"artifact changed: {relative}",
        )
    actual = {
        str(path.relative_to(root)) for path in successor_artifacts(root)
    }
    require(seen == actual, "artifact set changed")


def make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.chmod(path.stat().st_mode & ~0o222)
    directories = (path for path in root.rglob("*") if path.is_dir())
    for path in sorted(directories, reverse=True):
        path.chmod(path.stat().st_mode & ~0o222)
    root.chmod(root.stat().st_mode & ~0o222)


def validate_read_only(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        require(path.stat().st_mode & 0o222 == 0, f"writable output: {path}")


def execute(root: Path) -> dict[str, object]:
    require(not root.exists(), f"refusing to overwrite output: {root}")
    authority = preflight()
    root.mkdir(parents=True)
    (root / "arms").mkdir()
    manifest = {
        "schema": "dx100.api_hybrid_generation_matrix.v1",
        **authority,
        "matched_predecessor_root": str(MATCHED_PREDECESSOR),
        "checkpoint_predecessor_root": str(matched.PREDECESSOR),
        "new_arm": asdict(ORIGINAL_ARM),
        "original_row_table_slices": ORIGINAL_ROW_SLICES,
        "original_row_table_rows_per_slice": ORIGINAL_ROW_ROWS_PER_SLICE,
        "original_row_line_slots": ORIGINAL_ROW_LINE_SLOTS,
        "selected_matrix_arms": list(SELECTED_ARM_NAMES),
        "new_restores_launched": 1,
        "accepted_arms_rerun": 0,
        "full_application_runs": 0,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    environment = os.environ.copy()
    library = str((matched.PREDECESSOR / "input").resolve())
    prior = environment.get("LD_LIBRARY_PATH")
    environment["LD_LIBRARY_PATH"] = f"{library}:{prior}" if prior else library
    environment["OMP_PROC_BIND"] = "false"
    environment["OMP_NUM_THREADS"] = "4"
    try:
        arm_root = root / "arms" / ORIGINAL_ARM.name
        arm_root.mkdir()
        treatment = arm_root / "treatment.txt"
        treatment.write_text(ORIGINAL_ARM.treatment)
        command = command_for(arm_root / "run")
        wrapper = matched.wrapped_command(root, treatment, command)
        (arm_root / "command.json").write_text(
            json.dumps(command, indent=2) + "\n"
        )
        (arm_root / "wrapped_command.json").write_text(
            json.dumps(wrapper, indent=2) + "\n"
        )
        (arm_root / "arm.json").write_text(
            json.dumps(
                {
                    "name": ORIGINAL_ARM.name,
                    "spec": asdict(ORIGINAL_ARM),
                    "workload_sha256": authority["workload_sha256"],
                    "checkpoint_identity": authority["checkpoint_identity"],
                    "treatment_sha256": sha256_file(treatment),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        returncode = base.run_command(
            wrapper,
            arm_root / "restore.log",
            environment,
            arm_root / "process.json",
        )
        (arm_root / "restore.exit").write_text(f"{returncode}\n")
        require(returncode == 0, f"restore exited {returncode}")
        post = matched.verify_predecessor()
        require(
            post["selector_sha256"]
            == authority["predecessor_selector_sha256_before"],
            "predecessor selector changed",
        )
        result = classify_matrix(root)
        (root / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
        write_matrix(root, result)
        (root / "gate.complete").write_text(
            "ACCEPT_MATCHED_FOUR_ARM_MATRIX\n"
            "same_binary=true\n"
            "same_guest=true\n"
            "same_checkpoint_input=true\n"
            "matched_feeder_lines=64\n"
            "new_restores_launched=1\n"
            "accepted_arms_rerun=0\n"
            "full_application_runs=0\n"
            "performance_metric=simTicks\n"
        )
        write_ledger(root)
        validate_ledger(root)
        recomputed = classify_matrix(root)
        require(recomputed == result, "pre-seal classification changed")
        make_read_only(root)
        validate(root)
        return result
    except BaseException as error:
        if root.stat().st_mode & 0o222:
            (root / "matrix.failed").write_text("failed\n")
            (root / "failure.json").write_text(
                json.dumps(
                    {
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        raise


def validate(root: Path) -> dict[str, object]:
    validate_read_only(root)
    validate_ledger(root)
    recomputed = classify_matrix(root)
    sealed = json.loads((root / "result.json").read_text())
    require(recomputed == sealed, "sealed result differs from classification")
    require(
        (root / "gate.complete").read_text().splitlines()[0]
        == "ACCEPT_MATCHED_FOUR_ARM_MATRIX",
        "gate changed",
    )
    return recomputed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    run_parser = subparsers.add_parser(
        "run", help="launch only the new strict-off API restore"
    )
    run_parser.add_argument("out", type=Path)
    validate_parser = subparsers.add_parser(
        "validate", help="read-only validation of the sealed matrix"
    )
    validate_parser.add_argument("out", type=Path)
    subparsers.add_parser("preflight", help="validate frozen authority")
    args = parser.parse_args(argv)
    try:
        if args.action == "run":
            result = execute(args.out.resolve())
        elif args.action == "validate":
            result = validate(args.out.resolve())
        else:
            result = preflight()
    except (
        MatrixError,
        matched.SuccessorError,
        base.MatrixError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.action == "preflight":
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result["comparisons"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
