#!/usr/bin/env python3

import copy
import importlib.util
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validate_umt_retained_state.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_umt_retained_state", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def bits(width, first):
    return list(range(first, first + width))


def tagged_net(state_class, kind, member, width, first):
    return {
        "hide_name": 0,
        "bits": bits(width, first),
        "attributes": {
            "keep": "true",
            "umt_state_class": state_class,
            "umt_state_kind": kind,
            "umt_state_member": member,
        },
    }


def dff_for(net):
    return {
        "hide_name": 1,
        "type": "$dff",
        "parameters": {},
        "attributes": {},
        "port_directions": {"Q": "output"},
        "connections": {"Q": list(net["bits"])},
    }


def tagged_state_module(compute_tokens, issue_width, name):
    netnames = {}
    cells = {}
    first = 2

    def add(state_class, kind, member, width):
        nonlocal first
        net = tagged_net(state_class, kind, member, width, first)
        netnames[member] = net
        cells[f"{member}$dff"] = dff_for(net)
        first += width

    for state_class in ("functional", "bank_scheduler", "instrumentation"):
        for member, width in MODULE.BEHAVIORAL_MEMBERS[state_class].items():
            add(state_class, "behavioral", member, width)
        member = MODULE.RESERVED_MEMBERS[state_class]
        add(
            state_class,
            "model_floor_reserved",
            member,
            MODULE.RESERVED_WIDTHS[compute_tokens][state_class],
        )
    for index in range(compute_tokens):
        cells[f"token_entry_gen[{index}].entry"] = {
            "type": "LanlUmtTokenEntry",
            "attributes": {},
            "connections": {},
        }
    for index in range(4):
        cells[f"bank{index}Instance"] = {
            "type": "LanlUmtBank16x640WitnessOff",
            "attributes": {},
            "connections": {},
        }
    return {
        "attributes": {"hdlname": "LanlUmtSchedulerShell"},
        "parameter_default_values": {
            "COMPUTE_TOKENS": f"{compute_tokens:032b}",
            "FP_ISSUE_WIDTH": f"{issue_width:032b}",
            "ENABLE_STATE_WITNESS": f"{0:032b}",
        },
        "ports": {},
        "cells": cells,
        "netnames": netnames,
    }


def make_document(top, compute_tokens, issue_width):
    token_net = tagged_net("token", "behavioral", "token_entry", 471, 2)
    token_module = {
        "attributes": {"hdlname": "LanlUmtTokenEntry"},
        "ports": {},
        "cells": {"state$dff": dff_for(token_net)},
        "netnames": {"stateReg": token_net},
    }
    memory_cell = {
        "hide_name": 0,
        "type": "$mem_v2",
        "parameters": {
            "WIDTH": f"{640:032b}",
            "SIZE": f"{16:032b}",
            "ABITS": f"{4:032b}",
        },
        "attributes": {
            "keep": "true",
            "umt_state_class": "bank",
            "umt_state_kind": "physical_memory",
            "umt_state_member": "paired_store_bank",
        },
        "connections": {},
    }
    bank_module = {
        "attributes": {"hdlname": "LanlUmtBank16x640"},
        "parameter_default_values": {"ENABLE_STATE_WITNESS": f"{0:032b}"},
        "ports": {},
        "cells": {"memory": memory_cell},
        "netnames": {},
    }
    shell_name = f"shell_{compute_tokens}_{issue_width}"
    top_module = {
        "attributes": {"top": f"{1:032b}"},
        "ports": {},
        "cells": {"shell": {"type": shell_name, "attributes": {}}},
        "netnames": {},
    }
    return {
        "modules": {
            top: top_module,
            shell_name: tagged_state_module(
                compute_tokens, issue_width, shell_name
            ),
            "LanlUmtTokenEntry": token_module,
            "LanlUmtBank16x640WitnessOff": bank_module,
        }
    }


def make_all_documents():
    return {
        top: make_document(top, tokens, width)
        for top, (tokens, width, _total) in MODULE.WRAPPERS.items()
    }


