#!/usr/bin/env python3
"""Fail-closed grounding for paired dx100 physical-admission JSONL.

Only ``dx100.physical_admission.v1`` records cross this boundary.  The
schema-v1 nonphysical attribution events from the containing run are rejected
evidence and are neither parsed nor used here.  Treatment metadata is checked
within each input but deliberately excluded from the pair semantic comparison.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from bounded_row_model import (
    ACTIVE_ELEMENTS,
    LOGICAL_ELEMENTS,
    NUM_SLICES,
    PARTITIONS,
    ApertureGeometry,
    Model,
    PhysicalRecord,
)

PHYSICAL_SCHEMA = "dx100.physical_admission.v1"
GROUNDING_SCHEMA = "dx100.bounded_row_physical_grounding.v1"
INVENTORY_SCHEMA = "dx100.bounded_row_evidence_inventory.v1"
REJECTION_SCHEMA = "dx100.rejected_attribution_attempt.v1"
REJECTION_REASON = (
    "Attribution event schema 1 lacked source-generated occurrence identity; "
    "consecutive indirect_execute sequences 721 and 722 emitted "
    "indistinguishable row_table_full stall records at the same simTick."
)
EXPECTED_RECORDS = LOGICAL_ELEMENTS
SOURCE_ELEMENTS = LOGICAL_ELEMENTS * 8
EXPECTED_FIELD_COUNT = 33
DDR4_MODELED_ADDRESS_BITS = 33
DDR4_MODELED_ADDRESS_LIMIT = 1 << DDR4_MODELED_ADDRESS_BITS
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# This is the source emission order in IndirectAccess.cc.  It is needed to
# reproduce physical_validation.json:record_sha256 from the sorted JSONL.
PHYSICAL_FIELD_ORDER = (
    "schema",
    "event",
    "itr",
    "b_paddr",
    "b_value",
    "a_paddr",
    "a_line_paddr",
    "channel",
    "rank",
    "bank_group",
    "bank",
    "row",
    "column",
    "native_slice",
    "grow_addr",
    "wid",
    "generation_available",
    "generation",
    "opcode",
    "optype",
    "if_id",
    "cid",
    "pc",
    "operation_tick",
    "controller_managed",
    "controller_action",
    "controller_transaction",
    "controller_page",
    "rt_config",
    "aperture_slice_begin",
    "aperture_slice_end",
    "aperture_slices",
    "provenance",
)
PHYSICAL_FIELDS = frozenset(PHYSICAL_FIELD_ORDER)
JSONL_FIELDS = PHYSICAL_FIELDS | {"sim_tick", "trace_line"}

# These fields alone define the treatment-independent physical semantics.
SEMANTIC_FIELDS = (
    "itr",
    "b_value",
    "b_paddr",
    "a_paddr",
    "a_line_paddr",
    "channel",
    "rank",
    "bank_group",
    "bank",
    "row",
    "column",
    "native_slice",
    "grow_addr",
    "wid",
)
EXCLUDED_TREATMENT_FIELDS = tuple(sorted(JSONL_FIELDS - set(SEMANTIC_FIELDS)))
INTEGER_FIELDS = PHYSICAL_FIELDS - {"schema", "event", "provenance"}

VALIDATION_KEYS = {
    "aperture",
    "field_count",
    "generation",
    "operation_ticks",
    "record_count",
    "record_sha256",
    "records",
    "schema",
    "trace_path",
    "trace_sha256",
}
REJECTION_KEYS = {
    "gem5_sha256",
    "implementation_commit",
    "physical_admission_schema",
    "physical_records_preserved",
    "publication_allowed",
    "reason",
    "schema",
    "status",
}

SOURCE_SNAPSHOT_FILES = (
    "CpuSidePort.cc",
    "IF.cc",
    "IF.hh",
    "IndirectAccess.cc",
    "IndirectAccess.hh",
    "MAA.cc",
    "MAA.hh",
    "MAA_gem5.hpp",
    "TransparentSPDController.hh",
    "hybrid_overhead_attribution.py",
    "ramulator.yaml",
    "run_virtual_tile_consumer_case.sh",
    "se.py",
    "test_virtual_tile_consumer.cpp",
)
COMMON_CASE_FILES = {
    "artifact_sha256.txt",
    "checkpoint.exit",
    "checkpoint.path",
    "gem5.ldd.txt",
    "invocation.sh.txt",
    "manifest.txt",
    "physical_admission_records.jsonl",
    "physical_validation.json",
    "restore.exit",
    "restore.log",
    "result.tsv",
    "run/citations.bib",
    "run/config.dot",
    "run/config.dot.pdf",
    "run/config.dot.svg",
    "run/config.ini",
    "run/config.json",
    "run/fs/proc/cpuinfo",
    "run/fs/proc/stat",
    "run/fs/sys/devices/system/cpu/online",
    "run/fs/sys/devices/system/cpu/possible",
    "run/stats.txt",
    "run/virtual_trace.log",
    "shared_checkpoint_files.sha256",
    "shared_checkpoint_identity.sha256",
    "source.diff",
    "source_status.txt",
    "treatment.txt",
    "virtual_tile_consumer_case.pass",
} | {f"source_snapshot/{name}" for name in SOURCE_SNAPSHOT_FILES}
CASE_ONLY_FILES = {
    "native_direct_16k": set(),
    "transparent_4k": {"page_readiness.tsv", "transparent_issue_order.tsv"},
}


class GroundingError(ValueError):
    """The physical evidence did not satisfy the complete contract."""


def fail(message: str) -> None:
    raise GroundingError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def strict_int(value: object, name: str) -> int:
    if type(value) is not str or not value:
        fail(f"{name} must be a nonempty integer string")
    try:
        return int(value, 0)
    except ValueError as exc:
        raise GroundingError(f"{name} is not an integer: {value!r}") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GroundingError(f"cannot parse {path}: {exc}") from exc
    if type(value) is not dict:
        fail(f"{path} must contain one JSON object")
    return value


def _require_exact_keys(
    value: object, expected: set[str], name: str
) -> dict[str, object]:
    if type(value) is not dict:
        fail(f"{name} must be an object")
    actual = set(value)
    if actual != expected:
        fail(
            f"{name} fields missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _path_has_case_suffix(recorded: object, case: str, suffix: str) -> bool:
    if type(recorded) is not str:
        return False
    normalized = recorded.replace("\\", "/")
    return normalized.endswith(f"/{case}/{suffix}")


def validate_rejection_contract(
    path: Path, *, source_commit: str, gem5_hash: str
) -> str:
    if not path.is_file() or path.is_symlink():
        fail("mandatory rejection.json is missing or a symlink")
    rejection = _require_exact_keys(
        _read_json_object(path), REJECTION_KEYS, "rejection.json"
    )
    if (
        rejection["schema"] != REJECTION_SCHEMA
        or rejection["status"] != "rejected"
        or rejection["reason"] != REJECTION_REASON
        or rejection["publication_allowed"] is not False
        or rejection["physical_admission_schema"] != PHYSICAL_SCHEMA
        or rejection["physical_records_preserved"] is not True
        or rejection["implementation_commit"] != source_commit
        or rejection["gem5_sha256"] != gem5_hash
    ):
        fail("rejection/physical-preservation boundary is inconsistent")
    return sha256(path)


@dataclass(frozen=True)
class ValidatedCase:
    name: str
    records_path: Path
    validation_path: Path
    records: tuple[PhysicalRecord, ...]
    semantics: tuple[tuple[int, ...], ...]
    semantic_sha256: str
    records_sha256: str
    source_record_sha256: str
    trace_sha256: str
    validation_sha256: str
    provenance: dict[str, object] | None = None


def validate_records(
    records_path: Path,
    validation_path: Path,
    *,
    expected_count: int = EXPECTED_RECORDS,
    case_name: str | None = None,
) -> ValidatedCase:
    """Validate the deterministic JSONL and its physical validation envelope."""
    records_path = records_path.resolve()
    validation_path = validation_path.resolve()
    if not records_path.is_file() or not validation_path.is_file():
        fail("physical records or validation JSON is missing")
    case = case_name or records_path.parent.name
    validation = _require_exact_keys(
        _read_json_object(validation_path), VALIDATION_KEYS, "validation"
    )
    if validation["schema"] != PHYSICAL_SCHEMA:
        fail("validation has the wrong physical schema")
    if type(validation["field_count"]) is not int or (
        validation["field_count"] != EXPECTED_FIELD_COUNT
    ):
        fail("validation field_count is not exactly 33")
    if type(validation["record_count"]) is not int or (
        validation["record_count"] != expected_count
    ):
        fail("validation record_count is not exact")
    for key in ("record_sha256", "trace_sha256"):
        if type(validation[key]) is not str or not HEX64_RE.fullmatch(
            validation[key]
        ):
            fail(f"validation {key} is malformed")

    aperture = _require_exact_keys(
        validation["aperture"],
        {"slice_begin", "slice_end", "slices"},
        "validation aperture",
    )
    if aperture != {
        "slice_begin": 0,
        "slice_end": NUM_SLICES,
        "slices": NUM_SLICES,
    }:
        fail("validation aperture is not exactly native slices [0,16)")
    generation = _require_exact_keys(
        validation["generation"],
        {
            "available_records",
            "unavailable_records",
            "unavailable_is_explicit",
        },
        "validation generation",
    )
    if (
        type(generation["available_records"]) is not int
        or type(generation["unavailable_records"]) is not int
        or generation["available_records"] + generation["unavailable_records"]
        != expected_count
        or generation["unavailable_is_explicit"] is not True
    ):
        fail("validation generation accounting is inconsistent")
    metadata = _require_exact_keys(
        validation["records"],
        {"format", "order", "path", "record_count", "sha256"},
        "validation records",
    )
    if (
        metadata["format"] != "jsonl"
        or metadata["order"] != "logical_itr_ascending"
        or metadata["record_count"] != expected_count
        or not _path_has_case_suffix(
            metadata["path"], case, "physical_admission_records.jsonl"
        )
    ):
        fail("validation records provenance is inconsistent")
    if not _path_has_case_suffix(
        validation["trace_path"], case, "run/virtual_trace.log"
    ):
        fail("validation trace provenance is inconsistent")
    if type(metadata["sha256"]) is not str or not HEX64_RE.fullmatch(
        metadata["sha256"]
    ):
        fail("validation records sha256 is malformed")
    actual_records_sha = sha256(records_path)
    if metadata["sha256"] != actual_records_sha:
        fail("records SHA-256 disagrees with validation")

    operation_ticks = validation["operation_ticks"]
    if (
        type(operation_ticks) is not list
        or not operation_ticks
        or any(type(tick) is not int or tick < 0 for tick in operation_ticks)
        or operation_ticks != sorted(set(operation_ticks))
    ):
        fail("validation operation_ticks is malformed")

    physical_records: list[PhysicalRecord] = []
    semantic_rows: list[tuple[int, ...]] = []
    payload_digest = hashlib.sha256()
    semantic_digest = hashlib.sha256()
    seen_itrs: set[int] = set()
    observed_ticks: set[int] = set()
    generation_counts = {0: 0, 1: 0}
    prior_trace_line = -1

    try:
        stream = records_path.open(encoding="utf-8", errors="strict")
    except OSError as exc:
        raise GroundingError(f"cannot open {records_path}: {exc}") from exc
    with stream:
        for line_index, raw in enumerate(stream):
            if not raw.endswith("\n"):
                fail(f"JSONL line {line_index + 1} lacks a newline")
            encoded = raw[:-1]
            try:
                record = json.loads(encoded, object_pairs_hook=_unique_object)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise GroundingError(
                    f"JSONL line {line_index + 1} is malformed: {exc}"
                ) from exc
            if type(record) is not dict:
                fail(f"JSONL line {line_index + 1} is not an object")
            if set(record) != JSONL_FIELDS:
                fail(
                    f"JSONL line {line_index + 1} does not contain exactly "
                    "33 schema fields plus trace_line/sim_tick"
                )
            if encoded != json.dumps(
                record, sort_keys=True, separators=(",", ":")
            ):
                fail(f"JSONL line {line_index + 1} is not canonical/sorted")
            if record["schema"] != PHYSICAL_SCHEMA or (
                record["event"] != "physical_admission"
            ):
                fail(f"JSONL line {line_index + 1} has wrong schema/event")
            if record["provenance"] != "direct_index_descriptor_admission":
                fail(f"JSONL line {line_index + 1} has wrong provenance")
            if type(record["sim_tick"]) is not int or record["sim_tick"] < 0:
                fail("sim_tick must be a nonnegative integer")
            if (
                type(record["trace_line"]) is not int
                or record["trace_line"] <= prior_trace_line
            ):
                fail("trace_line must be a strictly increasing integer")
            prior_trace_line = record["trace_line"]

            values = {
                name: strict_int(record[name], name) for name in INTEGER_FIELDS
            }
            itr = values["itr"]
            if (
                itr != line_index
                or itr in seen_itrs
                or not 0 <= itr < expected_count
            ):
                fail(
                    "itr is missing, duplicated, out of range, or out of order"
                )
            seen_itrs.add(itr)
            if (
                values["aperture_slice_begin"],
                values["aperture_slice_end"],
                values["aperture_slices"],
            ) != (0, NUM_SLICES, NUM_SLICES):
                fail("record aperture is not exactly native slices [0,16)")

            available = values["generation_available"]
            generation_value = values["generation"]
            if (
                available not in (0, 1)
                or (available == 0 and generation_value != 0)
                or (available == 1 and generation_value == 0)
            ):
                fail("record generation availability is inconsistent")
            generation_counts[available] += 1
            observed_ticks.add(values["operation_tick"])

            b_paddr = values["b_paddr"]
            b_value = values["b_value"]
            a_paddr = values["a_paddr"]
            a_line = values["a_line_paddr"]
            if not 0 <= b_value < SOURCE_ELEMENTS:
                fail("B value is outside the workload source array")
            if b_paddr < 0 or b_paddr % 4:
                fail("B physical address is not four-byte aligned")
            # Do not infer physical contiguity across translated virtual pages.
            # The semantic pair comparison binds B value/paddr and A paddr
            # exactly; only relationships exported by the physical decoder are
            # checked within one record.
            if a_line != a_paddr & ~63 or values["wid"] != (a_paddr >> 3) & 7:
                fail("A paddr/line/wid relation is inconsistent")

            # Exact DDR4_8Gb_x8 RoBaRaCoCh decode after the 64-byte transaction
            # offset: 7 column, 0 channel, 0 rank, 2 BG, 2 bank, 16 row bits.
            # Reject, rather than mask, bits outside that 33-bit byte-address
            # domain.  Otherwise +1<<33 aliases a valid decoded row.
            if not (
                0 <= a_paddr < DDR4_MODELED_ADDRESS_LIMIT
                and 0 <= a_line < DDR4_MODELED_ADDRESS_LIMIT
            ):
                fail("A address is outside the modeled 33-bit DDR4 domain")
            line_number = a_line >> 6
            decoded_column = line_number & 0x7F
            decoded_bank_group = (line_number >> 7) & 0x3
            decoded_bank = (line_number >> 9) & 0x3
            decoded_row = (line_number >> 11) & 0xFFFF
            decoded_slice = decoded_bank_group * 4 + decoded_bank
            if (
                values["channel"] != 0
                or values["rank"] != 0
                or values["column"] != decoded_column
                or values["bank_group"] != decoded_bank_group
                or values["bank"] != decoded_bank
                or values["row"] != decoded_row
                or values["native_slice"] != decoded_slice
                or values["grow_addr"] != decoded_row
            ):
                fail(
                    "physical RoBaRaCoCh/native-slice/grow decode is inconsistent"
                )

            payload = " ".join(
                f"{field}={record[field]}" for field in PHYSICAL_FIELD_ORDER
            )
            payload_digest.update((payload + "\n").encode())
            semantics = tuple(values[field] for field in SEMANTIC_FIELDS)
            semantic_rows.append(semantics)
            semantic_digest.update(
                json.dumps(semantics, separators=(",", ":")).encode()
            )
            semantic_digest.update(b"\n")
            physical_records.append(
                PhysicalRecord(
                    itr=itr,
                    index=b_value,
                    b_paddr=b_paddr,
                    a_line_paddr=a_line,
                    channel=values["channel"],
                    rank=values["rank"],
                    bankgroup=values["bank_group"],
                    bank=values["bank"],
                    row=values["row"],
                    column=values["column"],
                    wid=values["wid"],
                )
            )

    if len(physical_records) != expected_count or seen_itrs != set(
        range(expected_count)
    ):
        fail("record count/domain is incomplete")
    if payload_digest.hexdigest() != validation["record_sha256"]:
        fail("reconstructed source-record SHA-256 disagrees with validation")
    if observed_ticks != set(operation_ticks):
        fail("record operation ticks disagree with validation")
    if generation_counts[1] != generation["available_records"] or (
        generation_counts[0] != generation["unavailable_records"]
    ):
        fail("record generation counts disagree with validation")

    trace_path = records_path.parent / "run" / "virtual_trace.log"
    if (
        not trace_path.is_file()
        or sha256(trace_path) != validation["trace_sha256"]
    ):
        fail("raw trace is missing or disagrees with validation SHA-256")
    return ValidatedCase(
        name=case,
        records_path=records_path,
        validation_path=validation_path,
        records=tuple(physical_records),
        semantics=tuple(semantic_rows),
        semantic_sha256=semantic_digest.hexdigest(),
        records_sha256=actual_records_sha,
        source_record_sha256=payload_digest.hexdigest(),
        trace_sha256=validation["trace_sha256"],
        validation_sha256=sha256(validation_path),
    )


def _parse_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_no, raw in enumerate(path.read_text().splitlines(), 1):
        if not raw or "=" not in raw:
            fail(f"{path}:{line_no}: malformed key/value line")
        key, value = raw.split("=", 1)
        if not key or key in values:
            fail(f"{path}:{line_no}: duplicate/empty key")
        values[key] = value
    return values


def _parse_hash_inventory(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(path.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", raw)
        if match is None or match.group(2) in seen:
            fail(f"{path}:{line_no}: malformed/duplicate hash inventory entry")
        seen.add(match.group(2))
        entries.append((match.group(1), match.group(2)))
    if not entries:
        fail(f"{path}: empty hash inventory")
    return entries


def _verify_declared_artifacts(
    case_dir: Path,
    evidence_root: Path,
    input_root: Path,
    inventory_path: Path,
) -> dict[str, str]:
    """Verify every legacy inventory entry with an exact semantic mapping."""
    expected: dict[str, tuple[str, Path]] = {
        "/input/gem5.opt": ("gem5_binary", evidence_root / "gem5.opt"),
        "/input/workload_build/test_virtual_tile_consumer_T16384": (
            "workload_binary",
            input_root
            / "workload_build"
            / "test_virtual_tile_consumer_T16384",
        ),
        "/input/libramulator.so": (
            "ramulator_library",
            input_root / "libramulator.so",
        ),
        "/input/ramulator_provenance.json": (
            "ramulator_provenance",
            input_root / "ramulator_provenance.json",
        ),
        f"/{case_dir.name}/gem5.ldd.txt": (
            "gem5_linkage",
            case_dir / "gem5.ldd.txt",
        ),
        f"/{case_dir.name}/source.diff": (
            "source_diff",
            case_dir / "source.diff",
        ),
        f"/{case_dir.name}/source_status.txt": (
            "source_status",
            case_dir / "source_status.txt",
        ),
        f"/{case_dir.name}/invocation.sh.txt": (
            "run_invocation",
            case_dir / "invocation.sh.txt",
        ),
    }
    for name in SOURCE_SNAPSHOT_FILES:
        expected[f"/{case_dir.name}/source_snapshot/{name}"] = (
            f"source_snapshot:{name}",
            case_dir / "source_snapshot" / name,
        )

    declared = _parse_hash_inventory(inventory_path)
    observed: dict[str, str] = {}
    for digest, recorded_path in declared:
        matches = [
            suffix for suffix in expected if recorded_path.endswith(suffix)
        ]
        if len(matches) != 1:
            fail(
                f"{case_dir.name}: artifact inventory path has no exact "
                f"semantic label: {recorded_path}"
            )
        suffix = matches[0]
        label, artifact = expected[suffix]
        if label in observed:
            fail(f"{case_dir.name}: duplicate semantic artifact label {label}")
        if artifact.is_symlink() or not artifact.is_file():
            fail(f"{case_dir.name}: artifact is missing or a symlink: {label}")
        if sha256(artifact) != digest:
            fail(f"{case_dir.name}: artifact content mismatch: {label}")
        observed[label] = digest
    expected_labels = {label for label, _ in expected.values()}
    if set(observed) != expected_labels:
        fail(
            f"{case_dir.name}: artifact semantic labels missing="
            f"{sorted(expected_labels - set(observed))} extra="
            f"{sorted(set(observed) - expected_labels)}"
        )
    return observed


def _evidence_entry(
    label: str, logical_path: str, path: Path
) -> dict[str, object]:
    if not label or not logical_path or logical_path.startswith("/"):
        fail("evidence inventory label/path is malformed")
    if path.is_symlink() or not path.is_file():
        fail(f"evidence inventory artifact is missing or a symlink: {label}")
    return {
        "label": label,
        "path": logical_path,
        "sha256": sha256(path),
        "size_bytes": path.stat().st_size,
    }


def validate_evidence_inventory(value: object) -> dict[str, object]:
    inventory = _require_exact_keys(
        value,
        {"case", "digest_sha256", "entries", "schema"},
        "evidence inventory",
    )
    if inventory["schema"] != INVENTORY_SCHEMA:
        fail("evidence inventory schema is inconsistent")
    if type(inventory["case"]) is not str or not inventory["case"]:
        fail("evidence inventory case is malformed")
    if type(inventory["entries"]) is not list or not inventory["entries"]:
        fail("evidence inventory entries are missing")
    labels: list[str] = []
    paths: list[str] = []
    for index, raw in enumerate(inventory["entries"]):
        entry = _require_exact_keys(
            raw,
            {"label", "path", "sha256", "size_bytes"},
            f"evidence inventory entry {index}",
        )
        if (
            type(entry["label"]) is not str
            or not entry["label"]
            or type(entry["path"]) is not str
            or not entry["path"]
            or entry["path"].startswith("/")
            or type(entry["sha256"]) is not str
            or HEX64_RE.fullmatch(entry["sha256"]) is None
            or type(entry["size_bytes"]) is not int
            or entry["size_bytes"] < 0
        ):
            fail(f"evidence inventory entry {index} is malformed")
        labels.append(entry["label"])
        paths.append(entry["path"])
    if labels != sorted(labels) or len(labels) != len(set(labels)):
        fail("evidence inventory labels are unordered or duplicated")
    if len(paths) != len(set(paths)):
        fail("evidence inventory paths are duplicated")
    unsigned = {
        "case": inventory["case"],
        "entries": inventory["entries"],
        "schema": inventory["schema"],
    }
    digest = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if inventory["digest_sha256"] != digest:
        fail("evidence inventory digest is inconsistent")
    return inventory


def _build_evidence_inventory(
    case_dir: Path,
    evidence_root: Path,
    input_root: Path,
    checkpoint_entries: list[tuple[str, str]],
) -> dict[str, object]:
    expected_case_files = COMMON_CASE_FILES | CASE_ONLY_FILES[case_dir.name]
    actual_case_files = {
        path.relative_to(case_dir).as_posix()
        for path in case_dir.rglob("*")
        if path.is_file()
    }
    if actual_case_files != expected_case_files:
        fail(
            f"{case_dir.name}: case package files missing="
            f"{sorted(expected_case_files - actual_case_files)} extra="
            f"{sorted(actual_case_files - expected_case_files)}"
        )

    entries = [
        _evidence_entry(
            f"case:{relative}",
            f"case/{relative}",
            case_dir / relative,
        )
        for relative in sorted(expected_case_files)
    ]
    shared = {
        "checkpoint_creation_script": (
            "shared/create_shared_checkpoint.sh",
            evidence_root / "create_shared_checkpoint.sh",
        ),
        "checkpoint_creation_time": (
            "shared/checkpoint.time.txt",
            evidence_root / "checkpoint.time.txt",
        ),
        "frozen_gem5_binary": ("shared/gem5.opt", evidence_root / "gem5.opt"),
        "rejected_attempt_contract": (
            "shared/rejection.json",
            evidence_root / "rejection.json",
        ),
        "shared_treatment": (
            "shared/shared_treatment.txt",
            evidence_root / "shared_treatment.txt",
        ),
        "workload_binary": (
            "input/workload_build/test_virtual_tile_consumer_T16384",
            input_root
            / "workload_build"
            / "test_virtual_tile_consumer_T16384",
        ),
        "ramulator_library": (
            "input/libramulator.so",
            input_root / "libramulator.so",
        ),
        "ramulator_provenance": (
            "input/ramulator_provenance.json",
            input_root / "ramulator_provenance.json",
        ),
    }
    entries.extend(
        _evidence_entry(label, logical, path)
        for label, (logical, path) in shared.items()
    )

    checkpoint_root = evidence_root / "shared_checkpoint"
    declared_checkpoint_files = {
        relative[2:] for _, relative in checkpoint_entries
    }
    actual_checkpoint_files = {
        path.relative_to(checkpoint_root).as_posix()
        for path in checkpoint_root.rglob("*")
        if path.is_file()
    }
    if actual_checkpoint_files != declared_checkpoint_files:
        fail(
            f"{case_dir.name}: checkpoint files missing="
            f"{sorted(declared_checkpoint_files - actual_checkpoint_files)} "
            f"extra={sorted(actual_checkpoint_files - declared_checkpoint_files)}"
        )
    entries.extend(
        _evidence_entry(
            f"checkpoint:{relative}",
            f"checkpoint/{relative}",
            checkpoint_root / relative,
        )
        for relative in sorted(declared_checkpoint_files)
    )
    entries.sort(key=lambda entry: entry["label"])
    unsigned = {
        "case": case_dir.name,
        "entries": entries,
        "schema": INVENTORY_SCHEMA,
    }
    result = {
        **unsigned,
        "digest_sha256": hashlib.sha256(
            json.dumps(
                unsigned, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    }
    return validate_evidence_inventory(result)


def audit_terminal_evidence(
    case_dir: Path,
    *,
    expected_case: str,
    expected_mode: str,
    expected_page_elements: int,
    expected_record_sha256: str,
) -> dict[str, object]:
    """Require code-zero wrapper evidence, oracle, m5 exit, and final stats."""
    for exit_name in ("checkpoint.exit", "restore.exit"):
        path = case_dir / exit_name
        if not path.is_file() or path.read_text() != "0\n":
            fail(f"{expected_case}: {exit_name} is missing or nonzero")
    marker = case_dir / "virtual_tile_consumer_case.pass"
    if not marker.is_file() or marker.stat().st_size != 0:
        fail(f"{expected_case}: pass marker is missing or malformed")
    log_path = case_dir / "restore.log"
    stats_path = case_dir / "run" / "stats.txt"
    result_path = case_dir / "result.tsv"
    if (
        not log_path.is_file()
        or not stats_path.is_file()
        or not result_path.is_file()
    ):
        fail(f"{expected_case}: terminal log/stats/result evidence is missing")
    log = log_path.read_text(encoding="utf-8", errors="strict")
    result_pattern = re.compile(
        rf"^VIRTUAL_TILE_CONSUMER_RESULT mode={re.escape(expected_mode)} "
        rf"page_elements={expected_page_elements} hash=(\d+) errors=(\d+)$",
        re.MULTILINE,
    )
    results = result_pattern.findall(log)
    if len(results) != 1 or int(results[0][1]) != 0:
        fail(f"{expected_case}: exact code-zero workload oracle is absent")
    oracle_hash = int(results[0][0])
    terminal_count = len(
        re.findall(
            r"^Exiting @ tick \d+ because m5_exit instruction encountered$",
            log,
            re.MULTILINE,
        )
    )
    if terminal_count != 1:
        fail(f"{expected_case}: exact gem5 terminal evidence is absent")
    if re.search(
        r"(^|\n)(panic|fatal|Segmentation fault)", log, re.IGNORECASE
    ):
        fail(f"{expected_case}: fatal marker appears in restore log")
    stats = stats_path.read_text(encoding="utf-8", errors="strict")
    if (
        not stats.strip().endswith(
            "---------- End Simulation Statistics   ----------"
        )
        or "---------- Begin Simulation Statistics ----------" not in stats
    ):
        fail(f"{expected_case}: nonempty final statistics window is absent")
    with result_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1 or rows[0].get("case") != expected_case:
        fail(f"{expected_case}: result.tsv does not contain one exact row")
    row = rows[0]
    if (
        row.get("output_hash") != str(oracle_hash)
        or row.get("physical_records") != str(EXPECTED_RECORDS)
        or row.get("physical_record_sha256") != expected_record_sha256
    ):
        fail(f"{expected_case}: result/oracle/physical hashes disagree")
    return {
        "checkpoint_exit": 0,
        "restore_exit": 0,
        "gem5_terminal": "m5_exit_instruction_encountered",
        "final_stats_present": True,
        "workload_oracle_hash": oracle_hash,
        "workload_errors": 0,
    }


def audit_case_provenance(
    case: ValidatedCase,
    *,
    expected_mode: str,
    expected_page_elements: int,
) -> dict[str, object]:
    case_dir = case.records_path.parent
    manifest_path = case_dir / "manifest.txt"
    inventory_path = case_dir / "artifact_sha256.txt"
    if not manifest_path.is_file() or not inventory_path.is_file():
        fail(f"{case.name}: manifest or artifact hash inventory is missing")
    manifest = _parse_key_values(manifest_path)
    required_manifest = {
        "case": case.name,
        "mode": expected_mode,
        "logical_tile_elements": str(EXPECTED_RECORDS),
        "page_elements": str(expected_page_elements),
        "row_table_slices": str(NUM_SLICES),
        "physical_record_schema": PHYSICAL_SCHEMA,
    }
    for key, value in required_manifest.items():
        if manifest.get(key) != value:
            fail(f"{case.name}: manifest {key} is missing or inconsistent")
    source_commit = manifest.get("source_commit", "")
    if not HEX40_RE.fullmatch(source_commit):
        fail(f"{case.name}: source commit is malformed")
    required_provenance_suffixes = {
        "shared_checkpoint": "/pair_evidence/shared_checkpoint",
        "shared_treatment_file": "/pair_evidence/shared_treatment.txt",
        "frozen_ramulator_library": "/pair_evidence/input/libramulator.so",
        "ramulator_provenance": "/pair_evidence/input/ramulator_provenance.json",
    }
    for key, suffix in required_provenance_suffixes.items():
        recorded = manifest.get(key)
        if type(recorded) is not str or not recorded.replace(
            "\\", "/"
        ).endswith(suffix):
            fail(f"{case.name}: manifest {key} provenance is inconsistent")
    if (case_dir / "source.diff").read_bytes() or (
        case_dir / "source_status.txt"
    ).read_bytes():
        fail(f"{case.name}: source snapshot was not clean")

    evidence_root = case_dir.parent
    input_root = evidence_root.parent / "input"
    declared_artifacts = _verify_declared_artifacts(
        case_dir, evidence_root, input_root, inventory_path
    )
    gem5_hash = declared_artifacts["gem5_binary"]
    benchmark_hash = declared_artifacts["workload_binary"]

    checkpoint_inventory = case_dir / "shared_checkpoint_files.sha256"
    identity_path = case_dir / "shared_checkpoint_identity.sha256"
    identity_entries = _parse_hash_inventory(identity_path)
    if len(identity_entries) != 1 or not identity_entries[0][1].replace(
        "\\", "/"
    ).endswith(f"/{case.name}/shared_checkpoint_files.sha256"):
        fail(f"{case.name}: checkpoint identity must contain one entry")
    checkpoint_identity = identity_entries[0][0]
    if sha256(checkpoint_inventory) != checkpoint_identity:
        fail(f"{case.name}: checkpoint inventory identity mismatch")
    checkpoint_root = evidence_root / "shared_checkpoint"
    checkpoint_entries = _parse_hash_inventory(checkpoint_inventory)
    for digest, relative in checkpoint_entries:
        if not relative.startswith("./") or ".." in Path(relative).parts:
            fail(f"{case.name}: unsafe checkpoint inventory path")
        artifact = checkpoint_root / relative[2:]
        if (
            artifact.is_symlink()
            or not artifact.is_file()
            or sha256(artifact) != digest
        ):
            fail(f"{case.name}: checkpoint artifact hash mismatch: {relative}")

    rejection_path = evidence_root / "rejection.json"
    try:
        rejection_sha = validate_rejection_contract(
            rejection_path,
            source_commit=source_commit,
            gem5_hash=gem5_hash,
        )
    except GroundingError as exc:
        raise GroundingError(f"{case.name}: {exc}") from exc

    terminal = audit_terminal_evidence(
        case_dir,
        expected_case=case.name,
        expected_mode=expected_mode,
        expected_page_elements=expected_page_elements,
        expected_record_sha256=case.source_record_sha256,
    )
    boundary: dict[str, object] = {
        "nonphysical_attribution_accepted": False,
        "physical_records_preserved": True,
        "rejected_attempt_manifest_sha256": rejection_sha,
    }
    evidence_inventory = _build_evidence_inventory(
        case_dir, evidence_root, input_root, checkpoint_entries
    )

    return {
        "source_commit": source_commit,
        "gem5_sha256": gem5_hash,
        "benchmark_sha256": benchmark_hash,
        "checkpoint_inventory_sha256": checkpoint_identity,
        "evidence_inventory": evidence_inventory,
        "terminal": terminal,
        "boundary": boundary,
    }


def _case_manifest(case: ValidatedCase) -> dict[str, object]:
    if case.provenance is None:
        fail(f"{case.name}: provenance audit was not attached")
    return {
        "case": case.name,
        "actual_records_path": str(case.records_path),
        "records_sha256": case.records_sha256,
        "source_record_sha256": case.source_record_sha256,
        "validation_path": str(case.validation_path),
        "validation_sha256": case.validation_sha256,
        "trace_sha256": case.trace_sha256,
        **case.provenance,
    }


def _with_provenance(
    case: ValidatedCase, provenance: dict[str, object]
) -> ValidatedCase:
    return ValidatedCase(**{**case.__dict__, "provenance": provenance})


def compare_semantics(
    native: ValidatedCase, transparent: ValidatedCase
) -> str:
    if len(native.semantics) != len(transparent.semantics):
        fail("native/transparent semantic record counts differ")
    for itr, (left, right) in enumerate(
        zip(native.semantics, transparent.semantics)
    ):
        if left != right:
            differing = [
                field
                for field, left_value, right_value in zip(
                    SEMANTIC_FIELDS, left, right
                )
                if left_value != right_value
            ]
            fail(f"semantic physical mismatch at itr {itr}: {differing}")
    if native.semantic_sha256 != transparent.semantic_sha256:
        fail("semantic digest mismatch despite row equality")
    return native.semantic_sha256


def _model_summary(case: ValidatedCase) -> dict[str, object]:
    # The schema exports a complete 16-slice aperture, while the exact DDR4
    # decoder fixes grow/row to [0,65536).  These full decode-domain bounds are
    # independent of the observed row histogram.
    geometry = ApertureGeometry((0,) * NUM_SLICES, (65_536,) * NUM_SLICES)
    result = Model(
        logical_elements=EXPECTED_RECORDS,
        active_elements=ACTIVE_ELEMENTS,
        source_elements=SOURCE_ELEMENTS,
        partitions=PARTITIONS,
    ).run(
        case.records,
        geometry,
        evidence_class="grounded_gem5_physical_admission_v1",
    )
    return {
        "evidence_class": result.evidence_class,
        "logical_elements": result.logical_elements,
        "active_elements": result.active_elements,
        "partitions": result.partitions,
        "grow_aperture": {
            "source": "DDR4_8Gb_x8_decoder_domain_not_observed_histogram",
            "per_slice_lower": 0,
            "per_slice_upper_exclusive": 65_536,
        },
        "capacity": {
            "epochs": result.epochs,
            "capacity_drains": result.capacity_drains,
            "drain_reasons": result.drain_reasons,
            "peak_offsets": result.peak_offsets,
            "peak_row_slots": result.peak_row_slots,
            "peak_line_slots": result.peak_line_slots,
            "line_slot_rollovers": result.line_slot_rollovers,
            "geometry_bound_respected": result.geometry_bound_respected,
            "peak_reserved_responses": result.peak_reserved_responses,
            "peak_reserved_words": result.peak_reserved_words,
        },
        "issue_order": {
            "a_line_requests": result.a_line_requests,
            "build_rounds": result.build_rounds,
            "per_slice_row_transitions": result.per_slice_row_transitions,
            "sha256": result.issue_order_sha256,
            "materialized_entries": result.materialized_issue_order_entries,
        },
        "traffic": {
            "b_unique_lines_per_pass": result.b_unique_lines_per_pass,
            "b_line_reads": result.b_line_reads,
            "b_reread_lines": result.b_reread_lines,
            "b_semantic_bytes": result.b_semantic_bytes,
            "selector_words": result.selector_words,
            "selector_cycles_arithmetic_lower_bound": (
                result.selector_cycles_lower_bound
            ),
            "a_line_requests": result.a_line_requests,
        },
        "placement_check": {
            "placements": result.placements,
            "missing": result.missing_placements,
            "duplicates": result.duplicate_placements,
        },
    }


def ground_pair(
    native_records: Path,
    native_validation: Path,
    transparent_records: Path,
    transparent_validation: Path,
) -> dict[str, object]:
    native = validate_records(
        native_records, native_validation, case_name="native_direct_16k"
    )
    transparent = validate_records(
        transparent_records, transparent_validation, case_name="transparent_4k"
    )
    native = _with_provenance(
        native,
        audit_case_provenance(
            native,
            expected_mode="native_direct",
            expected_page_elements=16_384,
        ),
    )
    transparent = _with_provenance(
        transparent,
        audit_case_provenance(
            transparent,
            expected_mode="transparent",
            expected_page_elements=4_096,
        ),
    )
    semantic_sha = compare_semantics(native, transparent)
    for field in (
        "source_commit",
        "gem5_sha256",
        "benchmark_sha256",
        "checkpoint_inventory_sha256",
    ):
        if native.provenance[field] != transparent.provenance[field]:
            fail(f"pair provenance mismatch: {field}")
    if (
        native.provenance["boundary"]["rejected_attempt_manifest_sha256"]
        != transparent.provenance["boundary"][
            "rejected_attempt_manifest_sha256"
        ]
    ):
        fail("pair provenance mismatch: rejected attempt manifest")
    native_terminal = native.provenance["terminal"]
    transparent_terminal = transparent.provenance["terminal"]
    if (
        native_terminal["workload_oracle_hash"]
        != transparent_terminal["workload_oracle_hash"]
    ):
        fail("pair workload oracle hashes differ")

    return {
        "schema": GROUNDING_SCHEMA,
        "status": "grounded",
        "evidence_boundary": {
            "accepted": PHYSICAL_SCHEMA,
            "rejected_nonphysical_attribution_consumed": False,
            "physical_records_preserved_by_upstream_rejection": True,
            "timing_performance_claim": None,
        },
        "pair_comparison": {
            "semantic_fields": list(SEMANTIC_FIELDS),
            "excluded_treatment_metadata": list(EXCLUDED_TREATMENT_FIELDS),
            "record_count": EXPECTED_RECORDS,
            "exact_match": True,
            "semantic_sha256": semantic_sha,
        },
        "provenance": {
            "source_commit": native.provenance["source_commit"],
            "gem5_sha256": native.provenance["gem5_sha256"],
            "benchmark_sha256": native.provenance["benchmark_sha256"],
            "checkpoint_inventory_sha256": native.provenance[
                "checkpoint_inventory_sha256"
            ],
            "case_inventory_sha256": {
                "native": native.provenance["evidence_inventory"][
                    "digest_sha256"
                ],
                "transparent": transparent.provenance["evidence_inventory"][
                    "digest_sha256"
                ],
            },
            "cases": {
                "native": _case_manifest(native),
                "transparent": _case_manifest(transparent),
            },
        },
        "finite_model": _model_summary(native),
        "claims": {
            "physical_grounding": "accepted",
            "finite_model": "model_output_on_exact_paired_physical_records",
            "gem5_timing_performance": "not_claimed",
            "implementation_authorization": False,
        },
    }


def encode_grounded_result(result: dict[str, object]) -> bytes:
    return (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-records", required=True, type=Path)
    parser.add_argument("--native-validation", required=True, type=Path)
    parser.add_argument("--transparent-records", required=True, type=Path)
    parser.add_argument("--transparent-validation", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = ground_pair(
        args.native_records,
        args.native_validation,
        args.transparent_records,
        args.transparent_validation,
    )
    encoded = encode_grounded_result(result)
    if args.output is not None:
        args.output.write_bytes(encoded)
    else:
        print(encoded.decode(), end="")


if __name__ == "__main__":
    main()
