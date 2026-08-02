#!/usr/bin/env python3
"""Deterministic static checks for the repaired four-sorted-run design."""

from __future__ import annotations

import copy
import heapq
import struct
import unittest
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = (
    ROOT
    / "experiments/analysis/sorted_runs_gem5_integration_design_2026-08-02.md"
)

UINT64_MAX = (1 << 64) - 1
CACHE_LINE_BYTES = 64
ELEMENTS = 16_384
RUNS = 4
RUN_ELEMENTS = 4_096
RECORD_BYTES = 32
B_BYTES = ELEMENTS * 4
C_BYTES = ELEMENTS * 8
RUN_BYTES = RUN_ELEMENTS * RECORD_BYTES
DESCRIPTOR_BYTES = ELEMENTS * RECORD_BYTES
B_LINES = B_BYTES // CACHE_LINE_BYTES
C_LINES = C_BYTES // CACHE_LINE_BYTES
RUN_LINES = RUN_BYTES // CACHE_LINE_BYTES
DESCRIPTOR_LINES = DESCRIPTOR_BYTES // CACHE_LINE_BYTES
C_OWNER_CAPACITY = 16
ACTION_OWNER_CAPACITY = 22
MAX_LIVE_ACTIONS = 21

RECORD = struct.Struct("<QQQIHBB")


def covered_lines(base: int, byte_count: int) -> int:
    if base < 0 or byte_count <= 0:
        raise ValueError("line coverage requires a non-negative base and size")
    return ((base & (CACHE_LINE_BYTES - 1)) + byte_count + 63) // 64


def checked_end(base: int, size: int) -> int:
    if base < 0 or size <= 0 or base > UINT64_MAX - size:
        raise ValueError("address span wraps or is empty")
    return base + size


def spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


@dataclass
class AdmissionState:
    mutations: int = 0
    admitted: bool = False


def admit(
    state: AdmissionState,
    *,
    a_base: int,
    a_elements: int,
    b_base: int,
    c_base: int,
    descriptor_base: int,
) -> None:
    """Validate into locals and commit one state mutation only at the end."""
    if a_base % 8:
        raise ValueError("A must be FP64 aligned")
    if b_base % 64 or c_base % 64 or descriptor_base % 64:
        raise ValueError("B, C, and descriptor backing must be line aligned")
    if a_elements <= 0 or a_elements > UINT64_MAX // 8:
        raise ValueError("invalid A element count")
    spans = (
        (a_base, checked_end(a_base, a_elements * 8)),
        (b_base, checked_end(b_base, B_BYTES)),
        (c_base, checked_end(c_base, C_BYTES)),
        (descriptor_base, checked_end(descriptor_base, DESCRIPTOR_BYTES)),
    )
    for left in range(len(spans)):
        for right in range(left + 1, len(spans)):
            if spans_overlap(spans[left], spans[right]):
                raise ValueError("operation spans overlap")
    if state.admitted:
        raise ValueError("only one operation may be admitted")
    state.admitted = True
    state.mutations += 1


@dataclass(frozen=True)
class Record:
    a_line_vaddr: int
    a_line_paddr: int
    grow: int
    destination: int
    slice_rank: int
    source_word: int
    flags: int = 1

    def validate(self) -> None:
        if self.a_line_vaddr % 64 or self.a_line_paddr % 64:
            raise ValueError("A record line is not aligned")
        if not 0 <= self.destination < ELEMENTS:
            raise ValueError("destination is out of range")
        if not 0 <= self.slice_rank < (1 << 16):
            raise ValueError("slice rank is out of range")
        if not 0 <= self.source_word < 8:
            raise ValueError("source word is out of range")
        if self.flags != 1:
            raise ValueError("record flags are invalid or reserved")
        for field in (self.a_line_vaddr, self.a_line_paddr, self.grow):
            if not 0 <= field <= UINT64_MAX:
                raise ValueError("64-bit record field is out of range")

    def pack(self) -> bytes:
        self.validate()
        encoded = RECORD.pack(
            self.a_line_vaddr,
            self.a_line_paddr,
            self.grow,
            self.destination,
            self.slice_rank,
            self.source_word,
            self.flags,
        )
        if len(encoded) != RECORD_BYTES:
            raise AssertionError("record ABI drift")
        return encoded

    @classmethod
    def unpack(cls, encoded: bytes) -> Record:
        if len(encoded) != RECORD_BYTES:
            raise ValueError("record encoding has the wrong width")
        result = cls(*RECORD.unpack(encoded))
        result.validate()
        return result