class RetainedStateValidatorTest(unittest.TestCase):
    def assertRejected(self, documents, pattern):
        with self.assertRaisesRegex(MODULE.ValidationError, pattern):
            MODULE.validate_designs(documents)

    def test_all_four_wrappers_pass_with_separate_evidence_classes(self):
        report = MODULE.validate_designs(make_all_documents())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(len(report["wrappers"]), 4)
        self.assertEqual(
            [
                (
                    item["behavioral_retained_bits"],
                    item["model_floor_reserved_bits"],
                )
                for item in report["wrappers"]
            ],
            [(11830, 1582), (11830, 1582), (15598, 1584), (15598, 1584)],
        )
        self.assertEqual(
            [
                item["cost_shell_allocation_bits"]
                for item in report["wrappers"]
            ],
            [54372, 54372, 58142, 58142],
        )

    def test_missing_wrapper_fails_closed(self):
        documents = make_all_documents()
        del documents["LanlUmtSchedulerShellT32W2"]
        self.assertRejected(documents, "wrapper set differs; missing")

    def test_missing_named_state_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        del shell["netnames"]["current_cycle"]
        del shell["cells"]["current_cycle$dff"]
        self.assertRejected(documents, "current_cycle.*occurs 0 times")

    def test_missing_reserved_class_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        del shell["netnames"]["functional_model_floor_reserved"]
        del shell["cells"]["functional_model_floor_reserved$dff"]
        self.assertRejected(
            documents, "functional_model_floor_reserved.*occurs 0 times"
        )

    def test_extra_state_class_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        net = tagged_net("mystery", "behavioral", "extra", 1, 9000)
        shell["netnames"]["extra"] = net
        shell["cells"]["extra$dff"] = dff_for(net)
        self.assertRejected(documents, "unexpected retained-state class")

    def test_extra_member_in_known_class_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        net = tagged_net("functional", "behavioral", "unaccounted", 1, 9000)
        shell["netnames"]["unaccounted"] = net
        shell["cells"]["unaccounted$dff"] = dff_for(net)
        self.assertRejected(documents, "unexpected retained-state class")

    def test_double_charged_member_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        net = tagged_net("functional", "behavioral", "current_cycle", 64, 9000)
        shell["netnames"]["current_cycle_duplicate"] = net
        shell["cells"]["current_cycle_duplicate$dff"] = dff_for(net)
        self.assertRejected(documents, "current_cycle.*occurs 2 times")

    def test_overlapping_state_bits_fail_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        shell["netnames"]["issue_cursor"]["bits"][0] = shell["netnames"][
            "current_cycle"
        ]["bits"][0]
        self.assertRejected(documents, "double-charges bit")

    def test_wrong_memory_geometry_fails_closed(self):
        documents = make_all_documents()
        bank = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "LanlUmtBank16x640WitnessOff"
        ]
        bank["cells"]["memory"]["parameters"]["WIDTH"] = f"{639:032b}"
        self.assertRejected(documents, "has 10224 bits instead of 10240")

    def test_extra_bank_instance_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        shell["cells"]["bank4Instance"] = {
            "type": "LanlUmtBank16x640WitnessOff",
            "attributes": {},
        }
        self.assertRejected(documents, "reached 5 bank instances")

    def test_witness_enabled_shell_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        shell["parameter_default_values"]["ENABLE_STATE_WITNESS"] = f"{1:032b}"
        self.assertRejected(documents, "witness-enabled scheduler shell")

    def test_witness_enabled_bank_fails_closed(self):
        documents = make_all_documents()
        bank = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "LanlUmtBank16x640WitnessOff"
        ]
        bank["parameter_default_values"]["ENABLE_STATE_WITNESS"] = f"{1:032b}"
        self.assertRejected(documents, "witness-enabled bank")

    def test_reserved_state_misclassified_as_behavioral_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        shell["netnames"]["functional_model_floor_reserved"]["attributes"][
            "umt_state_kind"
        ] = "behavioral"
        self.assertRejected(documents, "unexpected retained-state class")

    def test_behavioral_state_misclassified_as_reserved_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        shell["netnames"]["current_cycle"]["attributes"][
            "umt_state_kind"
        ] = "model_floor_reserved"
        self.assertRejected(documents, "unexpected retained-state class")

    def test_unbacked_tagged_bits_fail_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        del shell["cells"]["functional_model_floor_reserved$dff"]
        self.assertRejected(
            documents, "sequential Q drivers instead of exactly one"
        )

    def test_tagged_state_without_keep_fails_closed(self):
        documents = make_all_documents()
        shell = documents["LanlUmtSchedulerShellT24W1"]["modules"][
            "shell_24_1"
        ]
        del shell["netnames"]["current_cycle"]["attributes"]["keep"]
        self.assertRejected(documents, "retained-state tags without keep=true")


if __name__ == "__main__":
    unittest.main()
