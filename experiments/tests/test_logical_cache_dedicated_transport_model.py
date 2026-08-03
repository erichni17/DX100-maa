import importlib.util
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / (
    "experiments/analysis/logical_cache_dedicated_transport_model.py"
)
SPEC = importlib.util.spec_from_file_location(
    "logical_cache_dedicated_transport_model", MODULE_PATH
)
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)


def ready_source(model, descriptor=0):
    generation = model.allocate(descriptor)
    for page in range(MODEL.PAGES_PER_DESCRIPTOR):
        model.publish_backing(descriptor, page)
    return generation


def start_fill(model, page=0, slot=0):
    return model.start_fill(
        0,
        page,
        slot,
        page * MODEL.PAGE_BYTES,
        3,
    )


def send_one(model, accepted=True):
    index = model.transport.try_send(accepted)
    return index, (
        model.transport.expected_reply(index) if index is not None else None
    )


class DedicatedTransportContractTest(unittest.TestCase):
    def test_geometry_capacity_and_storage_are_explicit(self):
        self.assertEqual(MODEL.DESCRIPTORS, 2)
        self.assertEqual(MODEL.PAGES_PER_DESCRIPTOR, 4)
        self.assertEqual(MODEL.SLOTS, 2)
        self.assertEqual(MODEL.LINES_PER_PAGE, 512)
        self.assertEqual(len(MODEL.DedicatedTransport().records), 8)
        self.assertEqual(sum(MODEL.STORAGE_LEDGER_BYTES.values()), 66_392)

    def test_queue_capacity_and_credit_backpressure(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        self.assertEqual(model.transport.queue_count, 8)
        sent = [model.transport.try_send(True) for _ in range(4)]
        self.assertNotIn(None, sent)
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "credits"):
            model.transport.try_send(True)
        self.assertEqual(model.digest(), before)

    def test_retry_retains_the_same_authoritative_packet(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        index, reply = send_one(model, accepted=False)
        self.assertIsNone(index)
        pending = model.transport.pending
        packet_id = model.transport.records[pending].packet_id
        with self.assertRaisesRegex(MODEL.ContractError, "recvReqRetry"):
            model.transport.try_send(True)
        with self.assertRaisesRegex(MODEL.ContractError, "wrong port"):
            model.transport.recv_req_retry(2)
        model.transport.recv_req_retry(3)
        self.assertEqual(model.transport.try_send(True), pending)
        self.assertEqual(model.transport.records[pending].packet_id, packet_id)
        self.assertIsNone(reply)

    def test_exact_fill_requires_all_512_unique_responses(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        statuses = []
        while model.transport.action.ack_count < MODEL.LINES_PER_PAGE - 1:
            index, reply = send_one(model)
            self.assertIsNotNone(index)
            statuses.append(model.receive(reply))
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.FILLING)
        index, reply = send_one(model)
        self.assertEqual(model.receive(reply), MODEL.ReplyStatus.COMPLETED)
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.CLEAN)
        self.assertTrue(all(x == MODEL.ReplyStatus.ACCEPTED for x in statuses))

    def test_duplicate_and_stale_reply_mutate_no_owner(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        _, reply = send_one(model)
        self.assertEqual(model.receive(reply), MODEL.ReplyStatus.ACCEPTED)
        before = model.digest()
        self.assertEqual(
            model.receive(reply), MODEL.ReplyStatus.DUPLICATE_OR_STALE
        )
        # Only the dedicated diagnostic counter changes; ownership does not.
        self.assertEqual(model.transport.action.ack_count, 1)
        self.assertEqual(model.transport.fault_stale, 1)
        self.assertNotEqual(model.digest(), before)

    def test_foreign_reply_does_not_mutate_an_owner(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        _, reply = send_one(model)
        ack_count = model.transport.action.ack_count
        foreign = replace(
            reply,
            record=99,
            epoch=MODEL.RECORD_EPOCH_MAX,
            packet_id=0xFFFF_FFFE,
        )
        self.assertEqual(model.receive(foreign), MODEL.ReplyStatus.FOREIGN)
        self.assertEqual(model.transport.action.ack_count, ack_count)
        self.assertEqual(model.transport.credit_owner.count(-1), 3)

    def _assert_corrupt_reply_aborts_exact_action(self, change):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        replies = []
        for _ in range(3):
            _, reply = send_one(model)
            replies.append(reply)
        bad = change(replies[0])
        self.assertEqual(
            model.receive(bad), MODEL.ReplyStatus.CORRUPT_OWNER_ABORTED
        )
        self.assertEqual(
            model.transport.action.state, MODEL.ActionState.ABORT_DRAIN
        )
        # Two sent siblings remain owned; unsent records were destroyed.
        self.assertEqual(
            sum(
                r.state == MODEL.RecordState.ABORT_DRAIN
                for r in model.transport.records
            ),
            2,
        )
        for reply in replies[1:]:
            model.receive(reply)
        self.assertTrue(model.transport.drained())
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.EMPTY)

    def test_wrong_port_reply_is_fail_closed(self):
        self._assert_corrupt_reply_aborts_exact_action(
            lambda reply: replace(reply, port=reply.port + 1)
        )

    def test_wrong_size_reply_is_fail_closed(self):
        self._assert_corrupt_reply_aborts_exact_action(
            lambda reply: replace(reply, size=8)
        )

    def test_wrong_command_address_and_key_are_fail_closed(self):
        changes = (
            lambda reply: replace(reply, command="WriteResp"),
            lambda reply: replace(reply, address=reply.address + 64),
            lambda reply: replace(
                reply, key=replace(reply.key, line=reply.key.line + 1)
            ),
        )
        for change in changes:
            with self.subTest(change=change):
                self._assert_corrupt_reply_aborts_exact_action(change)

    def test_generation_and_record_epoch_exhaustion_do_not_wrap(self):
        model = MODEL.LogicalCacheModel()
        model.descriptors[0].generation = MODEL.GENERATION_MAX
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ExhaustedError, "generation"):
            model.allocate(0)
        self.assertEqual(model.digest(), before)

        transport = MODEL.DedicatedTransport()
        for record in transport.records:
            record.epoch = MODEL.RECORD_EPOCH_MAX
        before = transport.next_action_id
        with self.assertRaisesRegex(MODEL.ExhaustedError, "epochs"):
            transport.start_action(MODEL.Operation.FILL, 0, 1, 0, 0, 0, 0)
        self.assertEqual(transport.action.state, MODEL.ActionState.FREE)
        self.assertEqual(transport.next_action_id, before)

        transport = MODEL.DedicatedTransport()
        transport.next_packet_id = MODEL.PACKET_ID_MAX - 510
        with self.assertRaisesRegex(MODEL.ExhaustedError, "packet identities"):
            transport.start_action(MODEL.Operation.FILL, 0, 1, 0, 0, 0, 0)
        self.assertEqual(transport.action.state, MODEL.ActionState.FREE)

        transport = MODEL.DedicatedTransport()
        transport.next_action_id = MODEL.ACTION_ID_MAX
        transport.start_action(MODEL.Operation.FILL, 0, 1, 0, 0, 0, 0)
        self.assertTrue(transport.action_ids_exhausted)
        self.assertEqual(transport.next_action_id, MODEL.ACTION_ID_MAX)
        transport.abort_action("identity test")
        with self.assertRaisesRegex(MODEL.ExhaustedError, "action identity"):
            transport.start_action(MODEL.Operation.FILL, 0, 1, 0, 0, 0, 0)

    def test_fault_counters_saturate_without_becoming_owners(self):
        transport = MODEL.DedicatedTransport()
        transport.fault_foreign = MODEL.DIAGNOSTIC_MAX
        key = MODEL.TransactionKey(0, 1, 0, 0, 0, MODEL.Operation.FILL)
        foreign = MODEL.Reply(99, 99, 99, key, 0, "ReadResp", 64, 0)
        self.assertEqual(transport.receive(foreign), MODEL.ReplyStatus.FOREIGN)
        self.assertEqual(transport.fault_foreign, MODEL.DIAGNOSTIC_MAX)
        self.assertTrue(transport.drained())

    def test_abort_keeps_inflight_packets_until_drain(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        replies = [send_one(model)[1] for _ in range(4)]
        self.assertFalse(model.abort("test abort"))
        self.assertFalse(model.transport.drained())
        for reply in replies[:-1]:
            self.assertEqual(
                model.receive(reply), MODEL.ReplyStatus.ABORT_OWNER_DRAINED
            )
        self.assertEqual(
            model.receive(replies[-1]), MODEL.ReplyStatus.ABORT_DRAINED
        )
        self.assertTrue(model.transport.drained())
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.EMPTY)

    def test_dirty_and_pin_block_replacement(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        MODEL.drive_action(model)
        model.pin(0)
        with self.assertRaisesRegex(MODEL.ContractError, "unpinned clean"):
            model.evict_clean(0)
        model.mark_dirty(0)
        model.unpin(0)
        with self.assertRaisesRegex(MODEL.ContractError, "unpinned clean"):
            model.evict_clean(0)
        with self.assertRaisesRegex(MODEL.ContractError, "empty slot"):
            model.start_fill(0, 1, 0, MODEL.PAGE_BYTES, 3)

    def test_writeback_and_high_level_completion_wait_for_exact_acks(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        destination_generation = model.allocate(1)
        for page in range(MODEL.PAGES_PER_DESCRIPTOR):
            slot = page & 1
            model.start_fill(0, page, slot, page * MODEL.PAGE_BYTES, 3)
            MODEL.drive_action(model)
            model.pin(slot)
            model.mark_dirty(slot)
            model.bind_dirty_destination(slot, 1, page)
            model.unpin(slot)
            model.start_writeback(slot, 0x100000 + page * MODEL.PAGE_BYTES, 3)
            while model.transport.action.ack_count < MODEL.LINES_PER_PAGE - 1:
                _, reply = send_one(model)
                model.receive(reply)
            self.assertFalse(model.descriptor_complete(1))
            _, reply = send_one(model)
            self.assertEqual(model.receive(reply), MODEL.ReplyStatus.COMPLETED)
            self.assertEqual(model.slots[slot].phase, MODEL.SlotPhase.EMPTY)
        self.assertTrue(model.descriptor_complete(1))

    def test_dirty_destination_binding_is_exact_and_atomic(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        model.allocate(1)
        start_fill(model)
        MODEL.drive_action(model)
        model.pin(0)
        model.mark_dirty(0)
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "must differ"):
            model.bind_dirty_destination(0, 0, 0)
        self.assertEqual(model.digest(), before)
        model.bind_dirty_destination(0, 1, 0)
        self.assertEqual(model.slots[0].descriptor, 1)

    def test_writeback_abort_returns_owned_slot_to_dirty(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        MODEL.drive_action(model)
        model.pin(0)
        model.mark_dirty(0)
        model.unpin(0)
        model.start_writeback(0, 0x100000, 3)
        _, reply = send_one(model)
        self.assertFalse(model.abort("cancel writeback"))
        self.assertEqual(model.receive(reply), MODEL.ReplyStatus.ABORT_DRAINED)
        self.assertEqual(model.slots[0].phase, MODEL.SlotPhase.DIRTY)

    def test_reset_and_teardown_require_clean_drain(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        start_fill(model)
        with self.assertRaisesRegex(MODEL.ContractError, "drained"):
            model.reset()
        model.abort("reset")
        model.reset()
        self.assertFalse(model.descriptors[0].allocated)
        preserved_generation = model.descriptors[0].generation
        model.teardown()
        self.assertTrue(model.torn_down)
        self.assertEqual(model.descriptors[0].generation, preserved_generation)
        with self.assertRaisesRegex(MODEL.ContractError, "sealed"):
            model.transport.start_action(
                MODEL.Operation.FILL, 0, 1, 0, 0, 0, 0
            )

    def test_teardown_rejects_allocated_or_resident_state(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        with self.assertRaisesRegex(MODEL.ContractError, "free descriptors"):
            model.teardown()
        start_fill(model)
        MODEL.drive_action(model)
        with self.assertRaisesRegex(MODEL.ContractError, "free descriptors"):
            model.teardown()
        model.evict_clean(0)
        model.free_descriptor(0)
        model.teardown()

    def test_exception_atomicity_for_invalid_start_and_busy_transport(self):
        model = MODEL.LogicalCacheModel()
        ready_source(model)
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "aligned"):
            model.start_fill(0, 0, 0, 1, 3)
        self.assertEqual(model.digest(), before)
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "slot"):
            model.pin(-1)
        self.assertEqual(model.digest(), before)
        transport = MODEL.DedicatedTransport()
        before_action_id = transport.next_action_id
        with self.assertRaisesRegex(MODEL.ContractError, "operation"):
            transport.start_action("fill", 0, 1, 0, 0, 0, 0)
        self.assertEqual(transport.next_action_id, before_action_id)
        self.assertTrue(transport.drained())
        start_fill(model)
        before = model.digest()
        with self.assertRaisesRegex(MODEL.ContractError, "empty slot"):
            model.start_fill(0, 1, 0, MODEL.PAGE_BYTES, 3)
        self.assertEqual(model.digest(), before)

    def test_duplicate_deterministic_replays(self):
        first = MODEL.deterministic_demo()
        second = MODEL.deterministic_demo()
        self.assertEqual(first, second)
        self.assertTrue(first["destination_complete"])


if __name__ == "__main__":
    unittest.main()
