#!/usr/bin/env python3
"""Fail-closed parser for the matched hybrid macro-profile matrix."""

import argparse
import csv
import json
from pathlib import Path

PRODUCER_EVENT = "hybrid_producer_macro"
CONSUMER_EVENT = "hybrid_consumer_macro"


def read_one_row(path: Path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        raise ValueError(f"{path}: expected one result row, found {len(rows)}")
    return rows[0]


def read_arms(path: Path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    required = {"label", "case", "words_per_cycle", "write_credits", "role"}
    if not rows or set(rows[0]) != required:
        raise ValueError(f"{path}: arm columns must be {sorted(required)}")
    labels = [row["label"] for row in rows]
    if len(labels) != len(set(labels)):
        raise ValueError(f"{path}: duplicate arm label")
    return rows


def parse_event(line: str, event: str):
    marker = f"event={event} "
    offset = line.find(marker)
    if offset == -1:
        return None
    fields = {}
    for token in line[offset:].split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key in fields:
            raise ValueError(f"duplicate {key} in {event} event")
        fields[key] = value
    if fields.get("event") != event or fields.get("schema") != "1":
        raise ValueError(f"invalid {event} event schema")
    parsed = {"event": event}
    for key, value in fields.items():
        if key == "event":
            continue
        try:
            parsed[key] = int(value, 0)
        except ValueError as error:
            raise ValueError(
                f"{event}: {key} is not an integer: {value}"
            ) from error
    return parsed


def read_macro_events(path: Path):
    records = {PRODUCER_EVENT: [], CONSUMER_EVENT: []}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            for event in records:
                parsed = parse_event(line, event)
                if parsed is not None:
                    records[event].append(parsed)
    return records


def require_order(record, fields, label):
    values = [record[field] for field in fields]
    if any(value == 0 for value in values) or values != sorted(values):
        raise ValueError(
            f"{label}: invalid ordered fields {dict(zip(fields, values))}"
        )


def validate_hybrid(label, result, producer, consumer):
    require_order(
        producer,
        ("b_first_issue_tick", "b_last_issue_tick", "b_last_response_tick"),
        f"{label} B stream",
    )
    require_order(
        producer,
        ("a_first_issue_tick", "a_last_issue_tick", "a_last_response_tick"),
        f"{label} A stream",
    )
    require_order(
        producer,
        (
            "backing_first_issue_tick",
            "backing_last_issue_tick",
            "backing_last_ack_tick",
        ),
        f"{label} backing",
    )
    require_order(
        producer,
        ("page_first_ready_tick", "page_last_ready_tick"),
        f"{label} page ready",
    )
    require_order(
        consumer,
        ("submit_tick", "retire_tick"),
        f"{label} consumer",
    )
    if producer["operation_tick"] != consumer["producer_operation_tick"]:
        raise ValueError(f"{label}: producer/consumer operation tick mismatch")
    if producer["page_last_ready_tick"] != consumer["all_pages_ready_tick"]:
        raise ValueError(f"{label}: producer/consumer all-ready tick mismatch")
    if producer["pages_ready"] != 4 or result["pages_ready"] != "4":
        raise ValueError(f"{label}: page readiness did not close")
    issues = producer["backing_line_issues"] + producer["backing_word_issues"]
    if issues != int(result["write_issues"]):
        raise ValueError(f"{label}: macro/result backing issue mismatch")
    if result["write_issues"] != result["write_completions"]:
        raise ValueError(f"{label}: backing writes did not close")
    if producer["backing_last_ack_tick"] > producer["complete_tick"]:
        raise ValueError(
            f"{label}: producer completed before its last backing ACK"
        )
    if consumer["fill_issues"] != consumer["fill_completions"]:
        raise ValueError(f"{label}: page-fill actions did not close")
    if consumer["alu_issues"] != consumer["alu_completions"]:
        raise ValueError(f"{label}: ALU actions did not close")
    if consumer["store_issues"] != consumer["store_completions"]:
        raise ValueError(f"{label}: stream-store actions did not close")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--arms", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-tsv", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    root = args.root.resolve()
    arms = read_arms(args.arms)
    if "hybrid_base" not in {arm["label"] for arm in arms}:
        raise ValueError("matrix has no hybrid_base arm")

    records = []
    output_hash = None
    checkpoint_identity = None
    for arm in arms:
        label = arm["label"]
        arm_root = root / label
        result = read_one_row(arm_root / "result.tsv")
        if result["case"] != arm["case"]:
            raise ValueError(f"{label}: result case mismatch")
        if result["virtual_words_per_cycle"] != arm["words_per_cycle"]:
            raise ValueError(f"{label}: resolved words/cycle mismatch")
        if result["virtual_max_outstanding_writes"] != arm["write_credits"]:
            raise ValueError(f"{label}: resolved write-credit mismatch")
        if output_hash is None:
            output_hash = result["output_hash"]
        elif result["output_hash"] != output_hash:
            raise ValueError(f"{label}: exact output hash differs")
        identity = (
            (arm_root / "shared_checkpoint_identity.sha256")
            .read_text(encoding="utf-8")
            .split()[0]
        )
        if checkpoint_identity is None:
            checkpoint_identity = identity
        elif identity != checkpoint_identity:
            raise ValueError(f"{label}: shared-checkpoint identity differs")

        events = read_macro_events(arm_root / "run" / "virtual_trace.log")
        is_hybrid = arm["role"] != "native_reference"
        expected = 1 if is_hybrid else 0
        for event in (PRODUCER_EVENT, CONSUMER_EVENT):
            if len(events[event]) != expected:
                raise ValueError(
                    f"{label}: expected {expected} {event} events, "
                    f"found {len(events[event])}"
                )
        producer = events[PRODUCER_EVENT][0] if is_hybrid else None
        consumer = events[CONSUMER_EVENT][0] if is_hybrid else None
        if is_hybrid:
            validate_hybrid(label, result, producer, consumer)
        records.append(
            {
                "arm": arm,
                "raw_path": str(arm_root),
                "result": result,
                "producer": producer,
                "consumer": consumer,
            }
        )

    baseline = next(
        record for record in records if record["arm"]["label"] == "hybrid_base"
    )
    baseline_ticks = int(baseline["result"]["simTicks"])
    for record in records:
        ticks = int(record["result"]["simTicks"])
        record["delta_vs_hybrid_base_ticks"] = ticks - baseline_ticks
        record["delta_vs_hybrid_base_pct"] = (
            ticks / baseline_ticks - 1.0
        ) * 100.0
        record["simticks_changed_vs_hybrid_base"] = ticks != baseline_ticks

    provenance = {
        "root": str(root),
        "checkpoint_identity_sha256": checkpoint_identity,
        "exact_output_hash": output_hash,
    }
    provenance_path = root / "matrix.provenance.json"
    if provenance_path.exists():
        matrix_provenance = json.loads(
            provenance_path.read_text(encoding="utf-8")
        )
        recorded_checkpoint = matrix_provenance.get(
            "checkpoint_identity_sha256"
        )
        if recorded_checkpoint != checkpoint_identity:
            raise ValueError("matrix/checkpoint provenance mismatch")
        provenance.update(matrix_provenance)
    payload = {"schema": 1, "provenance": provenance, "records": records}
    args.output_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    fields = (
        "label",
        "case",
        "role",
        "words_per_cycle",
        "write_credits",
        "simTicks",
        "delta_vs_hybrid_base_ticks",
        "delta_vs_hybrid_base_pct",
        "output_hash",
        "backing_credit_stalls",
        "backing_queue_high_water",
        "backing_last_issue_to_last_ack_ticks",
        "pipeline_overlap_cycles",
        "producer_consumer_overlap_ticks",
        "consumer_exposed_idle_ticks",
        "post_ready_fill_ticks",
        "post_ready_alu_ticks",
        "post_ready_store_ticks",
        "post_ready_exposed_idle_ticks",
    )
    with args.output_tsv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for record in records:
            producer = record["producer"] or {}
            consumer = record["consumer"] or {}
            writer.writerow(
                {
                    **record["arm"],
                    "simTicks": record["result"]["simTicks"],
                    "delta_vs_hybrid_base_ticks": record[
                        "delta_vs_hybrid_base_ticks"
                    ],
                    "delta_vs_hybrid_base_pct": f"{record['delta_vs_hybrid_base_pct']:.9f}",
                    "output_hash": record["result"]["output_hash"],
                    "backing_credit_stalls": producer.get(
                        "backing_credit_stalls", ""
                    ),
                    "backing_queue_high_water": producer.get(
                        "backing_queue_high_water", ""
                    ),
                    "backing_last_issue_to_last_ack_ticks": (
                        producer["backing_last_ack_tick"]
                        - producer["backing_last_issue_tick"]
                        if producer
                        else ""
                    ),
                    "pipeline_overlap_cycles": producer.get(
                        "pipeline_overlap_cycles", ""
                    ),
                    "producer_consumer_overlap_ticks": consumer.get(
                        "producer_consumer_overlap_ticks", ""
                    ),
                    "consumer_exposed_idle_ticks": consumer.get(
                        "consumer_exposed_idle_ticks", ""
                    ),
                    "post_ready_fill_ticks": consumer.get(
                        "post_ready_fill_ticks", ""
                    ),
                    "post_ready_alu_ticks": consumer.get(
                        "post_ready_alu_ticks", ""
                    ),
                    "post_ready_store_ticks": consumer.get(
                        "post_ready_store_ticks", ""
                    ),
                    "post_ready_exposed_idle_ticks": consumer.get(
                        "post_ready_exposed_idle_ticks", ""
                    ),
                }
            )


if __name__ == "__main__":
    main()
