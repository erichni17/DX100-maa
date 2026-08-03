#!/usr/bin/env python3
"""Executable safety contract for a dedicated logical SPD-cache transport.

This is deliberately not a timing or performance model.  It models finite
records, ownership, retry, exact response validation, abort-drain, and the two
private-slot cache rules proposed by the accompanying design document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional

DESCRIPTORS = 2
PAGES_PER_DESCRIPTOR = 4
SLOTS = 2
FP64_ELEMENTS_PER_PAGE = 4096
LINE_BYTES = 64
PAGE_BYTES = FP64_ELEMENTS_PER_PAGE * 8
LINES_PER_PAGE = PAGE_BYTES // LINE_BYTES

TRANSACTION_CAPACITY = 8
REQUEST_QUEUE_CAPACITY = 8
RESPONSE_CREDITS = 4

GENERATION_MAX = (1 << 32) - 1
ACTION_ID_MAX = (1 << 32) - 1
PACKET_ID_MAX = (1 << 32) - 1
RECORD_EPOCH_MAX = (1 << 16) - 1
PIN_MAX = (1 << 8) - 1
DIAGNOSTIC_MAX = (1 << 32) - 1


class ContractError(ValueError):
    """A caller requested a transition that the contract does not enable."""


class ExhaustedError(ContractError):
    """A bounded, non-wrapping identity space has been exhausted."""


class Operation(str, Enum):
    FILL = "fill"
    WRITEBACK = "writeback"


class SlotPhase(str, Enum):
    EMPTY = "empty"
    FILLING = "filling"
    CLEAN = "clean"
    DIRTY = "dirty"
    WRITEBACK = "writeback"


class RecordState(str, Enum):
    FREE = "free"
    QUEUED = "queued"
    PENDING_SEND = "pending_send"
    WAIT_RETRY = "wait_retry"
    IN_FLIGHT = "in_flight"
    ABORT_DRAIN = "abort_drain"


class ActionState(str, Enum):
    FREE = "free"
    ACTIVE = "active"
    ABORT_DRAIN = "abort_drain"


class ReplyStatus(str, Enum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    ABORT_DRAINED = "abort_drained"
    DUPLICATE_OR_STALE = "duplicate_or_stale"
    FOREIGN = "foreign"
    CORRUPT_OWNER_ABORTED = "corrupt_owner_aborted"
    ABORT_OWNER_DRAINED = "abort_owner_drained"


@dataclass(frozen=True)
class TransactionKey:
    descriptor: int
    generation: int
    slot: int
    page: int
    line: int
    operation: Operation


@dataclass(frozen=True)
class Reply:
    """The fields visible to the dedicated response callback.

    ``record`` and ``epoch`` are an opaque bounded sender token.  The callback
    first finds ``packet_id`` in its own fixed records.  It never dereferences
    a request or sender wrapper before that authoritative lookup succeeds.
    """

    record: int
    epoch: int
    packet_id: int
    key: TransactionKey
    address: int
    command: str
    size: int
    port: int


@dataclass
class Descriptor:
    allocated: bool = False
    generation: int = 0
    backing_ready: int = 0
    writeback_acked: int = 0


@dataclass
class Slot:
    phase: SlotPhase = SlotPhase.EMPTY
    descriptor: int = -1
    generation: int = 0
    page: int = -1
    pins: int = 0
    action_id: int = 0

    def clear(self) -> None:
        self.phase = SlotPhase.EMPTY
        self.descriptor = -1
        self.generation = 0
        self.page = -1
        self.pins = 0
        self.action_id = 0


@dataclass
class TransactionRecord:
    state: RecordState = RecordState.FREE
    epoch: int = 0
    packet_id: int = 0
    action_id: int = 0
    key: TransactionKey | None = None
    address: int = 0
    request_command: str = ""
    response_command: str = ""
    size: int = 0
    port: int = -1
    credit: int = -1

    def release(self) -> None:
        epoch = self.epoch
        self.__dict__.update(TransactionRecord(epoch=epoch).__dict__)


@dataclass
class PageAction:
    state: ActionState = ActionState.FREE
    action_id: int = 0
    operation: Operation | None = None
    descriptor: int = -1
    generation: int = 0
    page: int = -1
    slot: int = -1
    base_address: int = 0
    port: int = -1
    next_line: int = 0
    issued_bits: int = 0
    ack_bits: int = 0
    ack_count: int = 0
    abort_reason: str = ""

    def clear(self) -> None:
        self.__dict__.update(PageAction().__dict__)


class DedicatedTransport:
    """One-action finite transport with fixed arrays and no event history."""

    def __init__(self) -> None:
        self.records = [
            TransactionRecord() for _ in range(TRANSACTION_CAPACITY)
        ]
        self.queue = [-1] * REQUEST_QUEUE_CAPACITY
        self.queue_head = 0
        self.queue_tail = 0
        self.queue_count = 0
        self.pending = -1
        self.credit_owner = [-1] * RESPONSE_CREDITS
        self.action = PageAction()
        self.next_action_id = 1
        self.action_ids_exhausted = False
        self.next_packet_id = 1
        self.packet_ids_exhausted = False
        self.sealed = False
        self.fault_foreign = 0
        self.fault_stale = 0
        self.fault_corrupt = 0

    def _increment_fault(self, name: str) -> None:
        value = getattr(self, name)
        setattr(self, name, min(value + 1, DIAGNOSTIC_MAX))

    def _queue_push(self, record: int) -> None:
        if self.queue_count >= REQUEST_QUEUE_CAPACITY:
            raise ContractError("request queue is full")
        self.queue[self.queue_tail] = record
        self.queue_tail = (self.queue_tail + 1) % REQUEST_QUEUE_CAPACITY
        self.queue_count += 1

    def _queue_pop(self) -> int:
        if self.queue_count == 0:
            raise ContractError("request queue is empty")
        record = self.queue[self.queue_head]
        self.queue[self.queue_head] = -1
        self.queue_head = (self.queue_head + 1) % REQUEST_QUEUE_CAPACITY
        self.queue_count -= 1
        return record

    def _allocate_record(self) -> int:
        for index, record in enumerate(self.records):
            if (
                record.state == RecordState.FREE
                and record.epoch < RECORD_EPOCH_MAX
            ):
                record.epoch += 1
                return index
        if all(record.epoch >= RECORD_EPOCH_MAX for record in self.records):
            raise ExhaustedError("all transaction-record epochs exhausted")
        raise ContractError("transaction records are full")

    def start_action(
        self,
        operation: Operation,
        descriptor: int,
        generation: int,
        page: int,
        slot: int,
        base_address: int,
        port: int,
    ) -> int:
        """Validate completely before committing a page action."""
        if self.sealed:
            raise ContractError("transport is sealed")
        if self.action.state != ActionState.FREE:
            raise ContractError("another page action owns the transport")
        if not isinstance(operation, Operation):
            raise ContractError("operation is invalid")
        if not 0 <= descriptor < DESCRIPTORS:
            raise ContractError("descriptor is out of range")
        if not 1 <= generation <= GENERATION_MAX:
            raise ContractError("generation is invalid")
        if not 0 <= page < PAGES_PER_DESCRIPTOR:
            raise ContractError("page is out of range")
        if not 0 <= slot < SLOTS:
            raise ContractError("slot is out of range")
        if base_address < 0 or base_address % PAGE_BYTES:
            raise ContractError("page base is not 32-KiB aligned")
        if not 0 <= port < 256:
            raise ContractError("port is out of range")
        if self.action_ids_exhausted:
            raise ExhaustedError("action identity exhausted")
        remaining_record_epochs = sum(
            RECORD_EPOCH_MAX - record.epoch for record in self.records
        )
        if remaining_record_epochs < LINES_PER_PAGE:
            raise ExhaustedError(
                "transaction-record epochs cannot cover a complete page"
            )
        packet_id_capacity = PACKET_ID_MAX - self.next_packet_id + 1
        if self.packet_ids_exhausted or packet_id_capacity < LINES_PER_PAGE:
            raise ExhaustedError(
                "packet identities cannot cover a complete page"
            )

        action_id = self.next_action_id
        if action_id == ACTION_ID_MAX:
            self.action_ids_exhausted = True
        else:
            self.next_action_id += 1
        self.action = PageAction(
            state=ActionState.ACTIVE,
            action_id=action_id,
            operation=operation,
            descriptor=descriptor,
            generation=generation,
            page=page,
            slot=slot,
            base_address=base_address,
            port=port,
        )
        self.refill_queue()
        self.assert_invariants()
        return action_id

    def refill_queue(self) -> None:
        """Materialize requests only into charged fixed transaction records."""
        action = self.action
        if action.state != ActionState.ACTIVE:
            return
        while action.next_line < LINES_PER_PAGE:
            try:
                index = self._allocate_record()
            except ExhaustedError as error:
                raise AssertionError(
                    "admission failed to reserve record identities"
                ) from error
            except ContractError:
                break
            if self.packet_ids_exhausted:
                self.records[index].release()
                raise ExhaustedError("packet identity exhausted")
            line = action.next_line
            key = TransactionKey(
                descriptor=action.descriptor,
                generation=action.generation,
                slot=action.slot,
                page=action.page,
                line=line,
                operation=action.operation,
            )
            record = self.records[index]
            record.state = RecordState.QUEUED
            record.packet_id = self.next_packet_id
            if self.next_packet_id == PACKET_ID_MAX:
                self.packet_ids_exhausted = True
            else:
                self.next_packet_id += 1
            record.action_id = action.action_id
            record.key = key
            record.address = action.base_address + line * LINE_BYTES
            record.request_command = (
                "ReadReq" if action.operation == Operation.FILL else "WriteReq"
            )
            record.response_command = (
                "ReadResp"
                if action.operation == Operation.FILL
                else "WriteResp"
            )
            record.size = LINE_BYTES
            record.port = action.port
            self._queue_push(index)
            action.next_line += 1
            action.issued_bits |= 1 << line

    def select_pending(self) -> int | None:
        if self.pending != -1:
            return self.pending
        if self.queue_count == 0:
            return None
        index = self._queue_pop()
        record = self.records[index]
        if record.state != RecordState.QUEUED:
            raise AssertionError(
                "queue does not authoritatively own its record"
            )
        record.state = RecordState.PENDING_SEND
        self.pending = index
        self.assert_invariants()
        return index

    def try_send(self, accepted: bool) -> int | None:
        if accepted and -1 not in self.credit_owner:
            raise ContractError("response credits are exhausted")
        index = self.select_pending()
        if index is None:
            return None
        record = self.records[index]
        if record.state == RecordState.WAIT_RETRY:
            raise ContractError("send is blocked until recvReqRetry")
        if record.state != RecordState.PENDING_SEND:
            raise AssertionError("pending index and state disagree")
        if not accepted:
            record.state = RecordState.WAIT_RETRY
            self.assert_invariants()
            return None
        credit = self.credit_owner.index(-1)
        self.credit_owner[credit] = index
        record.credit = credit
        record.state = RecordState.IN_FLIGHT
        self.pending = -1
        self.assert_invariants()
        return index

    def recv_req_retry(self, port: int) -> None:
        if self.pending == -1:
            raise ContractError("retry callback has no pending owner")
        record = self.records[self.pending]
        if record.state != RecordState.WAIT_RETRY:
            raise ContractError("retry callback is unexpected")
        if port != record.port:
            raise ContractError("retry callback arrived on the wrong port")
        record.state = RecordState.PENDING_SEND
        self.assert_invariants()

    def expected_reply(self, index: int) -> Reply:
        record = self.records[index]
        if record.state not in (
            RecordState.IN_FLIGHT,
            RecordState.ABORT_DRAIN,
        ):
            raise ContractError("record has no response obligation")
        assert record.key is not None
        return Reply(
            record=index,
            epoch=record.epoch,
            packet_id=record.packet_id,
            key=record.key,
            address=record.address,
            command=record.response_command,
            size=record.size,
            port=record.port,
        )

    def _lookup_owned_packet(self, packet_id: int) -> int:
        matches = [
            index
            for index, record in enumerate(self.records)
            if record.state != RecordState.FREE
            and record.packet_id == packet_id
        ]
        if len(matches) > 1:
            raise AssertionError("packet identity has multiple owners")
        return matches[0] if matches else -1

    def _release_record(self, index: int) -> None:
        record = self.records[index]
        if record.credit != -1:
            if self.credit_owner[record.credit] != index:
                raise AssertionError("credit ledger owner mismatch")
            self.credit_owner[record.credit] = -1
        record.release()

    def _action_has_records(self) -> bool:
        action_id = self.action.action_id
        return any(
            record.state != RecordState.FREE and record.action_id == action_id
            for record in self.records
        )

    def _finish_abort_if_drained(self) -> bool:
        if (
            self.action.state == ActionState.ABORT_DRAIN
            and not self._action_has_records()
        ):
            self.action.clear()
            return True
        return False

    def abort_action(self, reason: str) -> bool:
        """Cancel local records; retain every sent PacketPtr until reply."""
        if self.action.state == ActionState.FREE:
            return True
        self.action.state = ActionState.ABORT_DRAIN
        self.action.abort_reason = reason

        # The queue and pending owner are local: their packets were never sent.
        for index, record in enumerate(self.records):
            if record.action_id != self.action.action_id:
                continue
            if record.state in (
                RecordState.QUEUED,
                RecordState.PENDING_SEND,
                RecordState.WAIT_RETRY,
            ):
                if self.pending == index:
                    self.pending = -1
                self._release_record(index)
            elif record.state == RecordState.IN_FLIGHT:
                record.state = RecordState.ABORT_DRAIN
        self.queue = [-1] * REQUEST_QUEUE_CAPACITY
        self.queue_head = 0
        self.queue_tail = 0
        self.queue_count = 0
        drained = self._finish_abort_if_drained()
        self.assert_invariants()
        return drained

    def receive(self, reply: Reply) -> ReplyStatus:
        """Consume one response without ever probing native ownership state."""
        index = self._lookup_owned_packet(reply.packet_id)
        if index == -1:
            if 0 <= reply.record < TRANSACTION_CAPACITY and (
                reply.epoch <= self.records[reply.record].epoch
            ):
                self._increment_fault("fault_stale")
                return ReplyStatus.DUPLICATE_OR_STALE
            self._increment_fault("fault_foreign")
            return ReplyStatus.FOREIGN

        record = self.records[index]
        if record.state == RecordState.ABORT_DRAIN:
            self._release_record(index)
            drained = self._finish_abort_if_drained()
            self.assert_invariants()
            return (
                ReplyStatus.ABORT_DRAINED
                if drained
                else ReplyStatus.ABORT_OWNER_DRAINED
            )

        exact = (
            record.state == RecordState.IN_FLIGHT
            and reply.record == index
            and reply.epoch == record.epoch
            and reply.key == record.key
            and reply.address == record.address
            and reply.command == record.response_command
            and reply.size == record.size
            and reply.port == record.port
        )
        if not exact:
            self._increment_fault("fault_corrupt")
            # The packet pointer names this exact owner, so consume it first;
            # Siblings become abort-drain owners; unrelated state is untouched.
            self._release_record(index)
            self.abort_action("corrupt response for owned packet")
            return ReplyStatus.CORRUPT_OWNER_ABORTED

        assert record.key is not None
        line = record.key.line
        if self.action.ack_bits & (1 << line):
            # This is unreachable for a live unique PacketPtr, but remains
            # fail-closed if model state is deliberately fault-injected.
            self._increment_fault("fault_corrupt")
            self._release_record(index)
            self.abort_action("duplicate ACK bit on live packet")
            return ReplyStatus.CORRUPT_OWNER_ABORTED

        self.action.ack_bits |= 1 << line
        self.action.ack_count += 1
        self._release_record(index)
        self.refill_queue()

        if (
            self.action.next_line == LINES_PER_PAGE
            and self.action.ack_count == LINES_PER_PAGE
            and self.action.ack_bits == (1 << LINES_PER_PAGE) - 1
            and not self._action_has_records()
        ):
            self.action.clear()
            self.assert_invariants()
            return ReplyStatus.COMPLETED
        self.assert_invariants()
        return ReplyStatus.ACCEPTED

    def drained(self) -> bool:
        return (
            self.action.state == ActionState.FREE
            and self.queue_count == 0
            and self.pending == -1
            and all(r.state == RecordState.FREE for r in self.records)
            and all(owner == -1 for owner in self.credit_owner)
        )

    def seal(self) -> None:
        if not self.drained():
            raise ContractError("transport must drain before teardown")
        self.sealed = True

    def assert_invariants(self) -> None:
        queued = [
            self.queue[(self.queue_head + offset) % REQUEST_QUEUE_CAPACITY]
            for offset in range(self.queue_count)
        ]
        if len(set(queued)) != len(queued) or any(x < 0 for x in queued):
            raise AssertionError("request queue contains invalid ownership")
        for index, record in enumerate(self.records):
            in_queue = index in queued
            is_pending = index == self.pending
            credit_count = self.credit_owner.count(index)
            if record.state == RecordState.FREE:
                assert not in_queue and not is_pending and credit_count == 0
                assert record.packet_id == 0 and record.key is None
            elif record.state == RecordState.QUEUED:
                assert in_queue and not is_pending and credit_count == 0
            elif record.state in (
                RecordState.PENDING_SEND,
                RecordState.WAIT_RETRY,
            ):
                assert is_pending and not in_queue and credit_count == 0
            elif record.state in (
                RecordState.IN_FLIGHT,
                RecordState.ABORT_DRAIN,
            ):
                assert not in_queue and not is_pending and credit_count == 1
                assert self.credit_owner[record.credit] == index
            assert 0 <= record.epoch <= RECORD_EPOCH_MAX
        assert self.queue_count <= REQUEST_QUEUE_CAPACITY
        assert (
            sum(owner != -1 for owner in self.credit_owner) <= RESPONSE_CREDITS
        )
        if self.action.state == ActionState.FREE:
            assert not any(r.state != RecordState.FREE for r in self.records)
        else:
            assert 0 <= self.action.ack_count <= self.action.next_line
            assert self.action.next_line <= LINES_PER_PAGE
            assert self.action.ack_bits & ~self.action.issued_bits == 0


class LogicalCacheModel:
    """Two descriptors and two private FP64 slots around the transport."""

    def __init__(self) -> None:
        self.descriptors = [Descriptor() for _ in range(DESCRIPTORS)]
        self.slots = [Slot() for _ in range(SLOTS)]
        self.transport = DedicatedTransport()
        self.active_slot = -1
        self.active_operation: Operation | None = None
        self.last_abort = ""
        self.torn_down = False

    def allocate(self, descriptor: int) -> int:
        if not 0 <= descriptor < DESCRIPTORS:
            raise ContractError("descriptor is out of range")
        item = self.descriptors[descriptor]
        if item.allocated:
            raise ContractError("descriptor is already allocated")
        if item.generation >= GENERATION_MAX:
            raise ExhaustedError("descriptor generation exhausted")
        item.generation += 1
        item.allocated = True
        item.backing_ready = 0
        item.writeback_acked = 0
        return item.generation

    def publish_backing(self, descriptor: int, page: int) -> None:
        item = self._descriptor_page(descriptor, page)
        item.backing_ready |= 1 << page

    def _descriptor_page(self, descriptor: int, page: int) -> Descriptor:
        if not 0 <= descriptor < DESCRIPTORS:
            raise ContractError("descriptor is out of range")
        if not 0 <= page < PAGES_PER_DESCRIPTOR:
            raise ContractError("page is out of range")
        item = self.descriptors[descriptor]
        if not item.allocated:
            raise ContractError("descriptor is free")
        return item

    def _slot(self, slot: int) -> Slot:
        if not 0 <= slot < SLOTS:
            raise ContractError("slot is out of range")
        return self.slots[slot]

    def start_fill(
        self, descriptor: int, page: int, slot: int, base: int, port: int
    ) -> int:
        item = self._descriptor_page(descriptor, page)
        if not (item.backing_ready & (1 << page)):
            raise ContractError("fill has no backing-ready page")
        target = self._slot(slot)
        if target.phase != SlotPhase.EMPTY:
            raise ContractError("fill requires an empty slot")
        # Transport commit precedes slot mutation after full validation.
        action_id = self.transport.start_action(
            Operation.FILL,
            descriptor,
            item.generation,
            page,
            slot,
            base,
            port,
        )
        target.phase = SlotPhase.FILLING
        target.descriptor = descriptor
        target.generation = item.generation
        target.page = page
        target.action_id = action_id
        self.active_slot = slot
        self.active_operation = Operation.FILL
        self.assert_invariants()
        return action_id

    def pin(self, slot: int) -> None:
        target = self._slot(slot)
        if target.phase not in (SlotPhase.CLEAN, SlotPhase.DIRTY):
            raise ContractError("only a resident slot can be pinned")
        if target.pins >= PIN_MAX:
            raise ExhaustedError("pin counter exhausted")
        target.pins += 1

    def mark_dirty(self, slot: int) -> None:
        target = self._slot(slot)
        if target.phase not in (SlotPhase.CLEAN, SlotPhase.DIRTY):
            raise ContractError("dirtying requires a resident slot")
        if target.pins == 0:
            raise ContractError("dirtying requires a pin")
        target.phase = SlotPhase.DIRTY

    def unpin(self, slot: int) -> None:
        target = self._slot(slot)
        if target.pins == 0:
            raise ContractError("slot has no pin")
        target.pins -= 1

    def bind_dirty_destination(
        self, slot: int, descriptor: int, page: int
    ) -> None:
        """Bind compute-produced dirty payload to an exact live destination.

        Compute datapath behavior is outside this transport model.  This
        explicit transition prevents tests from mutating ownership as an
        external oracle: it validates and records the destination generation
        while the producer still holds the slot pin.
        """
        destination = self._descriptor_page(descriptor, page)
        target = self._slot(slot)
        if target.phase != SlotPhase.DIRTY or target.pins == 0:
            raise ContractError(
                "destination binding requires pinned dirty payload"
            )
        if target.descriptor == descriptor:
            raise ContractError(
                "source and destination descriptors must differ"
            )
        if destination.backing_ready & (1 << page):
            raise ContractError("destination page is already backing-ready")
        if destination.writeback_acked & (1 << page):
            raise ContractError("destination page is already acknowledged")
        if any(
            other is not target
            and other.descriptor == descriptor
            and other.generation == destination.generation
            and other.page == page
            for other in self.slots
        ):
            raise ContractError("destination page already has a slot owner")
        target.descriptor = descriptor
        target.generation = destination.generation
        target.page = page

    def evict_clean(self, slot: int) -> None:
        target = self._slot(slot)
        if target.phase != SlotPhase.CLEAN or target.pins:
            raise ContractError("only an unpinned clean slot may be discarded")
        target.clear()

    def start_writeback(self, slot: int, base: int, port: int) -> int:
        target = self._slot(slot)
        if target.phase != SlotPhase.DIRTY or target.pins:
            raise ContractError("writeback requires an unpinned dirty slot")
        action_id = self.transport.start_action(
            Operation.WRITEBACK,
            target.descriptor,
            target.generation,
            target.page,
            slot,
            base,
            port,
        )
        target.phase = SlotPhase.WRITEBACK
        target.action_id = action_id
        self.active_slot = slot
        self.active_operation = Operation.WRITEBACK
        self.assert_invariants()
        return action_id

    def receive(self, reply: Reply) -> ReplyStatus:
        status = self.transport.receive(reply)
        if status == ReplyStatus.COMPLETED:
            slot = self.slots[self.active_slot]
            if self.active_operation == Operation.FILL:
                if slot.phase != SlotPhase.FILLING:
                    raise AssertionError("fill completion has no slot owner")
                slot.phase = SlotPhase.CLEAN
                slot.action_id = 0
            elif self.active_operation == Operation.WRITEBACK:
                if slot.phase != SlotPhase.WRITEBACK:
                    raise AssertionError(
                        "writeback completion has no slot owner"
                    )
                descriptor = self.descriptors[slot.descriptor]
                if (
                    not descriptor.allocated
                    or descriptor.generation != slot.generation
                ):
                    raise AssertionError("writeback owner became stale")
                descriptor.writeback_acked |= 1 << slot.page
                slot.clear()
            self.active_slot = -1
            self.active_operation = None
        elif (
            status
            in (
                ReplyStatus.ABORT_DRAINED,
                ReplyStatus.CORRUPT_OWNER_ABORTED,
            )
            and self.transport.drained()
        ):
            self._finish_aborted_slot()
        self.assert_invariants()
        return status

    def abort(self, reason: str) -> bool:
        self.last_abort = reason
        drained = self.transport.abort_action(reason)
        if drained:
            self._finish_aborted_slot()
        self.assert_invariants()
        return drained

    def _finish_aborted_slot(self) -> None:
        if self.active_slot == -1:
            return
        slot = self.slots[self.active_slot]
        if self.active_operation == Operation.FILL:
            slot.clear()
        elif self.active_operation == Operation.WRITEBACK:
            slot.phase = SlotPhase.DIRTY
            slot.action_id = 0
        self.active_slot = -1
        self.active_operation = None

    def descriptor_complete(self, descriptor: int) -> bool:
        item = self.descriptors[descriptor]
        return (
            item.allocated
            and item.writeback_acked == (1 << PAGES_PER_DESCRIPTOR) - 1
        )

    def free_descriptor(self, descriptor: int) -> None:
        item = self.descriptors[descriptor]
        if not item.allocated:
            raise ContractError("descriptor is already free")
        if any(slot.descriptor == descriptor for slot in self.slots):
            raise ContractError("descriptor still owns a slot")
        item.allocated = False
        item.backing_ready = 0
        item.writeback_acked = 0

    def reset(self) -> None:
        if not self.transport.drained():
            raise ContractError("reset requires a drained transport")
        if any(
            slot.phase in (SlotPhase.DIRTY, SlotPhase.WRITEBACK)
            for slot in self.slots
        ):
            raise ContractError("reset cannot discard dirty data")
        if any(slot.pins for slot in self.slots):
            raise ContractError("reset cannot discard pins")
        for slot in self.slots:
            slot.clear()
        for descriptor in self.descriptors:
            descriptor.allocated = False
            descriptor.backing_ready = 0
            descriptor.writeback_acked = 0
        self.active_slot = -1
        self.active_operation = None

    def teardown(self) -> None:
        if any(item.allocated for item in self.descriptors):
            raise ContractError("teardown requires free descriptors")
        if any(slot.phase != SlotPhase.EMPTY for slot in self.slots):
            raise ContractError("teardown requires empty slots")
        self.transport.seal()
        self.torn_down = True

    def assert_invariants(self) -> None:
        self.transport.assert_invariants()
        action_slots = [slot for slot in self.slots if slot.action_id]
        assert len(action_slots) <= 1
        if self.transport.action.state == ActionState.FREE:
            assert not action_slots
            assert self.active_slot == -1
        else:
            assert len(action_slots) == 1
            assert action_slots[0].action_id == self.transport.action.action_id
            assert self.active_slot in range(SLOTS)
        for slot in self.slots:
            if slot.phase == SlotPhase.EMPTY:
                assert slot.descriptor == -1 and slot.pins == 0
            else:
                assert 0 <= slot.descriptor < DESCRIPTORS
                assert 0 <= slot.page < PAGES_PER_DESCRIPTOR
                assert slot.generation > 0
            if slot.pins:
                assert slot.phase in (SlotPhase.CLEAN, SlotPhase.DIRTY)
            if slot.phase in (SlotPhase.DIRTY, SlotPhase.WRITEBACK):
                assert self.descriptors[slot.descriptor].allocated

    def digest(self) -> str:
        """Deterministic state fingerprint; no history is retained in state."""

        def record_state(record: TransactionRecord) -> dict[str, object]:
            key = record.key
            return {
                **record.__dict__,
                "state": record.state.value,
                "key": (
                    {
                        **key.__dict__,
                        "operation": key.operation.value,
                    }
                    if key
                    else None
                ),
            }

        state = {
            "descriptors": [item.__dict__ for item in self.descriptors],
            "slots": [
                {**slot.__dict__, "phase": slot.phase.value}
                for slot in self.slots
            ],
            "transport": {
                "action": {
                    **self.transport.action.__dict__,
                    "state": self.transport.action.state.value,
                    "operation": (
                        self.transport.action.operation.value
                        if self.transport.action.operation
                        else None
                    ),
                },
                "queue": self.transport.queue,
                "queue_head": self.transport.queue_head,
                "queue_tail": self.transport.queue_tail,
                "queue_count": self.transport.queue_count,
                "pending": self.transport.pending,
                "credits": self.transport.credit_owner,
                "records": [
                    record_state(record) for record in self.transport.records
                ],
                "next_action_id": self.transport.next_action_id,
                "action_ids_exhausted": (self.transport.action_ids_exhausted),
                "next_packet_id": self.transport.next_packet_id,
                "packet_ids_exhausted": (self.transport.packet_ids_exhausted),
                "sealed": self.transport.sealed,
                "faults": (
                    self.transport.fault_foreign,
                    self.transport.fault_stale,
                    self.transport.fault_corrupt,
                ),
            },
            "active_slot": self.active_slot,
            "active_operation": (
                self.active_operation.value if self.active_operation else None
            ),
            "last_abort": self.last_abort,
            "torn_down": self.torn_down,
        }
        encoded = json.dumps(state, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()


STORAGE_LEDGER_BYTES = {
    "private_fp64_payload": 2 * PAGE_BYTES,
    "descriptor_records": 2 * 16,
    "slot_records": 2 * 16,
    "page_action_and_two_line_bitmaps": 160,
    "transaction_records": TRANSACTION_CAPACITY * 40,
    "request_fifo_and_control": 16,
    "response_credit_ledger": 8,
    "dedicated_line_buffers": RESPONSE_CREDITS * LINE_BYTES,
    "bounded_fault_and_control_counters": 32,
}


def drive_action(model: LogicalCacheModel) -> None:
    """Accept and respond deterministically until the page completes."""
    while not model.transport.drained():
        while -1 in model.transport.credit_owner:
            index = model.transport.try_send(True)
            if index is None:
                break
        inflight = [
            index
            for index, record in enumerate(model.transport.records)
            if record.state == RecordState.IN_FLIGHT
        ]
        if not inflight:
            raise AssertionError("active action made no forward progress")
        for index in reversed(inflight):
            model.receive(model.transport.expected_reply(index))


def deterministic_demo() -> dict[str, object]:
    model = LogicalCacheModel()
    source_generation = model.allocate(0)
    destination_generation = model.allocate(1)
    for page in range(PAGES_PER_DESCRIPTOR):
        model.publish_backing(0, page)
        slot = page & 1
        model.start_fill(0, page, slot, page * PAGE_BYTES, 0)
        drive_action(model)
        model.pin(slot)
        model.mark_dirty(slot)
        # The executable demo treats the resident page as the destination of a
        # bounded compute step; compute datapath semantics are out of scope.
        model.bind_dirty_destination(slot, 1, page)
        model.unpin(slot)
        model.start_writeback(slot, 0x100000 + page * PAGE_BYTES, 0)
        drive_action(model)
    return {
        "source_generation": source_generation,
        "destination_generation": destination_generation,
        "destination_complete": model.descriptor_complete(1),
        "digest": model.digest(),
        "state_bytes_per_maa": sum(STORAGE_LEDGER_BYTES.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.demo:
        print(json.dumps(deterministic_demo(), sort_keys=True))
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
