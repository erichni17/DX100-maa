#!/usr/bin/env python3
"""Focused source and FP32 models for CG's single-pass SoA/JIT RMW order."""

import struct
import unittest
from collections import (
    defaultdict,
    deque,
)
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
TABLES = (ROOT / "src/mem/MAA/Tables.cc").read_text()
CG_SOURCE = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
AUDIT = (
    ROOT
    / "experiments/analysis/cg_single_pass_destination_order_2026-08-25.md"
)


def f32(value: float) -> float:
    """Round after every modeled hardware FP32 operation."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def add_f32(left: float, right: float) -> float:
    return f32(f32(left) + f32(right))


def serial_by_word(events, initial):
    result = dict(initial)
    for _, word, value in events:
        result[word] = add_f32(result[word], value)
    return result


def single_pass_rowtable_model(events, initial, epoch_capacity):
    """Model only legal same-line scheduling, not unrelated-line order.

    A RowTable chain is keyed by destination cache line.  Its entries retain
    source insertion order.  An adversarial response schedule may ready later
    aliases first, but an apply consumes only the context's chain head.
    """
    result = dict(initial)
    cursor = 0
    committed = []
    while cursor < len(events):
        epoch = events[cursor : cursor + epoch_capacity]
        chains = defaultdict(deque)
        for ordinal, word, value in epoch:
            chains[word // 16].append((ordinal, word, value))

        # Deliberately choose contexts/lines in reverse order.  This exercises
        # cross-cache-line and cross-context reordering without allowing two
        # contexts for the same line.  A completion order is also reverse per
        # context, but the queued head remains the only apply candidate.
        for line in sorted(chains, reverse=True):
            ready = list(reversed(chains[line]))
            chain = chains[line]
            while chain:
                head = chain[0]
                self_ready = ready.pop()
                assert self_ready == head
                ordinal, word, value = chain.popleft()
                result[word] = add_f32(result[word], value)
                committed.append(ordinal)
        cursor += len(epoch)
    return result, committed


class SoaJitDestinationOrderTests(unittest.TestCase):
    def test_fp32_collisions_across_lines_contexts_lookahead_and_epochs(self):
        # Words 0 and 33 collide repeatedly.  0 is at the start of one cache
        # line and 33 is in another, so their contexts may run in either order.
        # The cancellation pattern changes FP32 bits if either collision chain
        # is reordered.  7 forces an unrelated destination in the first line.
        values = [16777216.0, 1.0, -16777216.0, 1.0]
        events = [
            (0, 0, values[0]),
            (1, 33, values[0]),
            (2, 7, 3.0),
            (3, 0, values[1]),
            (4, 33, values[1]),
            (5, 0, values[2]),
            (6, 33, values[2]),
            (7, 7, -3.0),
            (8, 0, values[3]),
            (9, 33, values[3]),
        ]
        initial = {0: f32(0.0), 7: f32(0.0), 33: f32(0.0)}
        expected = serial_by_word(events, initial)

        # Capacity three forces retry/drain boundaries in the middle of both
        # order-sensitive chains.  The model's reverse line/context schedule
        # must still produce the exact per-word serial FP32 result.
        actual, committed = single_pass_rowtable_model(events, initial, 3)
        self.assertCountEqual(committed, list(range(len(events))))
        for word in initial:
            self.assertEqual(
                [
                    ordinal
                    for ordinal, candidate, _ in events
                    if candidate == word
                ],
                [
                    ordinal
                    for ordinal in committed
                    if events[ordinal][1] == word
                ],
            )
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], f32(1.0))
        self.assertEqual(actual[33], f32(1.0))
        self.assertEqual(actual[7], f32(0.0))

    def test_source_contract_has_one_head_only_context_for_each_a_line(self):
        insert = TABLES[
            TABLES.index("bool RowTableEntry::insert") : TABLES.index(
                "void RowTableEntry::check_reset"
            )
        ]
        self.assertIn(
            "offset_table->insert(itr, wid, entries[i].last_itr", insert
        )
        self.assertIn("entries[i].last_itr =", insert)

        build = SOURCE[
            SOURCE.index(
                "bool IndirectAccessUnit::serviceSoaJitBuild()"
            ) : SOURCE.index("IndirectAccessUnit::issueSoaJitScalar")
        ]
        self.assertIn("claim_entry_send(", build)
        self.assertIn("active.aPaddr == addr", build)
        self.assertIn("claimed duplicate active A line", build)
        self.assertIn("context->nextOffset = head", build)

        lookahead = SOURCE[
            SOURCE.index(
                "IndirectAccessUnit::serviceSoaJitLookahead()"
            ) : SOURCE.index(
                "IndirectAccessUnit::serviceSoaJitOldResultWrites"
            )
        ]
        self.assertIn("candidate.offset == context.nextOffset", lookahead)
        self.assertIn(
            "offset_table->consume_entry(context.nextOffset)", lookahead
        )
        self.assertIn("expected_offset != slot->offset", lookahead)
        self.assertLess(
            lookahead.index("candidate.offset == context.nextOffset"),
            lookahead.index("offset_table->consume_entry(context.nextOffset)"),
        )

    def test_pressure_epoch_keeps_the_uncommitted_source_ordinal(self):
        retry = SOURCE[
            SOURCE.index(
                "IndirectAccessUnit::rememberSoaJitPressureRetry"
            ) : SOURCE.index("IndirectAccessUnit::commitSoaJitSourceOrdinal")
        ]
        self.assertIn("logical_itr != my_i", retry)
        self.assertIn("soa_jit_next_source_ordinal", retry)

        reset = SOURCE[
            SOURCE.index(
                "IndirectAccessUnit::resetSoaJitEpochTables()"
            ) : SOURCE.index("bool IndirectAccessUnit::serviceSoaJitBuild()")
        ]
        self.assertIn("soa_jit_epoch_resume_i != my_i", reset)
        self.assertIn("offset_table->occupancy() != 0", reset)
        self.assertIn("!soaJitContextsEmpty()", reset)

        request = SOURCE[
            SOURCE.index("case Status::Request:") : SOURCE.index(
                "if (usesBoundedSourceResponses())",
                SOURCE.index("case Status::Request:"),
            )
        ]
        self.assertLess(
            request.index("if (!soaJitContextsEmpty())"),
            request.index("resetSoaJitEpochTables()"),
        )
        self.assertLess(
            request.index("resetSoaJitEpochTables()"),
            request.index('"soa_epoch_refill"'),
        )

    def test_cg_keeps_one_useful_16k_selected_set_and_four_physical_pages(
        self,
    ):
        helper = CG_SOURCE[
            CG_SOURCE.index("cg_physical_page_product_rmw") : CG_SOURCE.index(
                "#endif\n#endif\n\n#ifdef CG_FP_ENABLE"
            )
        ]
        self.assertIn("maa_const<int>(TILE_SIZE, max_reg)", helper)
        self.assertIn("maa_indirect_rmw_vector_soa_jit<float>", helper)
        self.assertIn("cg_soa_indices[tid], cg_soa_products[tid]", helper)
        self.assertNotIn("soaJitMasked", helper)
        self.assertIn("page_offset += MAA_CONSUMER_TILE_SIZE", CG_SOURCE)
        self.assertIn("logical_page >= 4", CG_SOURCE)
        self.assertIn("physical_alus == full_windows * 4", CG_SOURCE)

    def test_audit_rejects_masked_four_passes_and_names_the_next_boundary(
        self,
    ):
        audit = AUDIT.read_text()
        self.assertIn("No new ordering hardware is added", audit)
        self.assertIn("0 B", audit)
        self.assertIn("Four masked 4K passes", audit)
        self.assertIn("not a valid successor", audit)
        self.assertIn("cg_soa_products", audit)
        self.assertIn("bitwise", audit)


if __name__ == "__main__":
    unittest.main()