def record_key(record: Record) -> tuple[int, int, int, int, int, int]:
    record.validate()
    return (
        record.slice_rank,
        record.grow,
        record.a_line_paddr,
        record.source_word,
        record.destination,
        record.a_line_vaddr,
    )


def merge_runs(runs: list[list[Record]]) -> list[Record]:
    """Four-way merge with the specified run-number tie breaker."""
    heap: list[tuple[tuple[int, ...], int, int, Record]] = []
    for run_number, run in enumerate(runs):
        if run:
            heapq.heappush(heap, (record_key(run[0]), run_number, 0, run[0]))
    merged: list[Record] = []
    while heap:
        _, run_number, index, record = heapq.heappop(heap)
        merged.append(record)
        next_index = index + 1
        if next_index < len(runs[run_number]):
            following = runs[run_number][next_index]
            heapq.heappush(
                heap,
                (record_key(following), run_number, next_index, following),
            )
    return merged


@dataclass(frozen=True)
class ActionTag:
    generation: int
    transaction: int
    serial: int
    expected_vline: int
    expected_pline: int
    maa: int
    line_index: int
    action: int
    slot: int
    run: int
    command: int


@dataclass
class ActionOwner:
    state: str = "free"
    tag: ActionTag | None = None
    packet_token: int = 0
    retry: bool = False
    stream_credit_owned: bool = False
    port_credit_owned: bool = False
    sender_state_owned: bool = False


