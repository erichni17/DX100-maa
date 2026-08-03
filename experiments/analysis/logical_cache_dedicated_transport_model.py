#!/usr/bin/env python3
"""Executable safety contract for a dedicated logical-cache transport.

This is a finite correctness/ownership model, not a timing or performance
model.  It deliberately models request and response Packet incarnations as
different objects while a persistent embedded SenderState route token (whose
fields are immutable for each live epoch) and the exact RequestPtr survive a
legal gem5 packet replacement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import (
    dataclass,
    field,
)
from enum import Enum

DESCRIPTORS = 2
PAGES_PER_DESCRIPTOR = 4
SLOTS = 2
NUM_PORTS = 4
FP64_ELEMENTS_PER_PAGE = 4096
LINE_BYTES = 64
PAGE_BYTES = FP64_ELEMENTS_PER_PAGE * 8
BACKING_SPAN = PAGES_PER_DESCRIPTOR * PAGE_BYTES
LINES_PER_PAGE = PAGE_BYTES // LINE_BYTES

TRANSACTION_CAPACITY = 8
REQUEST_QUEUE_CAPACITY = 8
RESPONSE_CREDITS = 4

U64_MAX = (1 << 64) - 1
GENERATION_MAX = (1 << 32) - 1
ACTION_ID_MAX = (1 << 32) - 1
INCARNATION_ID_MAX = (1 << 32) - 1
PEER_PACKET_ID_MAX = (1 << 64) - 1
RECORD_EPOCH_MAX = (1 << 16) - 1
PIN_MAX = (1 << 8) - 1


class ContractError(ValueError):
    """A caller requested a transition that the contract does not enable."""


class ExhaustedError(ContractError):
    """A bounded, non-wrapping identity space has been exhausted."""


class ProductionStop(RuntimeError):
    """Production must panic before touching state for an unowned token."""


class Operation(str, Enum):
    FILL = "fill"
    WRITEBACK = "writeback"


class SlotPhase(str, Enum):
    EMPTY = "empty"
    FILLING = "filling"
    CLEAN = "clean"
    DIRTY = "dirty"
    WRITEBACK = "writeback"


class SlotRole(str, Enum):
    NONE = "none"
    SOURCE = "source"
    DESTINATION = "destination"


class RecordState(str, Enum):
    FREE = "free"
    QUEUED = "queued"
    PENDING_SEND = "pending_send"
    WAIT_RETRY = "wait_retry"
    IN_FLIGHT = "in_flight"
    DELIVERING = "delivering"
    ABORT_DRAIN = "abort_drain"


class ActionState(str, Enum):
    FREE = "free"
    ACTIVE = "active"
    ABORT_DRAIN = "abort_drain"


class AbortCode(str, Enum):
    NONE = "none"
    CALLER = "caller"


class ReplyStatus(str, Enum):
    DELIVERY_PENDING = "delivery_pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    ABORT_DRAINED = "abort_drained"
    ABORT_OWNER_DRAINED = "abort_owner_drained"


@dataclass(frozen=True)
class TransactionKey:
    descriptor: int
    generation: int
    slot: int
    page: int
    line: int
    operation: Operation


@dataclass
class RouteToken:
    """Persistent embedded Packet::SenderState payload.

    Its address is stable for the life of the record.  Fields are reinitialized
    only while the record is FREE and are immutable throughout a live epoch.
    Equal copied values are never the embedded record token.
    """

    record: int
    epoch: int
    action_id: int


@dataclass(frozen=True)
class RequestPtr:
    """Host-side stand-in whose object identity models exact RequestPtr."""

    incarnation: int


@dataclass(frozen=True)
class PacketIncarnation:
    """One gem5 Packet object, containing only modeled real wire fields."""

    incarnation: int
    request: RequestPtr
    sender_stack: tuple[object, ...]
    address: int
    command: str
    size: int
    data: bytes = b""


@dataclass(frozen=True)
class DeliveryTicket:
    """Bounded, payload-free authority for one exact staged fill line."""

    record: int
    epoch: int
    action_id: int


@dataclass(frozen=True)
class ReceiveResult:
    status: ReplyStatus
    ticket: DeliveryTicket | None = None


@dataclass
class Descriptor:
    allocated: bool = False
    generation: int = 0
    backing_base: int = 0
    backing_span: int = 0
    backing_ready: int = 0
    writeback_acked: int = 0


@dataclass
class Slot:
    phase: SlotPhase = SlotPhase.EMPTY
    role: SlotRole = SlotRole.NONE
    descriptor: int = -1
    generation: int = 0
    page: int = -1
    pins: int = 0
    action_id: int = 0
    payload: bytearray = field(default_factory=lambda: bytearray(PAGE_BYTES))

    def clear(self) -> None:
        self.phase = SlotPhase.EMPTY
        self.role = SlotRole.NONE
        self.descriptor = -1
        self.generation = 0
        self.page = -1
        self.pins = 0
        self.action_id = 0
        self.payload[:] = bytes(PAGE_BYTES)


@dataclass
class TransactionRecord:
    state: RecordState = RecordState.FREE
    epoch: int = 0
    action_id: int = 0
    key: TransactionKey | None = None
    token: RouteToken | None = None
    request: RequestPtr | None = None
    packet: PacketIncarnation | None = None
    address: int = 0
    request_command: str = ""
    response_command: str = ""
    size: int = 0
    port: int = -1
    credit: int = -1
    line_buffer: bytes = b""

    def release(self) -> None:
        epoch = self.epoch
        token = self.token
        self.__dict__.update(
            TransactionRecord(epoch=epoch, token=token).__dict__
        )
        if token is not None:
            token.action_id = 0


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
    next_line: int = 0
    issued_bits: int = 0
    ack_bits: int = 0
    ack_count: int = 0
    abort_code: AbortCode = AbortCode.NONE

    def clear(self) -> None:
        self.__dict__.update(PageAction().__dict__)


def checked_span_end(base: int, span: int) -> int:
    if not isinstance(base, int) or not isinstance(span, int):
        raise ContractError("address and span must be integers")
    if base < 0 or span <= 0 or base > U64_MAX or span - 1 > U64_MAX - base:
        raise ContractError("u64 address span overflows")
    return base + span - 1


def core_port(address: int) -> int:
    """Clean-baseline core_addr: low core bits after the 64-byte offset."""
    if not 0 <= address <= U64_MAX:
        raise ContractError("address is outside u64")
    if NUM_PORTS <= 0 or NUM_PORTS & (NUM_PORTS - 1):
        raise AssertionError("modeled port count must be a power of two")
    return (address >> 6) & (NUM_PORTS - 1)


class DedicatedTransport:
    """One-action transport with fixed records, FIFO, credits, and buffers."""

    def __init__(self) -> None:
        self.records = [
            TransactionRecord(token=RouteToken(index, 0, 0))
            for index in range(TRANSACTION_CAPACITY)
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
        self.next_incarnation_id = 1
        self.incarnation_ids_exhausted = False
        self.next_peer_packet_id = 1
        self.peer_packet_ids_exhausted = False
        self.sealed = False

    def _ensure_live(self) -> None:
        if self.sealed:
            raise ContractError("transport is sealed")

    def _reserve_incarnations(self, count: int) -> tuple[int, ...]:
        """Reserve controller-created identities without partial mutation."""
        if count <= 0:
            raise AssertionError("incarnation reservation must be positive")
        available = INCARNATION_ID_MAX - self.next_incarnation_id + 1
        if self.incarnation_ids_exhausted or available < count:
            raise ExhaustedError("packet/request incarnation exhausted")
        first = self.next_incarnation_id
        last = first + count - 1
        if last == INCARNATION_ID_MAX:
            self.incarnation_ids_exhausted = True
        else:
            self.next_incarnation_id = last + 1
        return tuple(range(first, last + 1))

    def _next_peer_packet(self) -> int:
        """Allocate a test-peer identity outside the reserved action budget."""
        if self.peer_packet_ids_exhausted:
            raise ExhaustedError("test-peer packet incarnation exhausted")
        value = self.next_peer_packet_id
        if value == PEER_PACKET_ID_MAX:
            self.peer_packet_ids_exhausted = True
        else:
            self.next_peer_packet_id += 1
        return value

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

    def _allocate_record(self, action_id: int) -> int:
        for index, record in enumerate(self.records):
            if (
                record.state == RecordState.FREE
                and record.epoch < RECORD_EPOCH_MAX
            ):
                record.epoch += 1
                if record.token is None:
                    raise AssertionError(
                        "fixed record lost its embedded token"
                    )
                # The record is still FREE: this is the only legal token-field
                # reinitialization point.  Fields remain unchanged until release.
                record.token.record = index
                record.token.epoch = record.epoch
                record.token.action_id = action_id
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
        slot_payload: bytes | bytearray,
    ) -> int:
        """Validate a descriptor-derived page action before any mutation."""
        self._ensure_live()
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
        checked_span_end(base_address, PAGE_BYTES)
        if base_address % PAGE_BYTES:
            raise ContractError("derived page base is not 32-KiB aligned")
        if len(slot_payload) != PAGE_BYTES:
            raise ContractError("slot payload must be exactly one page")
        if self.action_ids_exhausted:
            raise ExhaustedError("action identity exhausted")
        remaining_epochs = sum(
            RECORD_EPOCH_MAX - record.epoch for record in self.records
        )
        if remaining_epochs < LINES_PER_PAGE:
            raise ExhaustedError(
                "transaction-record epochs cannot cover a complete page"
            )
        # Each line needs one controller-created Request and request Packet.
        # Test-peer response Packets use a disjoint host-only identity space.
        needed = 2 * LINES_PER_PAGE
        available = INCARNATION_ID_MAX - self.next_incarnation_id + 1
        if self.incarnation_ids_exhausted or available < needed:
            raise ExhaustedError(
                "packet/request incarnations cannot cover a complete page"
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
        )
        self._refill_queue()
        self.assert_invariants()
        return action_id

    def _refill_queue(self) -> None:
        action = self.action
        if action.state != ActionState.ACTIVE:
            return
        while action.next_line < LINES_PER_PAGE:
            try:
                index = self._allocate_record(action.action_id)
            except ExhaustedError as error:
                raise AssertionError(
                    "admission failed to reserve epochs"
                ) from error
            except ContractError:
                break
            line = action.next_line
            key = TransactionKey(
                action.descriptor,
                action.generation,
                action.slot,
                action.page,
                line,
                action.operation,
            )
            address = action.base_address + line * LINE_BYTES
            checked_span_end(address, LINE_BYTES)
            record = self.records[index]
            record.state = RecordState.QUEUED
            record.action_id = action.action_id
            record.key = key
            record.address = address
            record.request_command = (
                "ReadReq" if action.operation == Operation.FILL else "WriteReq"
            )
            record.response_command = (
                "ReadResp"
                if action.operation == Operation.FILL
                else "WriteResp"
            )
            record.size = LINE_BYTES
            record.port = core_port(address)
            self._queue_push(index)
            action.next_line += 1
            action.issued_bits |= 1 << line

    def _materialize_pending(
        self, slot_payload: bytes | bytearray
    ) -> int | None:
        if self.pending != -1:
            return self.pending
        if self.queue_count == 0:
            return None
        if -1 not in self.credit_owner:
            raise ContractError("response credits are exhausted")

        # All fallible identity allocation and construction precedes credit/FIFO
        # mutation.  A failure therefore leaves the QUEUED owner in place.
        credit = self.credit_owner.index(-1)
        index = self.queue[self.queue_head]
        record = self.records[index]
        if record.state != RecordState.QUEUED or record.token is None:
            raise AssertionError("queue does not own a complete record")
        if record.key is None:
            raise AssertionError("queued record has no key")
        request_id, packet_id = self._reserve_incarnations(2)
        request = RequestPtr(request_id)
        if record.key.operation == Operation.FILL:
            line_buffer = bytes(LINE_BYTES)
        else:
            start = record.key.line * LINE_BYTES
            line_buffer = bytes(
                memoryview(slot_payload)[start : start + LINE_BYTES]
            )
        packet = PacketIncarnation(
            incarnation=packet_id,
            request=request,
            sender_stack=(record.token,),
            address=record.address,
            command=record.request_command,
            size=record.size,
            data=(
                line_buffer
                if record.key.operation == Operation.WRITEBACK
                else b""
            ),
        )

        self.credit_owner[credit] = index
        popped = self._queue_pop()
        if popped != index:
            raise AssertionError("queue head changed during materialization")
        record.request = request
        record.packet = packet
        record.line_buffer = line_buffer
        record.credit = credit
        record.state = RecordState.PENDING_SEND
        self.pending = index
        return index

    def try_send(
        self,
        accepted: bool,
        slot_payload: bytes | bytearray,
    ) -> int | None:
        self._ensure_live()
        if len(slot_payload) != PAGE_BYTES:
            raise ContractError("slot payload must be exactly one page")
        index = self._materialize_pending(slot_payload)
        if index is None:
            return None
        record = self.records[index]
        if record.state == RecordState.WAIT_RETRY:
            raise ContractError("send is blocked until recvReqRetry")
        if record.state != RecordState.PENDING_SEND or record.packet is None:
            raise AssertionError("pending register is incomplete")
        if not accepted:
            record.state = RecordState.WAIT_RETRY
            self.assert_invariants()
            return None
        # Ownership of this Packet incarnation passes to the memory system.
        # The record retains only RequestPtr, embedded token, buffer, and the
        # response obligation; the callback may receive a different Packet.
        record.packet = None
        record.state = RecordState.IN_FLIGHT
        self.pending = -1
        self.assert_invariants()
        return index

    def recv_req_retry(self, callback_port: int) -> None:
        self._ensure_live()
        if self.pending == -1:
            raise ContractError("retry callback has no pending owner")
        record = self.records[self.pending]
        if record.state != RecordState.WAIT_RETRY:
            raise ContractError("retry callback is unexpected")
        if callback_port != record.port:
            raise ContractError("retry callback arrived on the wrong port")
        record.state = RecordState.PENDING_SEND
        self.assert_invariants()

    def make_response(
        self,
        index: int,
        data: bytes | None = None,
    ) -> PacketIncarnation:
        """Model a legal new response Packet carrying RequestPtr/token."""
        self._ensure_live()
        if not 0 <= index < TRANSACTION_CAPACITY:
            raise ContractError("record is out of range")
        record = self.records[index]
        if record.state not in (
            RecordState.IN_FLIGHT,
            RecordState.ABORT_DRAIN,
        ):
            raise ContractError("record has no response obligation")
        if (
            record.request is None
            or record.token is None
            or record.key is None
        ):
            raise AssertionError("owned response record is incomplete")
        if data is None:
            data = (
                bytes(LINE_BYTES)
                if record.key.operation == Operation.FILL
                else b""
            )
        return PacketIncarnation(
            incarnation=self._next_peer_packet(),
            request=record.request,
            sender_stack=(record.token,),
            address=record.address,
            command=record.response_command,
            size=record.size,
            data=bytes(data),
        )

    def _lookup_top_token(self, packet: PacketIncarnation) -> int:
        # SenderState is the only Packet member inspected before authority.
        # The returned stack must contain exactly the embedded token at top;
        # no residual wrapper and no mutable-chain search is accepted.
        stack = packet.sender_stack
        if len(stack) != 1:
            raise ProductionStop("missing, non-top, or residual route token")
        top = stack[0]
        matches = [
            index
            for index, record in enumerate(self.records)
            if record.state
            in (
                RecordState.IN_FLIGHT,
                RecordState.DELIVERING,
                RecordState.ABORT_DRAIN,
            )
            and record.token is top
        ]
        if len(matches) != 1:
            raise ProductionStop(
                "unknown, copied, duplicate, or stale route token"
            )
        index = matches[0]
        record = self.records[index]
        if not isinstance(top, RouteToken) or (
            top.record != index
            or top.epoch != record.epoch
            or top.action_id != record.action_id
        ):
            raise ProductionStop("route token identity fields are corrupt")
        return index

    @staticmethod
    def _wire_exact(
        record: TransactionRecord, packet: PacketIncarnation
    ) -> bool:
        if record.key is None or record.request is None:
            return False
        response_commands = (
            ("ReadResp", "ReadRespWithInvalidate")
            if record.key.operation == Operation.FILL
            else ("WriteResp",)
        )
        payload_exact = (
            len(packet.data) == LINE_BYTES
            if record.key.operation == Operation.FILL
            else packet.data == b""
        )
        return (
            packet.request is record.request
            and packet.address == record.address
            and 0 <= packet.address <= U64_MAX
            and packet.command in response_commands
            and packet.size == LINE_BYTES
            and payload_exact
        )

    @staticmethod
    def _callback_port_exact(
        record: TransactionRecord, callback_port: int
    ) -> bool:
        return callback_port == record.port == core_port(record.address)

    def _release_record(self, index: int) -> None:
        record = self.records[index]
        if record.credit == -1 or self.credit_owner[record.credit] != index:
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

    def abort_action(self, code: AbortCode) -> bool:
        self._ensure_live()
        if not isinstance(code, AbortCode) or code == AbortCode.NONE:
            raise ContractError("abort code is invalid")
        if self.action.state == ActionState.FREE:
            return True
        self.action.state = ActionState.ABORT_DRAIN
        self.action.abort_code = code
        for index, record in enumerate(self.records):
            if record.action_id != self.action.action_id:
                continue
            if record.state in (
                RecordState.QUEUED,
                RecordState.PENDING_SEND,
                RecordState.WAIT_RETRY,
                RecordState.DELIVERING,
            ):
                if self.pending == index:
                    self.pending = -1
                # Queued has no credit; pending/refused owns a local Packet and
                # one credit.  Delete/release both without entering memory.
                if record.credit != -1:
                    self.credit_owner[record.credit] = -1
                record.release()
            elif record.state == RecordState.IN_FLIGHT:
                record.state = RecordState.ABORT_DRAIN
        self.queue = [-1] * REQUEST_QUEUE_CAPACITY
        self.queue_head = self.queue_tail = self.queue_count = 0
        drained = self._finish_abort_if_drained()
        self.assert_invariants()
        return drained

    def receive(
        self,
        packet: PacketIncarnation,
        callback_port: int,
    ) -> ReceiveResult:
        """Authenticate token, endpoint, and wire before staging a response."""
        self._ensure_live()
        index = self._lookup_top_token(packet)
        record = self.records[index]
        if not self._callback_port_exact(record, callback_port):
            raise ProductionStop(
                "owned route token returned on wrong callback port"
            )
        if not self._wire_exact(record, packet):
            # Production panics immediately.  The Python exception may be
            # caught only to inspect the pre-panic state: no ACK, payload copy,
            # owner release, deletion, abort transition, or recovery occurs.
            raise ProductionStop(
                "owned route token carries malformed response"
            )

        if record.state == RecordState.ABORT_DRAIN:
            self._release_record(index)
            drained = self._finish_abort_if_drained()
            self.assert_invariants()
            return ReceiveResult(
                ReplyStatus.ABORT_DRAINED
                if drained
                else ReplyStatus.ABORT_OWNER_DRAINED
            )

        if record.state != RecordState.IN_FLIGHT or record.key is None:
            raise ProductionStop("owned response is already delivering")
        line = record.key.line
        if self.action.ack_bits & (1 << line):
            raise AssertionError("live token points at an acknowledged line")
        if record.key.operation == Operation.FILL:
            # This replaces the contents of the same credit-owned fixed buffer;
            # no result object or fifth payload owner is created.
            record.line_buffer = bytes(packet.data)
            record.state = RecordState.DELIVERING
            ticket = DeliveryTicket(index, record.epoch, record.action_id)
            self.assert_invariants()
            return ReceiveResult(ReplyStatus.DELIVERY_PENDING, ticket)

        status = self._ack_release_and_refill(index)
        return ReceiveResult(status)

    def validate_delivery_ticket(self, ticket: DeliveryTicket) -> int:
        """Validate a payload-free ticket without exposing or changing data."""
        self._ensure_live()
        if not isinstance(ticket, DeliveryTicket):
            raise ProductionStop("delivery ticket type is invalid")
        if not 0 <= ticket.record < TRANSACTION_CAPACITY:
            raise ProductionStop("delivery ticket record is invalid")
        record = self.records[ticket.record]
        if (
            record.state != RecordState.DELIVERING
            or record.epoch != ticket.epoch
            or record.action_id != ticket.action_id
            or record.key is None
            or record.key.operation != Operation.FILL
            or self.action.state != ActionState.ACTIVE
            or self.action.action_id != ticket.action_id
        ):
            raise ProductionStop("delivery ticket is stale or not exact")
        return ticket.record

    def commit_delivery(
        self,
        ticket: DeliveryTicket,
    ) -> ReplyStatus:
        """ACK/release only after the controller has committed the exact copy."""
        index = self.validate_delivery_ticket(ticket)
        return self._ack_release_and_refill(index)

    def _ack_release_and_refill(
        self,
        index: int,
    ) -> ReplyStatus:
        record = self.records[index]
        if record.key is None:
            raise AssertionError("response record lost its transaction key")
        line = record.key.line
        self.action.ack_bits |= 1 << line
        self.action.ack_count += 1
        self._release_record(index)
        self._refill_queue()
        completed = (
            self.action.next_line == LINES_PER_PAGE
            and self.action.ack_count == LINES_PER_PAGE
            and self.action.issued_bits == (1 << LINES_PER_PAGE) - 1
            and self.action.ack_bits == self.action.issued_bits
            and not self._action_has_records()
        )
        if completed:
            self.action.clear()
        self.assert_invariants()
        return ReplyStatus.COMPLETED if completed else ReplyStatus.ACCEPTED

    def drained(self) -> bool:
        return (
            self.action.state == ActionState.FREE
            and self.queue_count == 0
            and self.pending == -1
            and all(
                record.state == RecordState.FREE for record in self.records
            )
            and all(owner == -1 for owner in self.credit_owner)
        )

    def seal(self) -> None:
        self._ensure_live()
        if not self.drained():
            raise ContractError("transport must drain before teardown")
        self.sealed = True

    def assert_invariants(self) -> None:
        queued = [
            self.queue[(self.queue_head + offset) % REQUEST_QUEUE_CAPACITY]
            for offset in range(self.queue_count)
        ]
        assert len(set(queued)) == len(queued)
        assert all(0 <= index < TRANSACTION_CAPACITY for index in queued)
        buffer_owners = 0
        for index, record in enumerate(self.records):
            in_queue = index in queued
            is_pending = index == self.pending
            credits = self.credit_owner.count(index)
            if record.state == RecordState.FREE:
                assert not in_queue and not is_pending and credits == 0
                assert record.token is not None and record.request is None
                assert record.packet is None and record.line_buffer == b""
                assert record.token.record == index
                assert record.token.epoch == record.epoch
            elif record.state == RecordState.QUEUED:
                assert in_queue and not is_pending and credits == 0
                assert record.token is not None and record.request is None
                assert record.packet is None and record.line_buffer in (b"",)
            elif record.state in (
                RecordState.PENDING_SEND,
                RecordState.WAIT_RETRY,
            ):
                assert is_pending and not in_queue and credits == 1
                assert record.packet is not None and record.request is not None
                assert len(record.line_buffer) == LINE_BYTES
                buffer_owners += 1
            elif record.state in (
                RecordState.IN_FLIGHT,
                RecordState.DELIVERING,
                RecordState.ABORT_DRAIN,
            ):
                assert not in_queue and not is_pending and credits == 1
                assert record.packet is None and record.request is not None
                assert len(record.line_buffer) == LINE_BYTES
                buffer_owners += 1
            assert 0 <= record.epoch <= RECORD_EPOCH_MAX
            if record.credit != -1:
                assert self.credit_owner[record.credit] == index
        assert buffer_owners == sum(owner != -1 for owner in self.credit_owner)
        assert buffer_owners <= RESPONSE_CREDITS
        assert self.queue_count <= REQUEST_QUEUE_CAPACITY
        if self.action.state == ActionState.FREE:
            assert not any(
                record.state != RecordState.FREE for record in self.records
            )
        else:
            assert (
                0
                <= self.action.ack_count
                <= self.action.next_line
                <= LINES_PER_PAGE
            )
            assert self.action.ack_bits & ~self.action.issued_bits == 0


class LogicalCacheModel:
    """Two descriptors and two charged 32-KiB private payload slots."""

    def __init__(self) -> None:
        self.descriptors = [Descriptor() for _ in range(DESCRIPTORS)]
        self.slots = [Slot() for _ in range(SLOTS)]
        self.transport = DedicatedTransport()
        self.active_slot = -1
        self.active_operation: Operation | None = None
        self.delivery_commit_active = False
        self.last_abort = AbortCode.NONE
        self.torn_down = False

    def _ensure_live(self) -> None:
        if self.torn_down:
            raise ContractError("model is torn down")

    def allocate(self, descriptor: int, backing_base: int) -> int:
        self._ensure_live()
        if not 0 <= descriptor < DESCRIPTORS:
            raise ContractError("descriptor is out of range")
        end = checked_span_end(backing_base, BACKING_SPAN)
        if backing_base % BACKING_SPAN:
            raise ContractError("backing base is not 128-KiB aligned")
        item = self.descriptors[descriptor]
        if item.allocated:
            raise ContractError("descriptor is already allocated")
        if item.generation >= GENERATION_MAX:
            raise ExhaustedError("descriptor generation exhausted")
        for other in self.descriptors:
            if not other.allocated:
                continue
            other_end = checked_span_end(
                other.backing_base, other.backing_span
            )
            if not (end < other.backing_base or backing_base > other_end):
                raise ContractError("live descriptor backing ranges overlap")
        item.generation += 1
        item.allocated = True
        item.backing_base = backing_base
        item.backing_span = BACKING_SPAN
        item.backing_ready = 0
        item.writeback_acked = 0
        self.assert_invariants()
        return item.generation

    def publish_backing(self, descriptor: int, page: int) -> None:
        self._ensure_live()
        item = self._descriptor_page(descriptor, page)
        owners = self._page_slot_owners(descriptor, item.generation, page)
        if item.writeback_acked & (1 << page):
            raise ContractError(
                "acknowledged destination page cannot be republished"
            )
        if any(slot.role == SlotRole.DESTINATION for slot in owners):
            raise ContractError("destination-owned page cannot be republished")
        item.backing_ready |= 1 << page
        self.assert_invariants()

    def _descriptor_page(self, descriptor: int, page: int) -> Descriptor:
        if not 0 <= descriptor < DESCRIPTORS:
            raise ContractError("descriptor is out of range")
        if not 0 <= page < PAGES_PER_DESCRIPTOR:
            raise ContractError("page is out of range")
        item = self.descriptors[descriptor]
        if not item.allocated:
            raise ContractError("descriptor is free")
        return item

    @staticmethod
    def _page_base(item: Descriptor, page: int) -> int:
        base = item.backing_base + page * PAGE_BYTES
        checked_span_end(base, PAGE_BYTES)
        return base

    def _slot(self, slot: int) -> Slot:
        if not 0 <= slot < SLOTS:
            raise ContractError("slot is out of range")
        return self.slots[slot]

    def _page_slot_owners(
        self,
        descriptor: int,
        generation: int,
        page: int,
    ) -> list[Slot]:
        return [
            slot
            for slot in self.slots
            if slot.phase != SlotPhase.EMPTY
            and slot.descriptor == descriptor
            and slot.generation == generation
            and slot.page == page
        ]

    def start_fill(
        self,
        descriptor: int,
        page: int,
        slot: int,
        claimed_base: int | None = None,
        claimed_generation: int | None = None,
    ) -> int:
        self._ensure_live()
        item = self._descriptor_page(descriptor, page)
        if not item.backing_ready & (1 << page):
            raise ContractError("fill has no backing-ready page")
        base = self._page_base(item, page)
        if claimed_base is not None and claimed_base != base:
            raise ContractError("claimed fill base differs from descriptor")
        if (
            claimed_generation is not None
            and claimed_generation != item.generation
        ):
            raise ContractError("claimed fill generation is stale")
        target = self._slot(slot)
        if target.phase != SlotPhase.EMPTY:
            raise ContractError("fill requires an empty slot")
        if self._page_slot_owners(descriptor, item.generation, page):
            raise ContractError("source page already has a live slot owner")
        action_id = self.transport.start_action(
            Operation.FILL,
            descriptor,
            item.generation,
            page,
            slot,
            base,
            target.payload,
        )
        target.phase = SlotPhase.FILLING
        target.role = SlotRole.SOURCE
        target.descriptor = descriptor
        target.generation = item.generation
        target.page = page
        target.action_id = action_id
        self.active_slot = slot
        self.active_operation = Operation.FILL
        self.assert_invariants()
        return action_id

    def try_send(self, accepted: bool) -> int | None:
        self._ensure_live()
        slot_payload = (
            self.slots[self.active_slot].payload
            if self.active_slot >= 0
            else self.slots[0].payload
        )
        return self.transport.try_send(accepted, slot_payload)

    def recv_req_retry(self, callback_port: int) -> None:
        self._ensure_live()
        self.transport.recv_req_retry(callback_port)

    def make_response(
        self, index: int, data: bytes | None = None
    ) -> PacketIncarnation:
        self._ensure_live()
        return self.transport.make_response(index, data)

    def begin_receive(
        self,
        packet: PacketIncarnation,
        callback_port: int,
    ) -> ReceiveResult:
        """Run the exact response callback without implicitly copying a fill."""
        self._ensure_live()
        result = self.transport.receive(packet, callback_port)
        self._apply_terminal_status(result.status)
        self.assert_invariants()
        return result

    def _delivery_target(self, index: int) -> tuple[Slot, int]:
        record = self.transport.records[index]
        key = record.key
        if (
            record.state != RecordState.DELIVERING
            or key is None
            or key.operation != Operation.FILL
            or key.slot != self.active_slot
        ):
            raise ProductionStop(
                "delivery record has no exact active fill target"
            )
        slot = self.slots[key.slot]
        if (
            slot.phase != SlotPhase.FILLING
            or slot.role != SlotRole.SOURCE
            or slot.descriptor != key.descriptor
            or slot.generation != key.generation
            or slot.page != key.page
            or slot.action_id != record.action_id
            or len(record.line_buffer) != LINE_BYTES
        ):
            raise ProductionStop("delivery target ownership is not exact")
        start = key.line * LINE_BYTES
        if not 0 <= start <= PAGE_BYTES - LINE_BYTES:
            raise ProductionStop("delivery line offset is invalid")
        return slot, start

    def _copy_delivery_line(self, index: int) -> None:
        """Fixed controller copy from the one charged buffer to exact slot bytes."""
        slot, start = self._delivery_target(index)
        line_buffer = self.transport.records[index].line_buffer
        slot.payload[start : start + LINE_BYTES] = line_buffer

    def commit_delivery(self, ticket: DeliveryTicket) -> ReplyStatus:
        """Copy an exact staged line before transport ACK/release/completion."""
        self._ensure_live()
        index = self.transport.validate_delivery_ticket(ticket)
        self._delivery_target(index)
        if self.delivery_commit_active:
            raise ProductionStop("delivery commit reentry is forbidden")
        self.delivery_commit_active = True
        try:
            self._copy_delivery_line(index)
            status = self.transport.commit_delivery(ticket)
        finally:
            self.delivery_commit_active = False
        self._apply_terminal_status(status)
        self.assert_invariants()
        return status

    def receive(
        self,
        packet: PacketIncarnation,
        callback_port: int,
    ) -> ReplyStatus:
        """Convenience path that immediately performs the explicit copy commit."""
        result = self.begin_receive(packet, callback_port)
        if result.ticket is not None:
            return self.commit_delivery(result.ticket)
        return result.status

    def _apply_terminal_status(self, status: ReplyStatus) -> None:
        if status == ReplyStatus.COMPLETED:
            slot = self.slots[self.active_slot]
            if self.active_operation == Operation.FILL:
                if slot.phase != SlotPhase.FILLING:
                    raise AssertionError("fill completion has no slot owner")
                slot.phase = SlotPhase.CLEAN
                slot.action_id = 0
            elif self.active_operation == Operation.WRITEBACK:
                if (
                    slot.phase != SlotPhase.WRITEBACK
                    or slot.role != SlotRole.DESTINATION
                ):
                    raise AssertionError(
                        "writeback completion has no destination owner"
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
        elif status == ReplyStatus.ABORT_DRAINED and self.transport.drained():
            self._finish_aborted_slot()

    def pin(self, slot: int) -> None:
        self._ensure_live()
        target = self._slot(slot)
        if target.phase not in (SlotPhase.CLEAN, SlotPhase.DIRTY):
            raise ContractError("only a resident slot can be pinned")
        if target.pins >= PIN_MAX:
            raise ExhaustedError("pin counter exhausted")
        target.pins += 1

    def mark_dirty(self, slot: int) -> None:
        self._ensure_live()
        target = self._slot(slot)
        if target.phase not in (SlotPhase.CLEAN, SlotPhase.DIRTY):
            raise ContractError("dirtying requires a resident slot")
        if target.pins == 0:
            raise ContractError("dirtying requires a pin")
        target.phase = SlotPhase.DIRTY

    def bind_dirty_destination(
        self,
        slot: int,
        descriptor: int,
        page: int,
        claimed_generation: int | None = None,
    ) -> None:
        """Atomically change a pinned source-dirty slot to destination-dirty."""
        self._ensure_live()
        destination = self._descriptor_page(descriptor, page)
        target = self._slot(slot)
        if target.phase != SlotPhase.DIRTY or target.pins == 0:
            raise ContractError(
                "destination binding requires pinned dirty payload"
            )
        if target.role != SlotRole.SOURCE:
            raise ContractError("only source-dirty payload may be rebound")
        if (
            claimed_generation is not None
            and claimed_generation != destination.generation
        ):
            raise ContractError("claimed destination generation is stale")
        if target.descriptor == descriptor:
            raise ContractError(
                "source and destination descriptors must differ"
            )
        if destination.backing_ready & (1 << page):
            raise ContractError("destination page is already backing-ready")
        if destination.writeback_acked & (1 << page):
            raise ContractError("destination page is already acknowledged")
        if self._page_slot_owners(descriptor, destination.generation, page):
            raise ContractError(
                "destination page already has a source or destination owner"
            )
        target.descriptor = descriptor
        target.generation = destination.generation
        target.page = page
        target.role = SlotRole.DESTINATION
        self.assert_invariants()

    def unpin(self, slot: int) -> None:
        self._ensure_live()
        target = self._slot(slot)
        if target.pins == 0:
            raise ContractError("slot has no pin")
        target.pins -= 1

    def evict_clean(self, slot: int) -> None:
        self._ensure_live()
        target = self._slot(slot)
        if target.phase != SlotPhase.CLEAN or target.pins:
            raise ContractError("only an unpinned clean slot may be discarded")
        target.clear()

    def start_writeback(
        self,
        slot: int,
        claimed_base: int | None = None,
        claimed_generation: int | None = None,
    ) -> int:
        self._ensure_live()
        target = self._slot(slot)
        if target.phase != SlotPhase.DIRTY or target.pins:
            raise ContractError("writeback requires an unpinned dirty slot")
        if target.role != SlotRole.DESTINATION:
            raise ContractError("writeback requires destination-dirty role")
        destination = self._descriptor_page(target.descriptor, target.page)
        if target.generation != destination.generation:
            raise ContractError("destination slot generation is stale")
        base = self._page_base(destination, target.page)
        if claimed_base is not None and claimed_base != base:
            raise ContractError(
                "claimed writeback base differs from descriptor"
            )
        if (
            claimed_generation is not None
            and claimed_generation != target.generation
        ):
            raise ContractError("claimed writeback generation is stale")
        action_id = self.transport.start_action(
            Operation.WRITEBACK,
            target.descriptor,
            target.generation,
            target.page,
            slot,
            base,
            target.payload,
        )
        target.phase = SlotPhase.WRITEBACK
        target.action_id = action_id
        self.active_slot = slot
        self.active_operation = Operation.WRITEBACK
        self.assert_invariants()
        return action_id

    def abort(self, code: AbortCode) -> bool:
        self._ensure_live()
        if not isinstance(code, AbortCode) or code == AbortCode.NONE:
            raise ContractError("abort code is invalid")
        self.last_abort = code
        drained = self.transport.abort_action(code)
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
        if not 0 <= descriptor < DESCRIPTORS:
            raise ContractError("descriptor is out of range")
        item = self.descriptors[descriptor]
        return (
            item.allocated
            and item.writeback_acked == (1 << PAGES_PER_DESCRIPTOR) - 1
        )

    def free_descriptor(self, descriptor: int) -> None:
        self._ensure_live()
        if not 0 <= descriptor < DESCRIPTORS:
            raise ContractError("descriptor is out of range")
        item = self.descriptors[descriptor]
        if not item.allocated:
            raise ContractError("descriptor is already free")
        if any(slot.descriptor == descriptor for slot in self.slots):
            raise ContractError("descriptor still owns a slot")
        item.allocated = False
        item.backing_base = 0
        item.backing_span = 0
        item.backing_ready = 0
        item.writeback_acked = 0

    def reset(self) -> None:
        self._ensure_live()
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
            descriptor.backing_base = 0
            descriptor.backing_span = 0
            descriptor.backing_ready = 0
            descriptor.writeback_acked = 0
        self.active_slot = -1
        self.active_operation = None
        self.last_abort = AbortCode.NONE

    def teardown(self) -> None:
        self._ensure_live()
        if any(item.allocated for item in self.descriptors):
            raise ContractError("teardown requires free descriptors")
        if any(slot.phase != SlotPhase.EMPTY for slot in self.slots):
            raise ContractError("teardown requires empty slots")
        self.transport.seal()
        self.torn_down = True

    def assert_invariants(self) -> None:
        self.transport.assert_invariants()
        if self.delivery_commit_active:
            assert (
                sum(
                    record.state == RecordState.DELIVERING
                    for record in self.transport.records
                )
                == 1
            )
        action_slots = [slot for slot in self.slots if slot.action_id]
        assert len(action_slots) <= 1
        if self.transport.action.state == ActionState.FREE:
            assert not action_slots and self.active_slot == -1
        else:
            assert len(action_slots) == 1
            assert action_slots[0].action_id == self.transport.action.action_id
            assert self.active_slot in range(SLOTS)
        live_ranges: list[tuple[int, int]] = []
        for descriptor in self.descriptors:
            if descriptor.allocated:
                assert descriptor.backing_span == BACKING_SPAN
                live_ranges.append(
                    (
                        descriptor.backing_base,
                        checked_span_end(
                            descriptor.backing_base, descriptor.backing_span
                        ),
                    )
                )
        if len(live_ranges) == 2:
            assert (
                live_ranges[0][1] < live_ranges[1][0]
                or live_ranges[1][1] < live_ranges[0][0]
            )
        for slot in self.slots:
            assert len(slot.payload) == PAGE_BYTES
            if slot.phase == SlotPhase.EMPTY:
                assert (
                    slot.role == SlotRole.NONE
                    and slot.descriptor == -1
                    and slot.pins == 0
                )
            else:
                assert slot.role != SlotRole.NONE
                assert 0 <= slot.descriptor < DESCRIPTORS
                assert 0 <= slot.page < PAGES_PER_DESCRIPTOR
                assert slot.generation > 0
            if slot.pins:
                assert slot.phase in (SlotPhase.CLEAN, SlotPhase.DIRTY)
            if slot.role == SlotRole.DESTINATION:
                assert slot.phase in (SlotPhase.DIRTY, SlotPhase.WRITEBACK)
        for descriptor_index, descriptor in enumerate(self.descriptors):
            if not descriptor.allocated:
                continue
            for page in range(PAGES_PER_DESCRIPTOR):
                owners = self._page_slot_owners(
                    descriptor_index,
                    descriptor.generation,
                    page,
                )
                assert len(owners) <= 1
                ready = bool(descriptor.backing_ready & (1 << page))
                acknowledged = bool(descriptor.writeback_acked & (1 << page))
                assert not (ready and acknowledged)
                if owners:
                    if owners[0].role == SlotRole.SOURCE:
                        assert ready and not acknowledged
                    else:
                        assert not ready and not acknowledged
                if acknowledged:
                    assert not owners

    def digest(self) -> str:
        """Host-only deterministic observation; never feeds a transition."""

        def key_state(key: TransactionKey | None) -> object:
            return (
                None
                if key is None
                else (
                    key.descriptor,
                    key.generation,
                    key.slot,
                    key.page,
                    key.line,
                    key.operation.value,
                )
            )

        state = {
            "descriptors": [item.__dict__ for item in self.descriptors],
            "slots": [
                {
                    "phase": slot.phase.value,
                    "role": slot.role.value,
                    "descriptor": slot.descriptor,
                    "generation": slot.generation,
                    "page": slot.page,
                    "pins": slot.pins,
                    "action_id": slot.action_id,
                    "payload": hashlib.sha256(slot.payload).hexdigest(),
                }
                for slot in self.slots
            ],
            "action": {
                **self.transport.action.__dict__,
                "state": self.transport.action.state.value,
                "operation": self.transport.action.operation.value
                if self.transport.action.operation
                else None,
                "abort_code": self.transport.action.abort_code.value,
            },
            "queue": self.transport.queue,
            "queue_control": (
                self.transport.queue_head,
                self.transport.queue_tail,
                self.transport.queue_count,
            ),
            "pending": self.transport.pending,
            "credits": self.transport.credit_owner,
            "records": [
                {
                    "state": record.state.value,
                    "epoch": record.epoch,
                    "action": record.action_id,
                    "key": key_state(record.key),
                    "token": record.token.__dict__ if record.token else None,
                    "request": record.request.incarnation
                    if record.request
                    else None,
                    "packet": record.packet.incarnation
                    if record.packet
                    else None,
                    "address": record.address,
                    "commands": (
                        record.request_command,
                        record.response_command,
                    ),
                    "size": record.size,
                    "port": record.port,
                    "credit": record.credit,
                    "buffer": hashlib.sha256(record.line_buffer).hexdigest()
                    if record.line_buffer
                    else None,
                }
                for record in self.transport.records
            ],
            "identities": (
                self.transport.next_action_id,
                self.transport.action_ids_exhausted,
                self.transport.next_incarnation_id,
                self.transport.incarnation_ids_exhausted,
                self.transport.next_peer_packet_id,
                self.transport.peer_packet_ids_exhausted,
            ),
            "active": (
                self.active_slot,
                self.active_operation.value if self.active_operation else None,
                self.delivery_commit_active,
            ),
            "last_abort": self.last_abort.value,
            "sealed": self.transport.sealed,
            "torn_down": self.torn_down,
        }
        return hashlib.sha256(
            json.dumps(state, sort_keys=True, default=str).encode()
        ).hexdigest()


# Exact logical packed-state accounting.  State-dependent fields have no
# sentinel bit where the record state already determines validity.  Python
# containers, big integers, digests, tickets, SenderState/Request/Packet host
# objects, allocator overhead, and transient callback wires are not persistent
# hardware state.
DESCRIPTOR_BITS_PER_ENTRY = 1 + 32 + 64 + 18 + 4 + 4
SLOT_BITS_PER_ENTRY = 3 + 2 + 2 + 32 + 3 + 8 + 32
PAGE_ACTION_BITS = 2 + 32 + 2 + 2 + 32 + 3 + 2 + 64 + 10 + 512 + 512 + 10 + 1
TRANSACTION_BITS_PER_RECORD = 3 + 16 + 32 + 46 + 64 + 2 + 2 + 7 + 2 + 3
REQUEST_FIFO_CONTROL_BITS = 8 * 3 + 3 + 3 + 4 + 4
GLOBAL_CONTROL_BITS = 32 + 1 + 1 + 2 + 2 + 1 + 1 + 1
PACKED_LOGICAL_STATE_BITS = {
    "private_slot_payloads": 2 * PAGE_BYTES * 8,
    "descriptor_correlators": DESCRIPTORS * DESCRIPTOR_BITS_PER_ENTRY,
    "slot_correlators": SLOTS * SLOT_BITS_PER_ENTRY,
    "page_action_and_two_512b_sets": PAGE_ACTION_BITS,
    "eight_transaction_correlators": (
        TRANSACTION_CAPACITY * TRANSACTION_BITS_PER_RECORD
    ),
    "request_fifo_control": REQUEST_FIFO_CONTROL_BITS,
    "four_credit_owners": 16,
    "four_line_buffers": RESPONSE_CREDITS * LINE_BYTES * 8,
    "bounded_global_control_including_delivery_guard": GLOBAL_CONTROL_BITS,
}
PACKED_LOGICAL_STATE_BYTES = (sum(PACKED_LOGICAL_STATE_BITS.values()) + 7) // 8

# A proposed naturally aligned fixed-width C++ hardware-state projection,
# excluding all gem5-only polymorphic/pointer objects.  Actual production
# sizeof values remain a static_assert/integration gate.
ALIGNED_CPP_PROJECTION_BYTES = {
    "private_slot_payloads": 2 * PAGE_BYTES,
    "descriptor_correlators": 48,
    "slot_correlators": 32,
    "page_action_and_sets": 160,
    "transaction_correlators": 256,
    "request_fifo_control": 16,
    "credit_owners": 8,
    "line_buffers": RESPONSE_CREDITS * LINE_BYTES,
    "bounded_global_control": 12,
}


def line_pattern(page: int, line: int) -> bytes:
    """Deterministic demo/test input, not persistent model state or authority."""
    seed = (page * LINES_PER_PAGE + line) & 0xFFFF
    return bytes(((seed + offset) & 0xFF) for offset in range(LINE_BYTES))


def drive_action(model: LogicalCacheModel) -> None:
    """Accept and respond deterministically until the current page completes."""
    while not model.transport.drained():
        while -1 in model.transport.credit_owner:
            index = model.try_send(True)
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
            record = model.transport.records[index]
            data = (
                line_pattern(record.key.page, record.key.line)
                if record.key and record.key.operation == Operation.FILL
                else b""
            )
            model.receive(model.make_response(index, data), record.port)


def deterministic_demo() -> dict[str, object]:
    model = LogicalCacheModel()
    source_generation = model.allocate(0, 0)
    destination_generation = model.allocate(1, 2 * BACKING_SPAN)
    for page in range(PAGES_PER_DESCRIPTOR):
        model.publish_backing(0, page)
        slot = page & 1
        model.start_fill(0, page, slot)
        drive_action(model)
        model.pin(slot)
        model.mark_dirty(slot)
        model.bind_dirty_destination(slot, 1, page)
        model.unpin(slot)
        model.start_writeback(slot)
        drive_action(model)
    return {
        "source_generation": source_generation,
        "destination_generation": destination_generation,
        "destination_complete": model.descriptor_complete(1),
        "digest": model.digest(),
        "packed_state_bytes_per_maa": PACKED_LOGICAL_STATE_BYTES,
        "aligned_cpp_projection_bytes_per_maa": sum(
            ALIGNED_CPP_PROJECTION_BYTES.values()
        ),
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
