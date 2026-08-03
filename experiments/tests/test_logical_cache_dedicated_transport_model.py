import importlib.util
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / (
    "experiments/analysis/logical_cache_dedicated_transport_model.py"
)
DESIGN_PATH = ROOT / (
    "experiments/analysis/"
    "logical_cache_dedicated_transport_design_2026-08-02.md"
)
SPEC = importlib.util.spec_from_file_location(
    "logical_cache_dedicated_transport_model", MODULE_PATH
)
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)

SRC_BASE = 0
DST_BASE = 2 * MODEL.BACKING_SPAN


def allocate_source(model):
    generation = model.allocate(0, SRC_BASE)
    for page in range(MODEL.PAGES_PER_DESCRIPTOR):
        model.publish_backing(0, page)
    return generation


def allocate_pair(model):
    source = allocate_source(model)
    destination = model.allocate(1, DST_BASE)
    return source, destination


def start_fill(model, page=0, slot=0):
    return model.start_fill(0, page, slot)


def send_one(model, data=None):
    index = model.try_send(True)
    if index is None:
        return None, None
    return index, model.make_response(index, data)


def receive_on(model, packet, callback_port=None):
    if callback_port is None:
        callback_port = MODEL.core_port(packet.address)
    returned = MODEL.ReturnedPacketOwner(packet)
    status = model.receive(returned, callback_port)
    if not returned.destroyed or returned.packet is not None:
        raise AssertionError("valid callback retained its returned Packet")
    return status


def drain_exact(model, fill_data=True):
    responses = 0
    while not model.transport.drained():
        while -1 in model.transport.credit_owner:
            index = model.try_send(True)
            if index is None:
                break
        inflight = [
            index
            for index, record in enumerate(model.transport.records)
            if record.state == MODEL.RecordState.IN_FLIGHT
        ]
        if not inflight:
            raise AssertionError("no forward progress")
        for index in reversed(inflight):
            key = model.transport.records[index].key
            data = (
                MODEL.line_pattern(key.page, key.line)
                if fill_data and key.operation == MODEL.Operation.FILL
                else b""
            )
            receive_on(model, model.make_response(index, data))
            responses += 1
    return responses