class ActionLedger:
    """Small executable model of exact owner/ACK conservation."""

    def __init__(self) -> None:
        self.owners = [ActionOwner() for _ in range(ACTION_OWNER_CAPACITY)]
        self.live = 0
        self.started = 0
        self.translation_completed = 0
        self.packet_created = 0
        self.send_accepted = 0
        self.response_retired = 0
        self.translation_only_retired = 0
        self.rejections: dict[str, int] = {}

    def start(self, slot: int, tag: ActionTag) -> None:
        owner = self.owners[slot]
        if owner.state not in ("free", "retired"):
            raise OverflowError("action slot is occupied")
        if (
            owner.state == "retired"
            and owner.tag is not None
            and tag.generation == owner.tag.generation
            and tag.transaction == owner.tag.transaction
            and tag.serial <= owner.tag.serial
        ):
            raise ValueError("action serial did not advance")
        if self.live == MAX_LIVE_ACTIONS:
            raise OverflowError("live action bound exceeded")
        if tag.generation == 0 or tag.transaction == 0 or tag.serial == 0:
            raise ValueError("zero action identity is invalid")
        if tag.slot != slot:
            raise ValueError("tag names the wrong owner slot")
        owner.state = "translating"
        owner.tag = tag
        owner.packet_token = 0
        owner.retry = False
        owner.stream_credit_owned = True
        owner.port_credit_owned = False
        owner.sender_state_owned = False
        self.live += 1
        self.started += 1

    def translation_response(
        self, slot: int, tag: ActionTag, *, packet_bearing: bool
    ) -> str:
        owner = self.owners[slot]
        if owner.state != "translating" or owner.tag != tag:
            return self._reject("stale_translation")
        self.translation_completed += 1
        if packet_bearing:
            owner.state = "ready"
        else:
            owner.state = "retired"
            owner.stream_credit_owned = False
            self.live -= 1
            self.translation_only_retired += 1
        return "accepted"

    def create_packet(self, slot: int, packet_token: int) -> None:
        owner = self.owners[slot]
        if owner.state != "ready" or packet_token == 0:
            raise ValueError("packet creation is not authorized")
        owner.packet_token = packet_token
        owner.sender_state_owned = True
        self.packet_created += 1

    def send_rejected(self, slot: int) -> tuple[ActionOwner, tuple[int, ...]]:
        owner = self.owners[slot]
        if owner.state != "ready" or owner.packet_token == 0:
            raise ValueError("only a ready packet can be retried")
        before = copy.deepcopy(owner)
        counters = self._conservation_counters()
        owner.retry = True
        return before, counters

    def send(self, slot: int) -> None:
        owner = self.owners[slot]
        if owner.state != "ready" or owner.packet_token == 0:
            raise ValueError("packet is not ready")
        owner.state = "sent"
        owner.retry = False
        owner.port_credit_owned = True
        self.send_accepted += 1

    def abort_unsent(self, slot: int) -> None:
        owner = self.owners[slot]
        if owner.state not in ("translating", "ready"):
            raise ValueError("only an unsent action can abort")
        if not owner.stream_credit_owned or owner.port_credit_owned:
            raise AssertionError("unsent credit ownership is inconsistent")
        owner.state = "retired"
        owner.packet_token = 0
        owner.stream_credit_owned = False
        owner.sender_state_owned = False
        self.live -= 1

    def response(
        self,
        slot: int,
        tag: ActionTag,
        packet_token: int,
        *,
        response_size: int = 64,
        sender_depth: int = 1,
    ) -> str:
        owner = self.owners[slot]
        if owner.state == "retired" and owner.tag == tag:
            return self._reject("duplicate")
        if owner.state != "sent" or owner.tag is None:
            return self._reject("stale")
        expected = owner.tag
        if tag.generation != expected.generation:
            return self._reject("wrong_generation")
        if (
            tag.transaction != expected.transaction
            or tag.serial != expected.serial
        ):
            return self._reject("stale")
        if tag.expected_vline != expected.expected_vline or (
            tag.expected_pline != expected.expected_pline
        ):
            return self._reject("wrong_address")
        if tag.command != expected.command or tag.action != expected.action:
            return self._reject("wrong_command")
        if tag != expected:
            return self._reject("wrong_tag")
        if response_size != CACHE_LINE_BYTES:
            return self._reject("wrong_size")
        if packet_token != owner.packet_token:
            return self._reject("wrong_packet")
        if sender_depth != 1:
            return self._reject("sender_chain")
        if not owner.port_credit_owned or not owner.sender_state_owned:
            return self._reject("owned_corruption")
        owner.state = "retired"
        owner.packet_token = 0
        owner.stream_credit_owned = False
        owner.port_credit_owned = False
        owner.sender_state_owned = False
        self.live -= 1
        self.response_retired += 1
        return "retired"

    def _reject(self, result: str) -> str:
        self.rejections[result] = self.rejections.get(result, 0) + 1
        return result

    def _conservation_counters(self) -> tuple[int, ...]:
        return (
            self.live,
            self.started,
            self.translation_completed,
            self.packet_created,
            self.send_accepted,
            self.response_retired,
            self.translation_only_retired,
        )

    def assert_clean_conservation(self) -> None:
        if self.live != 0:
            raise AssertionError("live actions remain")
        if (
            self.started
            != self.response_retired + self.translation_only_retired
        ):
            raise AssertionError(
                "started actions were not retired exactly once"
            )
        if not (
            self.packet_created == self.send_accepted == self.response_retired
        ):
            raise AssertionError("packet/ACK conservation failed")


@dataclass
class CLineOwner:
    line: int = 0
    mask: int = 0
    state: str = "free"


class CLineOwners:
    def __init__(self) -> None:
        self.owners = [CLineOwner() for _ in range(C_OWNER_CAPACITY)]

    def insert(self, line: int, word: int) -> bool:
        if line % 64 or not 0 <= word < 8:
            raise ValueError("invalid C line or word")
        for owner in self.owners:
            if owner.state != "free" and owner.line == line:
                if owner.state == "in_flight":
                    return False
                bit = 1 << word
                if owner.mask & bit:
                    raise ValueError("duplicate destination word")
                owner.mask |= bit
                return True
        for owner in self.owners:
            if owner.state == "free":
                owner.line = line
                owner.mask = 1 << word
                owner.state = "accumulating"
                return True
        return False

    def issue(self, line: int) -> int:
        for owner in self.owners:
            if owner.line == line and owner.state == "accumulating":
                owner.state = "in_flight"
                return owner.mask
        raise ValueError("C line is not issuable")

    def ack(self, line: int, mask: int) -> bool:
        for owner in self.owners:
            if owner.line == line and owner.state == "in_flight":
                if owner.mask != mask:
                    return False
                owner.line = 0
                owner.mask = 0
                owner.state = "free"
                return True
        return False


