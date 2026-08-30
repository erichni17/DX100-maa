#!/usr/bin/env python3
"""Fail-closed hierarchical retained-state audit for the four UMT wrappers.

The input must be four Yosys JSON designs emitted after exactly the structural
``proc; memory_collect; opt_clean`` checkpoint.  This script proves only that
the cost-shell allocation is present and classified consistently.  In
particular, ``model_floor_reserved`` bits are not behavioral implementation
evidence and the result is not an area, Fmax, physical-cost, or trace-closure
claim.
"""

import argparse
import collections
import hashlib
import json
from pathlib import Path

WRAPPERS = {
    "LanlUmtSchedulerShellT24W1": (24, 1, 54372),
    "LanlUmtSchedulerShellT24W2": (24, 2, 54372),
    "LanlUmtSchedulerShellT32W1": (32, 1, 58142),
    "LanlUmtSchedulerShellT32W2": (32, 2, 58142),
}

BEHAVIORAL_MEMBERS = {
    "functional": {
        "current_cycle": 64,
        "issue_cursor": 6,
    },
    "bank_scheduler": {
        "writeback_reservations": 4,
        "issue_bank_reservations": 4,
    },
    "instrumentation": {
        "fp_operations_issued": 64,
        "dual_issue_cycles": 64,
        "fp_issue_stall_cycles": 64,
        "bank_conflict_cycles": 64,
        "writeback_stall_cycles": 64,
        "result_bank_stall_cycles": 64,
        "divider_no_lane_cycles": 64,
    },
    "token": {"token_entry": 471},
}

RESERVED_MEMBERS = {
    "functional": "functional_model_floor_reserved",
    "bank_scheduler": "bank_scheduler_model_floor_reserved",
    "instrumentation": "instrumentation_model_floor_reserved",
}

RESERVED_WIDTHS = {
    24: {
        "functional": 586,
        "bank_scheduler": 275,
        "instrumentation": 721,
    },
    32: {
        "functional": 587,
        "bank_scheduler": 275,
        "instrumentation": 722,
    },
}

STATE_ATTRIBUTE_KEYS = {
    "umt_state_class",
    "umt_state_kind",
    "umt_state_member",
}


class ValidationError(RuntimeError):
    pass


def fail(message):
    raise ValidationError(message)


def binary_parameter(value, description):
    if (
        not isinstance(value, str)
        or not value
        or any(bit not in "01" for bit in value)
    ):
        fail(f"{description} is not a fixed binary parameter: {value!r}")
    return int(value, 2)


def tagged_attributes(attributes, description):
    attributes = attributes or {}
    present = STATE_ATTRIBUTE_KEYS.intersection(attributes)
    if not present:
        return None
    if present != STATE_ATTRIBUTE_KEYS:
        missing = sorted(STATE_ATTRIBUTE_KEYS - present)
        fail(f"{description} has a partial state tag; missing {missing}")
    # The hierarchy audit is only meaningful if the structural checkpoint was
    # asked to preserve the state it is accounting for.  Do not accept a
    # correctly named tag whose storage is free to disappear in a later pass.
    if attributes.get("keep") != "true":
        fail(f"{description} has retained-state tags without keep=true")
    return {key: attributes[key] for key in STATE_ATTRIBUTE_KEYS}


def is_sequential_cell(cell):
    cell_type = cell.get("type", "")
    return (
        cell_type.startswith("$")
        and ("ff" in cell_type or "latch" in cell_type)
        and "Q" in cell.get("connections", {})
    )


def count_q_drivers(module):
    counts = collections.Counter()
    for cell in module.get("cells", {}).values():
        if not is_sequential_cell(cell):
            continue
        for bit in cell["connections"]["Q"]:
            if isinstance(bit, int):
                counts[bit] += 1
    return counts


