#!/usr/bin/env python3
"""Fail-closed schema validation and exact comparison for UMT P0 JSONL.

This is intentionally a trace-contract skeleton.  It validates C++ scheduler
traces, but neither consumes RTL output nor makes an RTL-equivalence claim.
"""

import argparse
import hashlib
import json
import pathlib
import sys

SCHEMA = "lanl-maa-umt-cycle-trace-v1"
HEADER_KEYS = {
    "record_type",
    "schema",
    "schema_version",
    "source_commit",
    "rtl_commit",
    "abi_versions",
    "compute_tokens",
    "fp_issue_width",
    "divider_lanes",
    "divide_latency",
    "divide_ii",
    "line_bytes",
    "descriptor_hash",
    "stimulus_hash",
    "canonicalization_version",
    "scenario",
}
CYCLE_KEYS = {
    "record_type",
    "cycle",
    "inputs",
    "issues",
    "completion_ready",
    "bank_word_changes",
    "state",
    "counters",
}
INPUT_KEYS = {
    "source_ingress",
    "denominator_ingress",
    "arithmetic_completions",
    "external_access",
    "line_ledger",
}
STATE_KEYS = {
    "digest",
    "issue_cursor",
    "active_tokens",
    "next_bank_cycle",
}
COUNTER_KEYS = {
    "fp_operations",
    "dual_issue",
    "fp_issue_stall",
    "bank_conflict",
    "writeback_stall",
    "result_bank_stall",
    "divider_no_lane",
}


def fail(message):
    raise ValueError(message)


def exact_keys(value, keys, location):
    if not isinstance(value, dict) or set(value) != keys:
        fail(f"{location}: keys must be exactly {sorted(keys)}")


def integer(value, location):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail(f"{location}: expected non-negative integer")


def hex_string(value, digits, location):
    if not isinstance(value, str) or len(value) != digits:
        fail(f"{location}: expected {digits}-digit lowercase hexadecimal")
    if any(character not in "0123456789abcdef" for character in value):
        fail(f"{location}: expected lowercase hexadecimal")


def validate_header(header):
    exact_keys(header, HEADER_KEYS, "header")
    if header["record_type"] != "header" or header["schema"] != SCHEMA:
        fail("header: unsupported record type or schema")
    if (
        header["schema_version"] != 1
        or header["canonicalization_version"] != 1
    ):
        fail("header: unsupported version")
    for key in ("source_commit", "rtl_commit"):
        hex_string(header[key], 40, f"header.{key}")
    if header["abi_versions"] != [4, 5]:
        fail("header.abi_versions: must pin [4, 5]")
    if header["compute_tokens"] not in (24, 32):
        fail("header.compute_tokens: must be T24 or T32")
    if header["fp_issue_width"] not in (1, 2):
        fail("header.fp_issue_width: must be W1 or W2")
    if (
        header["divider_lanes"],
        header["divide_latency"],
        header["divide_ii"],
        header["line_bytes"],
    ) != (8, 64, 32, 64):
        fail("header: fixed UMT timing/line pins do not match")
    for key in ("descriptor_hash", "stimulus_hash"):
        hex_string(header[key], 64, f"header.{key}")
    if not isinstance(header["scenario"], str) or not header["scenario"]:
        fail("header.scenario: expected non-empty string")