class FixedPortAliases:
    """Fixed alias inventory required before cleanup or fatal disposition."""

    KINDS = ("outstanding", "deferred", "pending_send")

    def __init__(self) -> None:
        self.locations = {
            kind: [0] * ACTION_OWNER_CAPACITY for kind in self.KINDS
        }

    def place(self, kind: str, slot: int, packet_token: int) -> None:
        if kind not in self.locations or packet_token == 0:
            raise ValueError("invalid port alias")
        self.locations[kind][slot] = packet_token

    def find(self, packet_token: int) -> list[tuple[str, int]]:
        return [
            (kind, slot)
            for kind, entries in self.locations.items()
            for slot, token in enumerate(entries)
            if token == packet_token
        ]

    def cleanup(self, packet_token: int) -> int:
        aliases = self.find(packet_token)
        for kind, slot in aliases:
            self.locations[kind][slot] = 0
        if self.find(packet_token):
            raise AssertionError("packet alias survived cleanup")
        return len(aliases)


@dataclass
class DrainRestoreModel:
    generation: int = 7
    transaction: int = 11
    next_serial: int = 19
    geometry: int = 0xA501
    phase: str = "idle"
    admission_blocked: bool = False
    live_owners: int = 0

    def drain(self) -> str:
        self.admission_blocked = True
        if self.phase == "idle" and self.live_owners == 0:
            return "drained"
        return "draining"

    def retire_and_finish(self) -> str:
        if self.live_owners <= 0:
            raise ValueError("no live owner to retire")
        self.live_owners -= 1
        if self.live_owners == 0:
            self.phase = "idle"
            return "signalDrainDone"
        return "draining"

    def serialize(self) -> dict[str, int]:
        if (
            not self.admission_blocked
            or self.phase != "idle"
            or self.live_owners
        ):
            raise RuntimeError("live checkpoint is forbidden")
        return {
            "generation": self.generation,
            "transaction": self.transaction,
            "next_serial": self.next_serial,
            "geometry": self.geometry,
        }

    @classmethod
    def unserialize(
        cls, state: dict[str, int], geometry: int
    ) -> DrainRestoreModel:
        if state["geometry"] != geometry:
            raise ValueError("checkpoint geometry mismatch")
        if (
            min(
                state["generation"], state["transaction"], state["next_serial"]
            )
            <= 0
        ):
            raise ValueError("checkpoint restored a zero allocator")
        return cls(
            generation=state["generation"],
            transaction=state["transaction"],
            next_serial=state["next_serial"],
            geometry=geometry,
            phase="idle",
            admission_blocked=True,
            live_owners=0,
        )


def make_tag(serial: int, slot: int = 0, generation: int = 3) -> ActionTag:
    return ActionTag(
        generation=generation,
        transaction=5,
        serial=serial,
        expected_vline=0x1000 + serial * 0x40,
        expected_pline=0x9000 + serial * 0x40,
        maa=0,
        line_index=serial,
        action=1,
        slot=slot,
        run=0,
        command=2,
    )