def validate_design(
    document, top, compute_tokens, issue_width, expected_total
):
    modules = document.get("modules")
    if not isinstance(modules, dict) or top not in modules:
        fail(f"{top}: top module is absent")
    if (
        binary_parameter(
            modules[top].get("attributes", {}).get("top", "0"),
            f"{top}: top attribute",
        )
        != 1
    ):
        fail(f"{top}: module is not the selected Yosys top")

    records = []
    reached_shells = []
    reached_banks = []

    def walk(module_name, path, ancestors):
        if module_name not in modules:
            fail(f"{top}: {path} references absent module {module_name}")
        if module_name in ancestors:
            fail(f"{top}: recursive module hierarchy at {path}")
        module = modules[module_name]
        module_hdlname = module.get("attributes", {}).get("hdlname")
        if module_hdlname == "LanlUmtSchedulerShell":
            reached_shells.append((path, module))
        if module_hdlname == "LanlUmtBank16x640":
            reached_banks.append((path, module))

        q_drivers = count_q_drivers(module)
        tagged_bit_owners = {}
        for net_name, net in module.get("netnames", {}).items():
            description = f"{top}: {path}.{net_name}"
            tag = tagged_attributes(net.get("attributes", {}), description)
            if tag is None:
                continue
            bits = net.get("bits")
            if not isinstance(bits, list) or not bits:
                fail(f"{description} has no retained bits")
            for bit in bits:
                if not isinstance(bit, int):
                    fail(f"{description} contains a constant/non-storage bit")
                if bit in tagged_bit_owners:
                    fail(
                        f"{description} double-charges bit {bit} already owned by "
                        f"{tagged_bit_owners[bit]}"
                    )
                tagged_bit_owners[bit] = description
                if q_drivers[bit] != 1:
                    fail(
                        f"{description} bit {bit} has {q_drivers[bit]} "
                        "sequential Q drivers instead of exactly one"
                    )
            records.append(
                {
                    "path": description,
                    "object": "net",
                    "class": tag["umt_state_class"],
                    "kind": tag["umt_state_kind"],
                    "member": tag["umt_state_member"],
                    "bits": len(bits),
                }
            )

        next_ancestors = ancestors + (module_name,)
        for cell_name, cell in module.get("cells", {}).items():
            description = f"{top}: {path}.{cell_name}"
            tag = tagged_attributes(cell.get("attributes", {}), description)
            if tag is not None:
                if cell.get("type") != "$mem_v2":
                    fail(
                        f"{description} tags a non-memory cell as retained state"
                    )
                parameters = cell.get("parameters", {})
                width = binary_parameter(
                    parameters.get("WIDTH"), f"{description} WIDTH"
                )
                size = binary_parameter(
                    parameters.get("SIZE"), f"{description} SIZE"
                )
                address_bits = binary_parameter(
                    parameters.get("ABITS"), f"{description} ABITS"
                )
                records.append(
                    {
                        "path": description,
                        "object": "memory",
                        "class": tag["umt_state_class"],
                        "kind": tag["umt_state_kind"],
                        "member": tag["umt_state_member"],
                        "bits": width * size,
                        "width": width,
                        "size": size,
                        "address_bits": address_bits,
                    }
                )
            cell_type = cell.get("type")
            if cell_type in modules:
                walk(cell_type, f"{path}.{cell_name}", next_ancestors)

    walk(top, top, ())

    if len(reached_shells) != 1:
        fail(
            f"{top}: reached {len(reached_shells)} scheduler shells, expected one"
        )
    shell_path, shell = reached_shells[0]
    shell_parameters = shell.get("parameter_default_values", {})
    if (
        binary_parameter(
            shell_parameters.get("COMPUTE_TOKENS"),
            f"{top}: {shell_path} COMPUTE_TOKENS",
        )
        != compute_tokens
    ):
        fail(f"{top}: shell token parameter does not match wrapper identity")
    if (
        binary_parameter(
            shell_parameters.get("FP_ISSUE_WIDTH"),
            f"{top}: {shell_path} FP_ISSUE_WIDTH",
        )
        != issue_width
    ):
        fail(
            f"{top}: shell issue-width parameter does not match wrapper identity"
        )
    if (
        binary_parameter(
            shell_parameters.get("ENABLE_STATE_WITNESS"),
            f"{top}: {shell_path} ENABLE_STATE_WITNESS",
        )
        != 0
    ):
        fail(f"{top}: witness-enabled scheduler shell is not a cost wrapper")
    if len(reached_banks) != 4:
        fail(
            f"{top}: reached {len(reached_banks)} bank instances, expected four"
        )
    for bank_path, bank in reached_banks:
        if (
            binary_parameter(
                bank.get("parameter_default_values", {}).get(
                    "ENABLE_STATE_WITNESS"
                ),
                f"{top}: {bank_path} ENABLE_STATE_WITNESS",
            )
            != 0
        ):
            fail(f"{top}: witness-enabled bank is not valid cost evidence")

    expected_records = {}
    for state_class, members in BEHAVIORAL_MEMBERS.items():
        for member, width in members.items():
            count = compute_tokens if state_class == "token" else 1
            expected_records[(state_class, "behavioral", member)] = (
                count,
                width,
                "net",
            )
    for state_class, member in RESERVED_MEMBERS.items():
        expected_records[(state_class, "model_floor_reserved", member)] = (
            1,
            RESERVED_WIDTHS[compute_tokens][state_class],
            "net",
        )
    expected_records[("bank", "physical_memory", "paired_store_bank")] = (
        4,
        10240,
        "memory",
    )

    grouped = collections.defaultdict(list)
    for record in records:
        key = (record["class"], record["kind"], record["member"])
        if key not in expected_records:
            fail(f"{top}: unexpected retained-state class/kind/member {key}")
        grouped[key].append(record)
    for key, (
        expected_count,
        expected_width,
        expected_object,
    ) in expected_records.items():
        actual = grouped.get(key, [])
        if len(actual) != expected_count:
            fail(
                f"{top}: {key} occurs {len(actual)} times instead of "
                f"{expected_count}"
            )
        for record in actual:
            if record["object"] != expected_object:
                fail(f"{top}: {key} is represented by the wrong object type")
            if record["bits"] != expected_width:
                fail(
                    f"{top}: {record['path']} has {record['bits']} bits instead "
                    f"of {expected_width}"
                )
            if expected_object == "memory" and (
                record["width"],
                record["size"],
                record["address_bits"],
            ) != (640, 16, 4):
                fail(
                    f"{top}: {record['path']} geometry is "
                    f"{record['size']}x{record['width']} ABITS={record['address_bits']} "
                    "instead of 16x640 ABITS=4"
                )

    physical_memory_bits = sum(
        record["bits"]
        for record in records
        if record["kind"] == "physical_memory"
    )
    model_floor_reserved_bits = sum(
        record["bits"]
        for record in records
        if record["kind"] == "model_floor_reserved"
    )
    behavioral_retained_bits = sum(
        record["bits"] for record in records if record["kind"] == "behavioral"
    )
    allocation_bits = (
        physical_memory_bits
        + model_floor_reserved_bits
        + behavioral_retained_bits
    )
    if physical_memory_bits != 40960:
        fail(
            f"{top}: physical memory total is {physical_memory_bits}, expected 40960"
        )
    expected_behavioral = compute_tokens * 471 + 526
    if behavioral_retained_bits != expected_behavioral:
        fail(
            f"{top}: behavioral retained total is {behavioral_retained_bits}, "
            f"expected {expected_behavioral}"
        )
    expected_reserved = 1582 if compute_tokens == 24 else 1584
    if model_floor_reserved_bits != expected_reserved:
        fail(
            f"{top}: model-floor reserved total is {model_floor_reserved_bits}, "
            f"expected {expected_reserved}"
        )
    if allocation_bits != expected_total:
        fail(
            f"{top}: allocation is {allocation_bits}, expected {expected_total}"
        )

    return {
        "top": top,
        "compute_tokens": compute_tokens,
        "fp_issue_width": issue_width,
        "token_entry_count": compute_tokens,
        "bank_memory_count": 4,
        "behavioral_retained_bits": behavioral_retained_bits,
        "model_floor_reserved_bits": model_floor_reserved_bits,
        "physical_memory_bits": physical_memory_bits,
        "cost_shell_allocation_bits": allocation_bits,
    }