def validate_cycle(record, previous_cycle, header):
    exact_keys(record, CYCLE_KEYS, "cycle")
    if record["record_type"] != "cycle":
        fail("cycle: wrong record type")
    integer(record["cycle"], "cycle.cycle")
    if previous_cycle is not None and record["cycle"] != previous_cycle + 1:
        fail("cycle.cycle: cycles must be contiguous")
    exact_keys(record["inputs"], INPUT_KEYS, "cycle.inputs")
    for key in (
        "source_ingress",
        "denominator_ingress",
        "arithmetic_completions",
        "external_access",
    ):
        if not isinstance(record["inputs"][key], list):
            fail(f"cycle.inputs.{key}: expected list")
    if (
        record["inputs"]["source_ingress"]
        or record["inputs"]["arithmetic_completions"]
        or record["inputs"]["external_access"]
    ):
        fail("cycle.inputs: P0 admits only ordered denominator ingress")
    previous_admission = None
    for admission in record["inputs"]["denominator_ingress"]:
        exact_keys(
            admission,
            {"operation", "group", "corner"},
            "cycle.inputs.denominator_ingress item",
        )
        for key in ("operation", "group", "corner"):
            integer(admission[key], f"denominator ingress.{key}")
        if admission["group"] >= 64 or admission["corner"] >= 8:
            fail("denominator ingress: group or corner out of range")
        coordinate = (
            admission["operation"],
            admission["group"],
            admission["corner"],
        )
        if previous_admission is not None and coordinate <= previous_admission:
            fail("denominator ingress: must preserve caller ordering")
        previous_admission = coordinate
    ledger = record["inputs"]["line_ledger"]
    exact_keys(
        ledger,
        {"d32", "d64", "response", "release", "hold"},
        "cycle.inputs.line_ledger",
    )
    for key, value in ledger.items():
        integer(value, f"cycle.inputs.line_ledger.{key}")
    issues = record["issues"]
    if not isinstance(issues, list) or len(issues) not in (1, 2):
        fail("cycle.issues: expected one or two ordered issue slots")
    for slot, issue in enumerate(issues):
        exact_keys(
            issue,
            {"valid", "slot", "token", "operation", "lane"},
            f"cycle.issues[{slot}]",
        )
        if not isinstance(issue["valid"], bool):
            fail(f"cycle.issues[{slot}].valid: expected boolean")
        if issue["slot"] != slot:
            fail("cycle.issues: slot order is architectural")
        integer(issue["token"], f"cycle.issues[{slot}].token")
        if issue["token"] >= header["compute_tokens"]:
            fail("cycle.issues: token index outside configured capacity")
        if issue["operation"] not in (
            "none",
            "denominator_add",
            "divide",
            "multiply",
            "edge_add",
        ):
            fail("cycle.issues: unknown operation")
        if issue["lane"] is not None:
            integer(issue["lane"], f"cycle.issues[{slot}].lane")
            if issue["lane"] >= 8:
                fail("cycle.issues: invalid divider lane")
        if not issue["valid"] and (
            issue["operation"] != "none" or issue["lane"] is not None
        ):
            fail("cycle.issues: invalid slots must be canonical none/null")
        if issue["valid"] and issue["operation"] == "none":
            fail("cycle.issues: valid slots require an operation")
        if (issue["operation"] == "divide") != (issue["lane"] is not None):
            fail("cycle.issues: only divide carries a lane")
    if not isinstance(record["completion_ready"], list):
        fail("cycle.completion_ready: expected ordered list")
    for item in record["completion_ready"]:
        integer(item, "cycle.completion_ready item")
    changes = record["bank_word_changes"]
    if not isinstance(changes, list):
        fail("cycle.bank_word_changes: expected list")
    previous_change = None
    for item in changes:
        exact_keys(item, {"bank", "row", "word", "value"}, "bank change")
        for key in ("bank", "row", "word"):
            integer(item[key], f"bank change.{key}")
        if item["bank"] >= 4 or item["row"] >= 16 or item["word"] >= 10:
            fail("bank change: out-of-range bank row or word")
        hex_string(item["value"], 16, "bank change.value")
        coordinate = (item["bank"], item["row"], item["word"])
        if previous_change is not None and coordinate <= previous_change:
            fail(
                "bank changes: must be strictly canonical bank,row,word order"
            )
        previous_change = coordinate
    state = record["state"]
    exact_keys(state, STATE_KEYS, "cycle.state")
    hex_string(state["digest"], 16, "cycle.state.digest")
    integer(state["issue_cursor"], "cycle.state.issue_cursor")
    if state["issue_cursor"] >= header["compute_tokens"]:
        fail("cycle.state.issue_cursor: outside configured capacity")
    if (
        not isinstance(state["next_bank_cycle"], list)
        or len(state["next_bank_cycle"]) != 4
    ):
        fail("cycle.state.next_bank_cycle: expected four banks")
    for value in state["next_bank_cycle"]:
        integer(value, "cycle.state.next_bank_cycle item")
    if not isinstance(state["active_tokens"], list):
        fail("cycle.state.active_tokens: expected list")
    previous_token = -1
    for token in state["active_tokens"]:
        exact_keys(token, {"index", "packed"}, "active token")
        integer(token["index"], "active token.index")
        if token["index"] >= header["compute_tokens"]:
            fail("active token.index: outside configured capacity")
        if token["index"] <= previous_token:
            fail("active tokens: must be first-free index order")
        previous_token = token["index"]
        hex_string(token["packed"], 118, "active token.packed")
        if int(token["packed"][-2:], 16) & 0x80:
            fail("active token.packed: required pad bit is nonzero")
    counters = record["counters"]
    exact_keys(counters, COUNTER_KEYS, "cycle.counters")
    for key, value in counters.items():
        integer(value, f"cycle.counters.{key}")
    return record["cycle"]


def load_trace(path):
    path = pathlib.Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        fail("trace must contain one header and at least one cycle")
    records = []
    for index, line in enumerate(lines, 1):
        if not line:
            fail(f"line {index}: blank JSONL records are forbidden")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            fail(f"line {index}: invalid JSON: {error.msg}")
        if not isinstance(value, dict):
            fail(f"line {index}: JSON object required")
        records.append(value)
    validate_header(records[0])
    previous_cycle = None
    for record in records[1:]:
        previous_cycle = validate_cycle(record, previous_cycle, records[0])
    return records


def semantic_digest(records):
    """Return the versioned canonical digest used for replay fixtures.

    JSONL permits harmless whitespace variation.  The trace contract does
    not: every semantic record is re-encoded with sorted keys and compact
    separators before hashing.  This is intentionally independent of the
    producer's fixed descriptor/stimulus labels, so a copied header cannot
    conceal a changed cycle record.
    """
    canonical = "\n".join(
        json.dumps(record, sort_keys=True, separators=(",", ":"))
        for record in records
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=pathlib.Path)
    parser.add_argument("--compare", type=pathlib.Path)
    parser.add_argument("--expected-semantic-digest")
    args = parser.parse_args()
    try:
        left = load_trace(args.trace)
        digest = semantic_digest(left)
        if (
            args.expected_semantic_digest is not None
            and digest != args.expected_semantic_digest
        ):
            fail("fixture integrity failed: semantic digest differs")
        if args.compare is not None:
            right = load_trace(args.compare)
            if left != right:
                fail("comparison failed: canonical records differ")
            if digest != semantic_digest(right):
                fail("comparison failed: semantic digest differs")
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": SCHEMA,
                "status": "passed",
                "records": len(left),
                "compared": args.compare is not None,
                "semantic_digest": digest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