class SortedRunsDesignContractTest(unittest.TestCase):
    def test_alignment_rejection_is_atomic_and_edge_lines_are_exact(
        self,
    ) -> None:
        self.assertEqual(covered_lines(0x200000, B_BYTES), 1_024)
        self.assertEqual(covered_lines(0x200004, B_BYTES), 1_025)
        self.assertEqual(covered_lines(0x300000, C_BYTES), 2_048)
        self.assertEqual(covered_lines(0x300008, C_BYTES), 2_049)
        self.assertEqual(covered_lines(0x400000, DESCRIPTOR_BYTES), 8_192)
        self.assertEqual(covered_lines(0x400020, DESCRIPTOR_BYTES), 8_193)

        for field, delta in (
            ("b_base", 4),
            ("c_base", 8),
            ("descriptor_base", 32),
        ):
            values = {
                "a_base": 0x100000,
                "a_elements": 4096,
                "b_base": 0x200000,
                "c_base": 0x300000,
                "descriptor_base": 0x400000,
            }
            values[field] += delta
            state = AdmissionState()
            with self.assertRaises(ValueError):
                admit(state, **values)
            self.assertEqual(state, AdmissionState())

        state = AdmissionState()
        admit(
            state,
            a_base=0x100008,
            a_elements=4096,
            b_base=0x200000,
            c_base=0x300000,
            descriptor_base=0x400000,
        )
        self.assertTrue(state.admitted)
        self.assertEqual(state.mutations, 1)
        with self.assertRaises(ValueError):
            checked_end(UINT64_MAX - 31, 64)

    def test_record_round_trip_preserves_virtual_and_physical_identity(
        self,
    ) -> None:
        record = Record(
            a_line_vaddr=0x7FFF_1234_5000,
            a_line_paddr=0x0000_00AB_C000,
            grow=0x1234_5678_9ABC,
            destination=16_383,
            slice_rank=513,
            source_word=7,
        )
        self.assertEqual(RECORD.size, 32)
        encoded = record.pack()
        self.assertEqual(Record.unpack(encoded), record)
        self.assertNotEqual(record.a_line_vaddr, record.a_line_paddr)
        with self.assertRaises(ValueError):
            Record.unpack(encoded[:-1])
        corrupted = bytearray(encoded)
        corrupted[31] = 0x81
        with self.assertRaises(ValueError):
            Record.unpack(bytes(corrupted))

    def test_comparator_is_total_transitive_and_merge_is_deterministic(
        self,
    ) -> None:
        records = [
            Record(0x1000, 0x9000, 7, 9, 2, 1),
            Record(0x2000, 0x9000, 7, 7, 2, 1),
            Record(0x3000, 0x8000, 7, 8, 2, 1),
            Record(0x4000, 0xA000, 6, 6, 1, 4),
            Record(0x5000, 0xA000, 6, 5, 1, 4),
            Record(0x6000, 0x7000, 9, 4, 3, 0),
        ]
        for left, right in permutations(records, 2):
            self.assertNotEqual(record_key(left), record_key(right))
            self.assertNotEqual(
                record_key(left) < record_key(right),
                record_key(right) < record_key(left),
            )
        ordered = sorted(records, key=record_key)
        for first, second, third in permutations(ordered, 3):
            if record_key(first) < record_key(second) < record_key(third):
                self.assertLess(record_key(first), record_key(third))

        runs = [
            sorted(records[index::RUNS], key=record_key)
            for index in range(RUNS)
        ]
        self.assertEqual(merge_runs(runs), ordered)

        duplicate = records[0]
        tied_runs = [[duplicate], [duplicate], [], []]
        self.assertEqual(merge_runs(tied_runs), [duplicate, duplicate])

    def test_bounded_occupancy_has_no_growth_escape(self) -> None:
        active = [None] * RUN_ELEMENTS
        for index in range(RUN_ELEMENTS):
            active[index] = index
        with self.assertRaises(IndexError):
            active[RUN_ELEMENTS] = RUN_ELEMENTS

        ledger = ActionLedger()
        for slot in range(MAX_LIVE_ACTIONS):
            ledger.start(slot, make_tag(slot + 1, slot=slot))
        self.assertEqual(ledger.live, 21)
        with self.assertRaises(OverflowError):
            ledger.start(21, make_tag(22, slot=21))
        self.assertEqual(len(ledger.owners), 22)
        self.assertEqual(C_OWNER_CAPACITY, 16)

    def test_retry_and_exact_action_ack_conservation(self) -> None:
        ledger = ActionLedger()
        for serial in range(1, 9):
            slot = serial - 1
            tag = make_tag(serial, slot=slot)
            ledger.start(slot, tag)
            self.assertEqual(
                ledger.translation_response(slot, tag, packet_bearing=True),
                "accepted",
            )
            ledger.create_packet(slot, 1000 + serial)
            before, counters = ledger.send_rejected(slot)
            self.assertEqual(before.tag, ledger.owners[slot].tag)
            self.assertEqual(
                before.packet_token, ledger.owners[slot].packet_token
            )
            self.assertEqual(counters, ledger._conservation_counters())
            ledger.send(slot)
            self.assertEqual(
                ledger.response(slot, tag, 1000 + serial), "retired"
            )

        for serial in range(9, 13):
            slot = serial - 1
            tag = make_tag(serial, slot=slot)
            ledger.start(slot, tag)
            self.assertEqual(
                ledger.translation_response(slot, tag, packet_bearing=False),
                "accepted",
            )
        ledger.assert_clean_conservation()
        self.assertEqual(ledger.started, 12)
        self.assertEqual(ledger.response_retired, 8)
        self.assertEqual(ledger.translation_only_retired, 4)

    def test_stale_duplicate_and_forged_responses_do_not_mutate_owner(
        self,
    ) -> None:
        ledger = ActionLedger()
        tag = make_tag(1)
        ledger.start(0, tag)
        ledger.translation_response(0, tag, packet_bearing=True)
        ledger.create_packet(0, 77)
        ledger.send(0)

        variants = (
            (
                ActionTag(**{**tag.__dict__, "generation": 4}),
                77,
                "wrong_generation",
            ),
            (
                ActionTag(**{**tag.__dict__, "expected_pline": 0xDEAD_0000}),
                77,
                "wrong_address",
            ),
            (ActionTag(**{**tag.__dict__, "command": 9}), 77, "wrong_command"),
            (tag, 78, "wrong_packet"),
        )
        for forged, packet, expected in variants:
            before = copy.deepcopy(ledger.owners[0])
            self.assertEqual(ledger.response(0, forged, packet), expected)
            self.assertEqual(ledger.owners[0], before)

        self.assertEqual(ledger.response(0, tag, 77), "retired")
        after_retire = copy.deepcopy(ledger.owners[0])
        self.assertEqual(ledger.response(0, tag, 77), "duplicate")
        self.assertEqual(ledger.owners[0], after_retire)
        stale = ActionTag(**{**tag.__dict__, "serial": 99})
        self.assertEqual(ledger.response(0, stale, 77), "stale")
        self.assertEqual(ledger.owners[0], after_retire)

    def test_rejected_transport_defects_are_explicit_fail_closed_gates(
        self,
    ) -> None:
        ledger = ActionLedger()
        unsent = make_tag(1)
        ledger.start(0, unsent)
        self.assertTrue(ledger.owners[0].stream_credit_owned)
        ledger.abort_unsent(0)
        self.assertFalse(ledger.owners[0].stream_credit_owned)
        self.assertEqual(ledger.live, 0)
        with self.assertRaises(ValueError):
            ledger.abort_unsent(0)

        sent = make_tag(2)
        ledger.start(0, sent)
        ledger.translation_response(0, sent, packet_bearing=True)
        ledger.create_packet(0, 88)
        ledger.send(0)
        for kwargs, result in (
            ({"response_size": 8}, "wrong_size"),
            ({"sender_depth": 2}, "sender_chain"),
        ):
            before = copy.deepcopy(ledger.owners[0])
            self.assertEqual(ledger.response(0, sent, 88, **kwargs), result)
            self.assertEqual(ledger.owners[0], before)
        self.assertEqual(ledger.response(0, sent, 88), "retired")

        aliases = FixedPortAliases()
        aliases.place("outstanding", 0, 91)
        aliases.place("pending_send", 0, 91)
        aliases.place("deferred", 1, 92)
        self.assertEqual(
            aliases.find(91), [("outstanding", 0), ("pending_send", 0)]
        )
        self.assertEqual(aliases.cleanup(91), 2)
        self.assertEqual(aliases.find(92), [("deferred", 1)])

    def test_c_owner_same_address_waits_for_exact_ack(self) -> None:
        owners = CLineOwners()
        for index in range(C_OWNER_CAPACITY):
            self.assertTrue(owners.insert(0x1000 + index * 64, index % 8))
        self.assertFalse(owners.insert(0x9000, 0))

        mask = owners.issue(0x1000)
        before = copy.deepcopy(owners.owners)
        self.assertFalse(owners.insert(0x1000, 7))
        self.assertFalse(owners.ack(0x1000, mask ^ 1))
        self.assertEqual(owners.owners, before)
        self.assertTrue(owners.ack(0x1000, mask))
        self.assertTrue(owners.insert(0x1000, 7))

    def test_drain_serialize_restore_is_quiescent_and_monotonic(self) -> None:
        model = DrainRestoreModel(phase="merge", live_owners=2)
        self.assertEqual(model.drain(), "draining")
        with self.assertRaises(RuntimeError):
            model.serialize()
        self.assertEqual(model.retire_and_finish(), "draining")
        self.assertEqual(model.retire_and_finish(), "signalDrainDone")
        state = model.serialize()
        restored = DrainRestoreModel.unserialize(state, model.geometry)
        self.assertEqual(restored.phase, "idle")
        self.assertTrue(restored.admission_blocked)
        self.assertEqual(restored.live_owners, 0)
        self.assertEqual(restored.next_serial, model.next_serial)
        with self.assertRaises(ValueError):
            DrainRestoreModel.unserialize(state, model.geometry + 1)

    def test_byte_and_action_ledgers_recompute_exactly(self) -> None:
        self.assertEqual(B_BYTES, 65_536)
        self.assertEqual(C_BYTES, 131_072)
        self.assertEqual(RUN_BYTES, 131_072)
        self.assertEqual(DESCRIPTOR_BYTES, 524_288)
        self.assertEqual(
            (B_LINES, C_LINES, RUN_LINES, DESCRIPTOR_LINES),
            (1_024, 2_048, 2_048, 8_192),
        )

        packed_rows = {
            "active_records": 131_072,
            "run_buffers": 256,
            "b_payload": 64,
            "a_payload": 64,
            "c_owners": 1_216,
            "action_owners": 1_408,
            "coverage": 2_048,
            "operation": 208,
            "runs": 176,
            "heap": 48,
            "merge": 36,
            "pages": 32,
            "conservation": 144,
        }
        self.assertEqual(sum(packed_rows.values()), 136_772)
        self.assertEqual(sum(packed_rows.values()) - 131_072, 5_700)
        self.assertEqual(sum(packed_rows.values()) + 4_096, 140_868)

        fixed_serial_actions = 1_024 + 16_384 + 8_192 + 8_192 + 16_384
        self.assertEqual(fixed_serial_actions, 50_176)
        self.assertEqual(fixed_serial_actions + 16_384, 66_560)
        fixed_packet_responses = 1_024 + 8_192 + 8_192
        self.assertEqual(fixed_packet_responses, 17_408)
        self.assertEqual(fixed_packet_responses + 1 + 2_048, 19_457)
        self.assertEqual(fixed_packet_responses + 16_384 + 16_384, 50_176)
        self.assertEqual(fixed_serial_actions + 2_048, 52_224)

        minimum_payload = B_BYTES + 2 * DESCRIPTOR_BYTES + 64 + C_BYTES
        maximum_payload = (
            B_BYTES
            + 2 * DESCRIPTOR_BYTES
            + ELEMENTS * CACHE_LINE_BYTES
            + ELEMENTS * CACHE_LINE_BYTES
        )
        self.assertEqual(minimum_payload, 1_245_248)
        self.assertEqual(maximum_payload, 3_211_264)
        self.assertEqual(2 * DESCRIPTOR_BYTES, 1_048_576)
        maximum_extra_carry = ELEMENTS * CACHE_LINE_BYTES - C_BYTES
        self.assertEqual(maximum_extra_carry, 917_504)
        self.assertEqual(2 * DESCRIPTOR_BYTES + maximum_extra_carry, 1_966_080)

    def test_document_contains_repaired_contract_and_no_stale_headlines(
        self,
    ) -> None:
        design = DESIGN.read_text(encoding="utf-8")
        for required in (
            "record is **32 bytes**",
            "off-chip coherent image",
            "1,025 lines",
            "aLineVaddr",
            "sliceRank",
            "FatalUnownedExtra",
            "**136,772**",
            "**1,245,248 bytes**",
            "**3,211,264 bytes**",
            "Latency,\nspeedup, area, power",
            "BLOCKED for production implementation",
            "READY_FOR_INDEPENDENT_REVIEW",
            "deferred/pending-send aliases",
            "unsent read could leak",
            "arbitrary residual sender-state chains",
        ):
            self.assertIn(required, design)
        for stale in (
            "103 B",
            "Each record is exactly 16 B",
            "256-KiB descriptor",
        ):
            self.assertNotIn(stale, design)


if __name__ == "__main__":
    unittest.main()