def validate_designs(documents):
    supplied = set(documents)
    expected = set(WRAPPERS)
    if supplied != expected:
        fail(
            "wrapper set differs; missing={} extra={}".format(
                sorted(expected - supplied), sorted(supplied - expected)
            )
        )
    wrappers = []
    for top in WRAPPERS:
        wrappers.append(validate_design(documents[top], top, *WRAPPERS[top]))
    return {
        "schema": "lanl_maa_umt_retained_state_audit_v1",
        "status": "passed",
        "evidence_class": "structural_cost_shell_allocation_only",
        "wrappers": wrappers,
        "claim_boundary": {
            "behavioral_state": (
                "Only tagged token and named control/counter registers are "
                "behavioral retained-state evidence."
            ),
            "model_floor_reserved": (
                "Reserved bits reproduce a model-floor allocation and are not "
                "implemented C++ field or RTL behavior evidence."
            ),
            "excluded": [
                "C++/RTL trace closure",
                "memory macro characterization",
                "area",
                "Fmax",
                "power or energy",
                "routed physical cost",
            ],
        },
    }


def parse_design_argument(value):
    if "=" not in value:
        fail(f"design argument must be TOP=PATH, got {value!r}")
    top, path = value.split("=", 1)
    if not top or not path:
        fail(f"design argument must be TOP=PATH, got {value!r}")
    return top, Path(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design",
        action="append",
        required=True,
        metavar="TOP=JSON",
        help="one post-proc Yosys JSON design; all four fixed wrappers required",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    documents = {}
    source_hashes = {}
    try:
        for value in args.design:
            top, path = parse_design_argument(value)
            if top in documents:
                fail(f"duplicate design argument for {top}")
            payload = path.read_bytes()
            documents[top] = json.loads(payload)
            source_hashes[top] = {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        report = validate_designs(documents)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        parser.exit(1, f"UMT_RETAINED_STATE_AUDIT_FAIL: {error}\n")

    report["sources"] = source_hashes
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded)
    print("UMT_RETAINED_STATE_AUDIT_PASS")
    for wrapper in report["wrappers"]:
        print(
            "{top}: behavioral={behavioral_retained_bits} "
            "reserved={model_floor_reserved_bits} memory={physical_memory_bits} "
            "allocation={cost_shell_allocation_bits}".format(**wrapper)
        )


if __name__ == "__main__":
    main()