class DedicatedTransportContractTest(unittest.TestCase):
    def test_geometry_ports_and_recomputed_storage_are_explicit(self):
        self.assertEqual(MODEL.DESCRIPTORS, 2)
        self.assertEqual(MODEL.PAGES_PER_DESCRIPTOR, 4)
        self.assertEqual(MODEL.SLOTS, 2)
        self.assertEqual(MODEL.NUM_PORTS, 4)
        self.assertEqual(MODEL.LINES_PER_PAGE, 512)
        self.assertEqual(len(MODEL.DedicatedTransport().records), 8)
        self.assertEqual(MODEL.DESCRIPTOR_BITS_PER_ENTRY, 123)
        self.assertEqual(MODEL.SLOT_BITS_PER_ENTRY, 82)
        self.assertEqual(MODEL.PAGE_ACTION_BITS, 1_184)
        self.assertEqual(MODEL.TRANSACTION_BITS_PER_RECORD, 177)
        self.assertEqual(MODEL.REQUEST_FIFO_CONTROL_BITS, 38)
        self.assertEqual(MODEL.GLOBAL_CONTROL_BITS, 41)
        self.assertEqual(
            sum(MODEL.PACKED_LOGICAL_STATE_BITS.values()), 529_441
        )
        self.assertEqual(MODEL.PACKED_LOGICAL_STATE_BYTES, 66_181)
        self.assertEqual(
            sum(MODEL.ALIGNED_CPP_PROJECTION_BYTES.values()), 66_324
        )
        self.assertEqual(4 * MODEL.PACKED_LOGICAL_STATE_BYTES, 264_724)

    def test_first_bridge_rejects_non_four_core_or_non_64_byte_geometry(self):
        for factory in (MODEL.LogicalCacheModel, MODEL.DedicatedTransport):
            for num_cores, line_bytes, pattern in (
                (1, 64, "four"),
                (8, 64, "four"),
                (4, 32, "64-byte"),
                (4, 128, "64-byte"),
            ):
                with self.subTest(
                    factory=factory.__name__,
                    num_cores=num_cores,
                    line_bytes=line_bytes,
                ):
                    with self.assertRaisesRegex(MODEL.ContractError, pattern):
                        factory(num_cores, line_bytes)

        source = MODULE_PATH.read_text(encoding="utf-8")
        design = DESIGN_PATH.read_text(encoding="utf-8")
        self.assertIn("if num_cores != NUM_PORTS:", source)
        self.assertIn("if maa_line_bytes != LINE_BYTES:", source)
        self.assertIn("num_cores == 4", design)
        self.assertIn("MAA transaction line and cache line", design)
        self.assertIn("Future parameterization must recompute", design)

    def test_four_credits_block_fifth_accepted_or_refused_materialization(
        self,
    ):
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                model = MODEL.LogicalCacheModel()
                allocate_source(model)
                start_fill(model)
                self.assertTrue(
                    all(
                        record.line_buffer == b""
                        for record in model.transport.records
                    )
                )
                sent = [model.try_send(True) for _ in range(4)]
                self.assertNotIn(None, sent)
                before = model.digest()
                with self.assertRaisesRegex(MODEL.ContractError, "credits"):
                    model.try_send(accepted)
                self.assertEqual(model.digest(), before)
                self.assertEqual(
                    sum(
                        len(record.line_buffer) == MODEL.LINE_BYTES
                        for record in model.transport.records
                    ),
                    4,
                )

    def test_refusal_reserves_final_credit_and_exact_packet_until_exact_port_retry(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        for _ in range(3):
            model.try_send(True)
        self.assertIsNone(model.try_send(False))
        pending = model.transport.pending
        record = model.transport.records[pending]
        packet = record.packet
        token = record.token
        request = record.request
        expected_port = MODEL.core_port(record.address)
        self.assertEqual(model.transport.credit_owner.count(-1), 0)
        with self.assertRaisesRegex(MODEL.ContractError, "wrong port"):
            model.recv_req_retry((expected_port + 1) % MODEL.NUM_PORTS)
        self.assertIs(record.packet, packet)
        model.recv_req_retry(expected_port)
        self.assertEqual(model.try_send(True), pending)
        self.assertIs(record.token, token)
        self.assertIs(record.request, request)
        self.assertIsNone(record.packet)

    def test_line_address_mapping_spans_all_bounded_ports(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        expected = []
        for _ in range(MODEL.NUM_PORTS):
            index = model.try_send(True)
            record = model.transport.records[index]
            expected.append(record.port)
            self.assertEqual(record.port, MODEL.core_port(record.address))
        self.assertEqual(expected, list(range(MODEL.NUM_PORTS)))

    def test_legal_response_packet_replacement_preserves_request_and_token(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        self.assertIsNone(model.try_send(False))
        index = model.transport.pending
        request_packet = model.transport.records[index].packet
        request = request_packet.request
        token = request_packet.sender_stack[0]
        model.recv_req_retry(model.transport.records[index].port)
        self.assertEqual(model.try_send(True), index)
        response = model.make_response(index, bytes([0xA5]) * MODEL.LINE_BYTES)
        self.assertIsNot(response, request_packet)
        self.assertNotEqual(response.incarnation, request_packet.incarnation)
        self.assertIs(response.request, request)
        self.assertIs(response.sender_stack[0], token)
        self.assertEqual(
            receive_on(model, response), MODEL.ReplyStatus.ACCEPTED
        )
        self.assertEqual(
            model.slots[0].payload[: MODEL.LINE_BYTES],
            bytes([0xA5]) * MODEL.LINE_BYTES,
        )

    def test_both_legal_read_responses_carry_exact_data(self):
        for command in ("ReadResp", "ReadRespWithInvalidate"):
            with self.subTest(command=command):
                model = MODEL.LogicalCacheModel()
                allocate_source(model)
                start_fill(model)
                _, response = send_one(model, bytes([0x5A]) * MODEL.LINE_BYTES)
                response = replace(response, command=command)
                self.assertEqual(
                    receive_on(model, response), MODEL.ReplyStatus.ACCEPTED
                )
                self.assertEqual(
                    model.slots[0].payload[: MODEL.LINE_BYTES],
                    bytes([0x5A]) * MODEL.LINE_BYTES,
                )

    def test_exact_slot_copy_precedes_every_release_and_final_action_clear(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        releases = []
        original_release = model.transport._release_record

        def checked_release(index):
            record = model.transport.records[index]
            key = record.key
            start = key.line * MODEL.LINE_BYTES
            self.assertEqual(
                bytes(
                    model.slots[key.slot].payload[
                        start : start + MODEL.LINE_BYTES
                    ]
                ),
                record.line_buffer,
            )
            self.assertEqual(
                model.transport.action.state, MODEL.ActionState.ACTIVE
            )
            releases.append(key.line)
            original_release(index)

        model.transport._release_record = checked_release
        self.assertEqual(drain_exact(model), MODEL.LINES_PER_PAGE)
        self.assertEqual(len(releases), MODEL.LINES_PER_PAGE)
        self.assertEqual(model.transport.action.state, MODEL.ActionState.FREE)
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.CLEAN)

    def test_four_credits_and_buffers_remain_owned_through_delayed_delivery(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        tickets = []
        for value in range(MODEL.RESPONSE_CREDITS):
            index, response = send_one(
                model, bytes([value + 1]) * MODEL.LINE_BYTES
            )
            returned = MODEL.ReturnedPacketOwner(response)
            result = model.begin_receive(
                returned, model.transport.records[index].port
            )
            self.assertTrue(returned.destroyed)
            self.assertEqual(result.status, MODEL.ReplyStatus.DELIVERY_PENDING)
            tickets.append(result.ticket)
        self.assertEqual(model.transport.credit_owner.count(-1), 0)
        self.assertEqual(
            sum(
                record.state == MODEL.RecordState.DELIVERING
                for record in model.transport.records
            ),
            MODEL.RESPONSE_CREDITS,
        )
        self.assertEqual(
            sum(
                len(record.line_buffer) == MODEL.LINE_BYTES
                for record in model.transport.records
            ),
            MODEL.RESPONSE_CREDITS,
        )
        with self.assertRaisesRegex(MODEL.ContractError, "credits"):
            model.try_send(True)
        for ticket in tickets:
            model.commit_delivery(ticket)

    def test_delayed_failing_and_reentrant_delivery_never_false_ack_or_drain(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes([0xC3]) * MODEL.LINE_BYTES)
        returned = MODEL.ReturnedPacketOwner(response)
        result = model.begin_receive(
            returned, model.transport.records[index].port
        )
        self.assertTrue(returned.destroyed)
        ticket = result.ticket
        self.assertEqual(result.status, MODEL.ReplyStatus.DELIVERY_PENDING)
        self.assertEqual(model.transport.action.ack_count, 0)
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))
        self.assertEqual(model.transport.credit_owner.count(-1), 3)

        bad_ticket = MODEL.DeliveryTicket(
            ticket.record, ticket.epoch, ticket.action_id + 1
        )
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ProductionStop, "ticket"):
            model.commit_delivery(bad_ticket)
        self.assertEqual(model.digest(), before)

        original_copy = model._copy_delivery_line

        def reenter(_index):
            model.commit_delivery(ticket)

        model._copy_delivery_line = reenter
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ProductionStop, "delivery copy"):
            model.commit_delivery(ticket)
        self.assertEqual(model.digest(), before)
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))
        self.assertEqual(model.transport.action.ack_count, 0)
        self.assertFalse(model.transport.drained())
        model._copy_delivery_line = original_copy
        self.assertEqual(
            model.commit_delivery(ticket), MODEL.ReplyStatus.ACCEPTED
        )

    def _assert_abort_reentry_is_guarded(self, direct_transport):
        payload = bytes([0xE7]) * MODEL.LINE_BYTES
        model = MODEL.LogicalCacheModel()
        control = MODEL.LogicalCacheModel()
        tickets = []
        for target in (model, control):
            allocate_source(target)
            start_fill(target)
            index, response = send_one(target, payload)
            returned = MODEL.ReturnedPacketOwner(response)
            result = target.begin_receive(
                returned, target.transport.records[index].port
            )
            self.assertTrue(returned.destroyed)
            tickets.append(result.ticket)

        original_copy = model._copy_delivery_line

        def reject_abort_then_copy(index):
            guarded = model.digest()
            mutate = (
                (lambda: model.transport.abort_action(MODEL.AbortCode.CALLER))
                if direct_transport
                else (lambda: model.abort(MODEL.AbortCode.CALLER))
            )
            with self.assertRaisesRegex(MODEL.ProductionStop, "delivery copy"):
                mutate()
            self.assertEqual(model.digest(), guarded)
            self.assertFalse(model.transport.drained())
            record = model.transport.records[index]
            self.assertEqual(record.state, MODEL.RecordState.DELIVERING)
            self.assertEqual(
                model.transport.credit_owner[record.credit], index
            )
            original_copy(index)

        model._copy_delivery_line = reject_abort_then_copy
        self.assertEqual(
            model.commit_delivery(tickets[0]), MODEL.ReplyStatus.ACCEPTED
        )
        self.assertEqual(
            control.commit_delivery(tickets[1]), MODEL.ReplyStatus.ACCEPTED
        )
        self.assertEqual(model.digest(), control.digest())
        self.assertEqual(model.transport.action.ack_count, 1)
        self.assertEqual(model.transport.credit_owner.count(-1), 4)
        self.assertEqual(model.slots[0].payload[: MODEL.LINE_BYTES], payload)
        model.assert_invariants()

    def test_model_abort_reentry_during_exact_copy_is_guarded(self):
        self._assert_abort_reentry_is_guarded(direct_transport=False)

    def test_direct_transport_abort_reentry_during_exact_copy_is_guarded(self):
        self._assert_abort_reentry_is_guarded(direct_transport=True)

    def test_pre_copy_failure_clears_only_guard_and_preserves_delivery_owner(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        payload = bytes([0xB6]) * MODEL.LINE_BYTES
        index, response = send_one(model, payload)
        returned = MODEL.ReturnedPacketOwner(response)
        ticket = model.begin_receive(
            returned, model.transport.records[index].port
        ).ticket
        before = model.digest()

        def fail_before_copy(_index):
            raise RuntimeError("injected pre-copy failure")

        original_copy = model._copy_delivery_line
        model._copy_delivery_line = fail_before_copy
        with self.assertRaisesRegex(RuntimeError, "pre-copy"):
            model.commit_delivery(ticket)
        self.assertEqual(model.digest(), before)
        self.assertFalse(model.transport.delivery_copy_active)
        self.assertEqual(
            model.transport.records[index].state,
            MODEL.RecordState.DELIVERING,
        )
        self.assertEqual(model.transport.action.ack_count, 0)
        self.assertEqual(model.transport.credit_owner.count(-1), 3)
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))
        self.assertFalse(model.transport.drained())
        model.assert_invariants()
        model._copy_delivery_line = original_copy
        self.assertEqual(
            model.commit_delivery(ticket), MODEL.ReplyStatus.ACCEPTED
        )

    def test_copy_guard_rejects_all_public_model_and_transport_mutators(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes(MODEL.LINE_BYTES))
        returned = MODEL.ReturnedPacketOwner(response)
        ticket = model.begin_receive(
            returned, model.transport.records[index].port
        ).ticket
        dummy = MODEL.PacketIncarnation(
            99,
            MODEL.RequestPtr(99),
            (),
            0,
            "ReadResp",
            MODEL.LINE_BYTES,
            bytes(MODEL.LINE_BYTES),
        )
        page = bytes(MODEL.PAGE_BYTES)
        original_copy = model._copy_delivery_line

        def check_every_entry(record_index):
            model_mutators = (
                lambda: model.allocate(1, DST_BASE),
                lambda: model.publish_backing(0, 0),
                lambda: model.start_fill(0, 0, 1),
                lambda: model.try_send(True),
                lambda: model.recv_req_retry(0),
                lambda: model.make_response(record_index),
                lambda: model.begin_receive(
                    MODEL.ReturnedPacketOwner(dummy), 0
                ),
                lambda: model.commit_delivery(ticket),
                lambda: model.receive(MODEL.ReturnedPacketOwner(dummy), 0),
                lambda: model.pin(0),
                lambda: model.mark_dirty(0),
                lambda: model.bind_dirty_destination(0, 1, 0),
                lambda: model.unpin(0),
                lambda: model.evict_clean(0),
                lambda: model.start_writeback(0),
                lambda: model.abort(MODEL.AbortCode.CALLER),
                lambda: model.free_descriptor(0),
                model.reset,
                model.teardown,
            )
            transport_mutators = (
                lambda: model.transport.start_action(
                    MODEL.Operation.FILL, 0, 1, 0, 0, 0, page
                ),
                lambda: model.transport.try_send(True, page),
                lambda: model.transport.recv_req_retry(0),
                lambda: model.transport.make_response(record_index),
                lambda: model.transport.abort_action(MODEL.AbortCode.CALLER),
                lambda: model.transport.receive(
                    MODEL.ReturnedPacketOwner(dummy), 0
                ),
                model.transport.seal,
            )
            for mutate in model_mutators + transport_mutators:
                guarded = model.digest()
                with self.subTest(mutate=mutate):
                    with self.assertRaisesRegex(
                        MODEL.ProductionStop, "delivery copy"
                    ):
                        mutate()
                    self.assertEqual(model.digest(), guarded)
            self.assertFalse(model.transport.drained())
            original_copy(record_index)

        model._copy_delivery_line = check_every_entry
        self.assertEqual(
            model.commit_delivery(ticket), MODEL.ReplyStatus.ACCEPTED
        )
        self.assertEqual(model.transport.action.ack_count, 1)
        model.assert_invariants()

    def test_abort_drain_never_invokes_delivery_copy_consumer(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes([0xD4]) * MODEL.LINE_BYTES)
        calls = []
        original_copy = model._copy_delivery_line

        def observe_copy(record_index):
            calls.append(record_index)
            original_copy(record_index)

        model._copy_delivery_line = observe_copy
        self.assertFalse(model.abort(MODEL.AbortCode.CALLER))
        self.assertEqual(
            receive_on(model, response, model.transport.records[index].port),
            MODEL.ReplyStatus.ABORT_DRAINED,
        )
        self.assertEqual(calls, [])
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))
        self.assertTrue(model.transport.drained())

    def test_abort_between_begin_receive_and_commit_cancels_without_copy(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes([0xD5]) * MODEL.LINE_BYTES)
        returned = MODEL.ReturnedPacketOwner(response)
        result = model.begin_receive(
            returned, model.transport.records[index].port
        )
        self.assertEqual(result.status, MODEL.ReplyStatus.DELIVERY_PENDING)
        self.assertTrue(returned.destroyed)
        self.assertTrue(model.abort(MODEL.AbortCode.CALLER))
        self.assertTrue(model.transport.drained())
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.EMPTY)
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))
        with self.assertRaisesRegex(MODEL.ProductionStop, "ticket"):
            model.commit_delivery(result.ticket)
        model.assert_invariants()

    def test_packet_shape_has_no_port_or_transaction_key_extension(self):
        names = tuple(MODEL.PacketIncarnation.__dataclass_fields__)
        self.assertNotIn("port", names)
        self.assertNotIn("key", names)
        self.assertNotIn("transaction_key", names)
        self.assertNotIn("data", MODEL.ReceiveResult.__dataclass_fields__)
        self.assertNotIn("packet", MODEL.ReceiveResult.__dataclass_fields__)
        self.assertNotIn("returned", MODEL.ReceiveResult.__dataclass_fields__)
        self.assertEqual(
            tuple(MODEL.DeliveryTicket.__dataclass_fields__),
            ("record", "epoch", "action_id"),
        )

    def test_returned_packet_owner_is_destroyed_before_ticket_or_release(self):
        fill = MODEL.LogicalCacheModel()
        allocate_source(fill)
        start_fill(fill)
        index, response = send_one(fill, bytes([0xA9]) * MODEL.LINE_BYTES)
        returned = MODEL.ReturnedPacketOwner(response)
        result = fill.begin_receive(
            returned, fill.transport.records[index].port
        )
        self.assertTrue(returned.destroyed)
        self.assertIsNone(returned.packet)
        self.assertEqual(result.status, MODEL.ReplyStatus.DELIVERY_PENDING)
        self.assertIsNotNone(result.ticket)
        self.assertEqual(
            fill.transport.records[index].line_buffer,
            bytes([0xA9]) * MODEL.LINE_BYTES,
        )

        write = MODEL.LogicalCacheModel()
        allocate_pair(write)
        start_fill(write)
        drain_exact(write)
        write.pin(0)
        write.mark_dirty(0)
        write.bind_dirty_destination(0, 1, 0)
        write.unpin(0)
        write.start_writeback(0)
        self.assertIsNone(write.try_send(False))
        index = write.transport.pending
        record = write.transport.records[index]
        request_packet = record.packet
        retained_line = record.line_buffer
        self.assertIs(request_packet.data, retained_line)
        write.recv_req_retry(record.port)
        self.assertEqual(write.try_send(True), index)
        # A gem5 makeResponse() mutates and may return the original WriteReq
        # Packet.  This test-only mutation preserves exact Python object
        # identity and its static-data pointer.
        object.__setattr__(request_packet, "command", "WriteResp")
        returned = MODEL.ReturnedPacketOwner(request_packet)
        original_release = write.transport._release_record

        def release_after_destroy(record_index):
            self.assertTrue(returned.destroyed)
            self.assertIsNone(returned.packet)
            self.assertIs(
                write.transport.records[record_index].line_buffer,
                retained_line,
            )
            original_release(record_index)

        write.transport._release_record = release_after_destroy
        result = write.begin_receive(returned, record.port)
        self.assertEqual(result.status, MODEL.ReplyStatus.ACCEPTED)
        self.assertTrue(returned.destroyed)

        aborted = MODEL.LogicalCacheModel()
        allocate_source(aborted)
        start_fill(aborted)
        index, response = send_one(aborted, bytes(MODEL.LINE_BYTES))
        self.assertFalse(aborted.abort(MODEL.AbortCode.CALLER))
        returned = MODEL.ReturnedPacketOwner(response)
        result = aborted.begin_receive(
            returned, aborted.transport.records[index].port
        )
        self.assertEqual(result.status, MODEL.ReplyStatus.ABORT_DRAINED)
        self.assertTrue(returned.destroyed)
        self.assertIsNone(returned.packet)

    def test_malformed_returned_packet_is_not_destroyed_or_recovered(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes(MODEL.LINE_BYTES))
        returned = MODEL.ReturnedPacketOwner(
            replace(response, size=MODEL.LINE_BYTES - 1)
        )
        before = model.digest()
        with self.assertRaises(MODEL.ProductionStop):
            model.begin_receive(returned, model.transport.records[index].port)
        self.assertFalse(returned.destroyed)
        self.assertIsNotNone(returned.packet)
        self.assertEqual(model.digest(), before)

    def test_copied_missing_non_top_residual_and_unknown_tokens_stop_atomically(
        self,
    ):
        variants = (
            lambda packet: replace(
                packet,
                sender_stack=(
                    MODEL.RouteToken(**packet.sender_stack[0].__dict__),
                ),
            ),
            lambda packet: replace(packet, sender_stack=()),
            lambda packet: replace(
                packet, sender_stack=(object(), packet.sender_stack[0])
            ),
            lambda packet: replace(
                packet, sender_stack=(packet.sender_stack[0], object())
            ),
            lambda packet: replace(
                packet, sender_stack=(MODEL.RouteToken(7, 9, 11),)
            ),
        )
        for mutate in variants:
            with self.subTest(mutate=mutate):
                model = MODEL.LogicalCacheModel()
                allocate_source(model)
                start_fill(model)
                _, response = send_one(model, bytes(MODEL.LINE_BYTES))
                bad = mutate(response)
                before = model.digest()
                with self.assertRaises(MODEL.ProductionStop):
                    receive_on(model, bad)
                self.assertEqual(model.digest(), before)
                self.assertEqual(
                    model.slots[0].payload, bytes(MODEL.PAGE_BYTES)
                )

    def test_same_request_wrong_token_stops_same_token_wrong_request_is_retained(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes([1]) * MODEL.LINE_BYTES)
        wrong_token = replace(
            response,
            sender_stack=(MODEL.RouteToken(index, 1, 1),),
        )
        before = model.digest()
        with self.assertRaises(MODEL.ProductionStop):
            receive_on(model, wrong_token)
        self.assertEqual(model.digest(), before)
        wrong_request = replace(response, request=MODEL.RequestPtr(99))
        before = model.digest()
        with self.assertRaises(MODEL.ProductionStop):
            receive_on(model, wrong_request)
        self.assertEqual(model.digest(), before)
        self.assertEqual(
            model.transport.records[index].state, MODEL.RecordState.IN_FLIGHT
        )
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))

    def test_duplicate_and_reused_stale_tokens_stop_without_writes(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes([7]) * MODEL.LINE_BYTES)
        self.assertEqual(
            receive_on(model, response), MODEL.ReplyStatus.ACCEPTED
        )
        guard = bytes(model.slots[0].payload)
        before = model.digest()
        with self.assertRaises(MODEL.ProductionStop):
            receive_on(model, response)
        self.assertEqual(model.digest(), before)
        self.assertEqual(bytes(model.slots[0].payload), guard)
        # Drive until the same fixed record/token address is an in-flight owner
        # for a new epoch and an exact new RequestPtr.
        while (
            model.transport.records[index].state != MODEL.RecordState.IN_FLIGHT
        ):
            current = model.try_send(True)
            if current == index:
                break
            key = model.transport.records[current].key
            receive_on(
                model,
                model.make_response(
                    current, MODEL.line_pattern(key.page, key.line)
                ),
            )
        # The SenderState object is embedded: its address is intentionally
        # stable and the old response now observes the new live epoch fields.
        self.assertIs(
            model.transport.records[index].token, response.sender_stack[0]
        )
        self.assertEqual(
            response.sender_stack[0].epoch,
            model.transport.records[index].epoch,
        )
        self.assertIsNot(
            response.request, model.transport.records[index].request
        )
        before = model.digest()
        with self.assertRaises(MODEL.ProductionStop):
            receive_on(model, response)
        self.assertEqual(model.digest(), before)

    def _assert_malformed_owned_packet_stops(self, change):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes([3]) * MODEL.LINE_BYTES)
        bad = change(response)
        before = model.digest()
        with self.assertRaises(MODEL.ProductionStop):
            receive_on(model, bad)
        self.assertEqual(model.digest(), before)
        self.assertEqual(
            model.transport.records[index].state, MODEL.RecordState.IN_FLIGHT
        )
        self.assertEqual(model.transport.credit_owner.count(-1), 3)
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))
        # Test-harness-only continuation after the modeled production panic:
        # it proves the malformed packet did not ACK or release the obligation.
        self.assertFalse(model.abort(MODEL.AbortCode.CALLER))
        self.assertEqual(
            receive_on(
                model, model.make_response(index, bytes(MODEL.LINE_BYTES))
            ),
            MODEL.ReplyStatus.ABORT_DRAINED,
        )
        self.assertTrue(model.transport.drained())

    def test_wrong_real_packet_fields_and_payload_are_fail_closed(self):
        changes = (
            lambda packet: replace(packet, size=8),
            lambda packet: replace(packet, command="WriteResp"),
            lambda packet: replace(packet, address=packet.address + 64),
            lambda packet: replace(packet, data=bytes(MODEL.LINE_BYTES - 1)),
        )
        for change in changes:
            with self.subTest(change=change):
                self._assert_malformed_owned_packet_stops(change)

    def test_wrong_callback_endpoint_stops_atomically_after_token_authentication(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes(MODEL.LINE_BYTES))
        expected = model.transport.records[index].port
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ProductionStop, "callback port"):
            receive_on(model, response, (expected + 1) % MODEL.NUM_PORTS)
        self.assertEqual(model.digest(), before)
        bad_token = replace(
            response, sender_stack=(MODEL.RouteToken(7, 9, 11),)
        )
        with self.assertRaisesRegex(MODEL.ProductionStop, "token"):
            receive_on(model, bad_token, (expected + 1) % MODEL.NUM_PORTS)
        self.assertEqual(model.digest(), before)

    def test_line_identity_is_derived_from_owned_token_not_packet_extension(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        first = model.try_send(True)
        second = model.try_send(True)
        first_response = model.make_response(first, bytes(MODEL.LINE_BYTES))
        second_response = model.make_response(second, bytes(MODEL.LINE_BYTES))
        self.assertFalse(hasattr(first_response, "key"))
        # Exact token for line 1 combined with line 0's Request/address routes
        # to record 1, then fails real-field validation without mutation.
        cross_line = replace(
            first_response, sender_stack=second_response.sender_stack
        )
        before = model.digest()
        with self.assertRaises(MODEL.ProductionStop):
            receive_on(model, cross_line)
        self.assertEqual(model.digest(), before)

    def test_abort_drain_revalidates_every_field_and_malformed_owner_cannot_drain(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index, response = send_one(model, bytes([9]) * MODEL.LINE_BYTES)
        self.assertFalse(model.abort(MODEL.AbortCode.CALLER))
        bad = replace(response, address=response.address + MODEL.LINE_BYTES)
        before = model.digest()
        with self.assertRaises(MODEL.ProductionStop):
            receive_on(model, bad)
        self.assertEqual(model.digest(), before)
        self.assertEqual(model.transport.credit_owner.count(-1), 3)
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))
        # Test-harness-only continuation after the modeled production panic.
        good = model.make_response(index, bytes([9]) * MODEL.LINE_BYTES)
        self.assertEqual(
            receive_on(model, good), MODEL.ReplyStatus.ABORT_DRAINED
        )
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))

    def test_delayed_valid_replies_after_abort_never_copy_payload(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        responses = []
        for value in range(MODEL.RESPONSE_CREDITS):
            index = model.try_send(True)
            responses.append(
                model.make_response(
                    index, bytes([value + 1]) * MODEL.LINE_BYTES
                )
            )
        self.assertFalse(model.abort(MODEL.AbortCode.CALLER))
        for response in responses[:-1]:
            self.assertEqual(
                receive_on(model, response),
                MODEL.ReplyStatus.ABORT_OWNER_DRAINED,
            )
            self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))
        self.assertEqual(
            receive_on(model, responses[-1]), MODEL.ReplyStatus.ABORT_DRAINED
        )
        self.assertEqual(model.slots[0].payload, bytes(MODEL.PAGE_BYTES))

    def test_exact_fill_requires_512_unique_payload_lines(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model, page=2)
        self.assertEqual(drain_exact(model), MODEL.LINES_PER_PAGE)
        expected = b"".join(
            MODEL.line_pattern(2, line) for line in range(MODEL.LINES_PER_PAGE)
        )
        self.assertEqual(bytes(model.slots[0].payload), expected)
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.CLEAN)

    def test_four_pages_data_and_2048_write_responses_are_exact(self):
        model = MODEL.LogicalCacheModel()
        allocate_pair(model)
        fill_responses = 0
        write_responses = 0
        for page in range(MODEL.PAGES_PER_DESCRIPTOR):
            slot = page & 1
            start_fill(model, page, slot)
            fill_responses += drain_exact(model)
            expected = b"".join(
                MODEL.line_pattern(page, line)
                for line in range(MODEL.LINES_PER_PAGE)
            )
            self.assertEqual(bytes(model.slots[slot].payload), expected)
            model.pin(slot)
            model.mark_dirty(slot)
            model.bind_dirty_destination(slot, 1, page)
            model.unpin(slot)
            model.start_writeback(slot)
            seen_lines = set()
            while not model.transport.drained():
                while -1 in model.transport.credit_owner:
                    index = model.try_send(True)
                    if index is None:
                        break
                inflight = [
                    index
                    for index, record in enumerate(model.transport.records)
                    if record.state == MODEL.RecordState.IN_FLIGHT
                ]
                for index in inflight:
                    record = model.transport.records[index]
                    line = record.key.line
                    seen_lines.add(line)
                    offset = line * MODEL.LINE_BYTES
                    self.assertEqual(
                        record.line_buffer,
                        expected[offset : offset + MODEL.LINE_BYTES],
                    )
                    receive_on(model, model.make_response(index, b""))
                    write_responses += 1
            self.assertEqual(len(seen_lines), MODEL.LINES_PER_PAGE)
        self.assertEqual(fill_responses, 2048)
        self.assertEqual(write_responses, 2048)
        self.assertTrue(model.descriptor_complete(1))
        complete_bits = (1 << MODEL.PAGES_PER_DESCRIPTOR) - 1
        self.assertEqual(model.descriptors[1].writeback_acked, complete_bits)
        self.assertEqual(model.descriptors[1].backing_ready, complete_bits)

    def test_wrong_base_page_generation_overlap_and_u64_fail_atomically(self):
        model = MODEL.LogicalCacheModel()
        source_generation = allocate_source(model)
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "overlap"):
            model.allocate(1, SRC_BASE)
        self.assertEqual(model.digest(), before)
        for kwargs, pattern in (
            ({"page": 4}, "page"),
            ({"page": 0, "claimed_base": MODEL.PAGE_BYTES}, "base"),
            (
                {"page": 0, "claimed_generation": source_generation + 1},
                "generation",
            ),
        ):
            with self.subTest(kwargs=kwargs):
                before = model.digest()
                with self.assertRaisesRegex(MODEL.ContractError, pattern):
                    model.start_fill(0, slot=0, **kwargs)
                self.assertEqual(model.digest(), before)
        overflow_base = MODEL.U64_MAX + 1 - MODEL.BACKING_SPAN
        other = MODEL.LogicalCacheModel()
        self.assertEqual(other.allocate(0, overflow_base), 1)
        before = other.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "u64"):
            other.allocate(1, overflow_base + MODEL.BACKING_SPAN)
        self.assertEqual(other.digest(), before)

    def test_source_dirty_role_cannot_write_until_atomic_destination_binding(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        _, destination_generation = allocate_pair(model)
        start_fill(model)
        drain_exact(model)
        model.pin(0)
        model.mark_dirty(0)
        model.unpin(0)
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "destination-dirty"):
            model.start_writeback(0)
        self.assertEqual(model.digest(), before)
        model.pin(0)
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "generation"):
            model.bind_dirty_destination(
                0, 1, 0, claimed_generation=destination_generation + 1
            )
        self.assertEqual(model.digest(), before)
        model.bind_dirty_destination(0, 1, 0, destination_generation)
        self.assertEqual(model.slots[0].role, MODEL.SlotRole.DESTINATION)
        model.unpin(0)
        for claimed_base, claimed_generation, pattern in (
            (DST_BASE + MODEL.PAGE_BYTES, None, "base"),
            (None, destination_generation + 1, "generation"),
        ):
            with self.subTest(
                claimed_base=claimed_base,
                claimed_generation=claimed_generation,
            ):
                before = model.digest()
                with self.assertRaisesRegex(MODEL.ContractError, pattern):
                    model.start_writeback(
                        0,
                        claimed_base=claimed_base,
                        claimed_generation=claimed_generation,
                    )
                self.assertEqual(model.digest(), before)
        model.start_writeback(0)

    def test_destination_owned_page_cannot_be_republished_into_other_slot(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_pair(model)
        start_fill(model, page=0, slot=0)
        drain_exact(model)
        model.pin(0)
        model.mark_dirty(0)
        model.bind_dirty_destination(0, 1, 0)
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "destination-owned"):
            model.publish_backing(1, 0)
        self.assertEqual(model.digest(), before)
        with self.assertRaisesRegex(MODEL.ContractError, "backing-ready"):
            model.start_fill(1, 0, 1)
        self.assertEqual(model.digest(), before)
        model.assert_invariants()
        model.unpin(0)
        model.start_writeback(0)
        drain_exact(model, fill_data=False)
        bit = 1 << 0
        self.assertEqual(model.descriptors[1].writeback_acked & bit, bit)
        self.assertEqual(model.descriptors[1].backing_ready & bit, bit)
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.EMPTY)
        self.assertFalse(model.descriptor_complete(1))
        acknowledged = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "acknowledged"):
            model.publish_backing(1, 0)
        self.assertEqual(model.digest(), acknowledged)
        model.start_fill(1, 0, 1)
        drain_exact(model)
        expected = b"".join(
            MODEL.line_pattern(0, line) for line in range(MODEL.LINES_PER_PAGE)
        )
        self.assertEqual(model.slots[1].phase, MODEL.SlotPhase.CLEAN)
        self.assertEqual(model.slots[1].role, MODEL.SlotRole.SOURCE)
        self.assertEqual(bytes(model.slots[1].payload), expected)
        self.assertEqual(model.descriptors[1].writeback_acked & bit, bit)
        self.assertEqual(model.descriptors[1].backing_ready & bit, bit)
        model.assert_invariants()

    def test_pin_dirty_replacement_and_writeback_abort_are_safe(self):
        model = MODEL.LogicalCacheModel()
        allocate_pair(model)
        start_fill(model)
        drain_exact(model)
        model.pin(0)
        with self.assertRaisesRegex(MODEL.ContractError, "unpinned clean"):
            model.evict_clean(0)
        model.mark_dirty(0)
        model.bind_dirty_destination(0, 1, 0)
        model.unpin(0)
        model.start_writeback(0)
        index = model.try_send(True)
        response = model.make_response(index, b"")
        self.assertFalse(model.abort(MODEL.AbortCode.CALLER))
        self.assertEqual(
            receive_on(model, response), MODEL.ReplyStatus.ABORT_DRAINED
        )
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.DIRTY)
        self.assertEqual(model.slots[0].role, MODEL.SlotRole.DESTINATION)

    def test_writeback_accepts_only_write_response(self):
        model = MODEL.LogicalCacheModel()
        allocate_pair(model)
        start_fill(model)
        drain_exact(model)
        model.pin(0)
        model.mark_dirty(0)
        model.bind_dirty_destination(0, 1, 0)
        model.unpin(0)
        model.start_writeback(0)
        index = model.try_send(True)
        response = model.make_response(index, b"")
        before = model.digest()
        with self.assertRaises(MODEL.ProductionStop):
            receive_on(
                model, replace(response, command="ReadRespWithInvalidate")
            )
        self.assertEqual(model.digest(), before)
        # Test-harness-only inspection after the modeled production panic.
        self.assertEqual(
            receive_on(model, response), MODEL.ReplyStatus.ACCEPTED
        )

    def test_generation_epoch_action_and_incarnations_never_wrap(self):
        model = MODEL.LogicalCacheModel()
        model.descriptors[0].generation = MODEL.GENERATION_MAX
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ExhaustedError, "generation"):
            model.allocate(0, SRC_BASE)
        self.assertEqual(model.digest(), before)

        transport = MODEL.DedicatedTransport()
        for record in transport.records:
            record.epoch = MODEL.RECORD_EPOCH_MAX
        with self.assertRaisesRegex(MODEL.ExhaustedError, "epochs"):
            transport.start_action(
                MODEL.Operation.FILL, 0, 1, 0, 0, 0, bytes(MODEL.PAGE_BYTES)
            )
        transport = MODEL.DedicatedTransport()
        transport.next_incarnation_id = MODEL.INCARNATION_ID_MAX - 1000
        before = (
            transport.next_incarnation_id,
            transport.next_action_id,
            transport.action.state,
            tuple(record.epoch for record in transport.records),
        )
        with self.assertRaisesRegex(MODEL.ExhaustedError, "incarnations"):
            transport.start_action(
                MODEL.Operation.FILL, 0, 1, 0, 0, 0, bytes(MODEL.PAGE_BYTES)
            )
        self.assertEqual(
            (
                transport.next_incarnation_id,
                transport.next_action_id,
                transport.action.state,
                tuple(record.epoch for record in transport.records),
            ),
            before,
        )
        transport = MODEL.DedicatedTransport()
        transport.next_action_id = MODEL.ACTION_ID_MAX
        transport.start_action(
            MODEL.Operation.FILL, 0, 1, 0, 0, 0, bytes(MODEL.PAGE_BYTES)
        )
        transport.abort_action(MODEL.AbortCode.CALLER)
        with self.assertRaisesRegex(MODEL.ExhaustedError, "action"):
            transport.start_action(
                MODEL.Operation.FILL, 0, 1, 0, 0, 0, bytes(MODEL.PAGE_BYTES)
            )

    def test_materialization_identity_exhaustion_is_full_digest_atomic(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        model.transport.next_incarnation_id = MODEL.INCARNATION_ID_MAX
        model.transport.incarnation_ids_exhausted = False
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ExhaustedError, "incarnation"):
            model.try_send(True)
        self.assertEqual(model.digest(), before)
        self.assertEqual(
            model.transport.queue_count, MODEL.TRANSACTION_CAPACITY
        )
        self.assertEqual(
            model.transport.records[model.transport.queue[0]].state,
            MODEL.RecordState.QUEUED,
        )
        model.assert_invariants()

    def test_every_post_preview_host_construction_failure_is_digest_atomic(
        self,
    ):
        self.assertEqual(
            MODEL.MATERIALIZATION_FAULT_POINTS,
            ("request_ptr", "line_snapshot", "request_packet"),
        )
        for fault_point in MODEL.MATERIALIZATION_FAULT_POINTS:
            with self.subTest(fault_point=fault_point):
                model = MODEL.LogicalCacheModel()
                allocate_pair(model)
                start_fill(model)
                drain_exact(model)
                model.pin(0)
                model.mark_dirty(0)
                model.bind_dirty_destination(0, 1, 0)
                model.unpin(0)
                model.start_writeback(0)

                def inject(point):
                    if point == fault_point:
                        raise RuntimeError(f"injected {point} failure")

                model.transport._materialization_fault = inject
                before = model.digest()
                with self.assertRaisesRegex(RuntimeError, fault_point):
                    model.try_send(True)
                self.assertEqual(model.digest(), before)
                self.assertEqual(
                    model.transport.queue_count,
                    MODEL.TRANSACTION_CAPACITY,
                )
                self.assertEqual(model.transport.credit_owner.count(-1), 4)
                self.assertEqual(model.transport.pending, -1)
                model.assert_invariants()

    def test_test_peer_responses_cannot_consume_reserved_controller_identities(
        self,
    ):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        index = model.try_send(True)
        reserved_after_request = model.transport.next_incarnation_id
        first = model.make_response(index, bytes(MODEL.LINE_BYTES))
        second = model.make_response(index, bytes(MODEL.LINE_BYTES))
        self.assertNotEqual(first.incarnation, second.incarnation)
        self.assertEqual(
            model.transport.next_incarnation_id, reserved_after_request
        )

    def test_reset_teardown_and_all_public_mutators_are_terminal(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        start_fill(model)
        with self.assertRaisesRegex(MODEL.ContractError, "drained"):
            model.reset()
        model.abort(MODEL.AbortCode.CALLER)
        model.reset()
        preserved_generation = model.descriptors[0].generation
        model.teardown()
        self.assertEqual(model.descriptors[0].generation, preserved_generation)
        dummy = MODEL.PacketIncarnation(
            1,
            MODEL.RequestPtr(1),
            (),
            0,
            "ReadResp",
            64,
            bytes(64),
        )
        mutators = (
            lambda: model.allocate(0, 0),
            lambda: model.publish_backing(0, 0),
            lambda: model.start_fill(0, 0, 0),
            lambda: model.try_send(True),
            lambda: model.recv_req_retry(0),
            lambda: model.make_response(0),
            lambda: model.begin_receive(MODEL.ReturnedPacketOwner(dummy), 0),
            lambda: model.commit_delivery(MODEL.DeliveryTicket(0, 0, 0)),
            lambda: model.receive(MODEL.ReturnedPacketOwner(dummy), 0),
            lambda: model.pin(0),
            lambda: model.mark_dirty(0),
            lambda: model.bind_dirty_destination(0, 1, 0),
            lambda: model.unpin(0),
            lambda: model.evict_clean(0),
            lambda: model.start_writeback(0),
            lambda: model.abort(MODEL.AbortCode.CALLER),
            lambda: model.free_descriptor(0),
            model.reset,
            model.teardown,
        )
        for mutate in mutators:
            with self.subTest(mutate=mutate):
                with self.assertRaisesRegex(MODEL.ContractError, "torn down"):
                    mutate()

    def test_teardown_requires_free_descriptors_and_empty_slots(self):
        model = MODEL.LogicalCacheModel()
        allocate_source(model)
        with self.assertRaisesRegex(MODEL.ContractError, "free descriptors"):
            model.teardown()
        start_fill(model)
        drain_exact(model)
        model.evict_clean(0)
        model.free_descriptor(0)
        model.teardown()

    def test_duplicate_deterministic_replays(self):
        first = MODEL.deterministic_demo()
        second = MODEL.deterministic_demo()
        self.assertEqual(first, second)
        self.assertTrue(first["destination_complete"])


if __name__ == "__main__":
    unittest.main()
