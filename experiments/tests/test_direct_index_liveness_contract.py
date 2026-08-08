#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DirectIndexLivenessContractTest(unittest.TestCase):
    def test_selected_word_is_discarded_only_after_descriptor_insert(self):
        source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        begin = source.index("void IndirectAccessUnit::fillRowTable")
        end = source.index(
            "void IndirectAccessUnit::chargeDirectIndexFilterLatency", begin
        )
        fill = source[begin:end]
        insert = fill.index("RT[my_RT_config][my_RT_idx].insert(")
        commit = fill.index("direct_index_descriptor_inserted = true", insert)
        discard = fill.index("discardDirectIndex(", commit)
        self.assertLess(insert, commit)
        self.assertLess(commit, discard)
        failed_insert = fill[insert:commit]
        self.assertIn("if (!inserted)", failed_insert)
        self.assertIn("break;", failed_insert)

    def test_descriptor_retains_issue_and_placement_fields(self):
        header = (ROOT / "src/mem/MAA/Tables.hh").read_text()
        offset_begin = header.index("struct OffsetTableEntry")
        offset_end = header.index("class OffsetTable", offset_begin)
        offset = header[offset_begin:offset_end]
        row_begin = header.index("class RowTableEntry")
        row_end = header.index("class RowTableSlice", row_begin)
        row = header[row_begin:row_end]
        self.assertIn("Addr addr;", row)
        self.assertIn("int first_itr;", row)
        self.assertIn("int last_itr;", row)
        self.assertIn("int itr;", offset)
        self.assertIn("int wid;", offset)
        self.assertIn("int next_itr;", offset)

        source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        self.assertIn(
            "issueVirtualSource(\n                                addr,",
            source,
        )
        self.assertIn("dataptr + entry.wid * my_word_size", source)
        self.assertIn("insertVirtualCombineWord(entry.itr", source)

    def test_every_post_admission_consumer_uses_descriptor_metadata(self):
        source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()

        build = source[
            source.index("case Status::Build:") : source.index(
                "case Status::Request:"
            )
        ]
        bounded_response = source[
            source.index("const bool bounded_response_load") : source.index(
                "uint8_t new_data[block_size]"
            )
        ]
        drain = source[
            source.index("bool IndirectAccessUnit::drainVirtualResponses") :
            source.index("bool IndirectAccessUnit::reserveVirtualCombineBank")
        ]
        placement = source[
            source.index("Addr IndirectAccessUnit::backingWordAddr") :
            source.index("void IndirectAccessUnit::drainVirtualCombiner")
        ]

        # Request issue retains the source line plus the OffsetTable-chain
        # head/count. Response capture uses wid to select A's returned word.
        self.assertIn("claim_entry_send(\n                                addr", build)
        self.assertIn("VirtualSourceReservation{source_head", build)
        self.assertIn("createReadPacket(source_addr", build)
        self.assertIn("slot->next_itr = virtual_head", bounded_response)
        self.assertIn("offset_table->peek_entry(itr)", bounded_response)
        self.assertIn("dataptr + entry.wid * my_word_size", bounded_response)

        # Retirement consumes the linked chain in its retained order. itr is
        # the logical destination identity; the instruction-owned backing base
        # supplies the other half of the destination address.
        self.assertIn("insertVirtualCombineWord(entry.itr", drain)
        self.assertIn("offset_table->consume_entry(slot.next_itr)", drain)
        self.assertIn(
            "return my_backing_addr + index * my_word_size", placement
        )
        self.assertIn("const Addr vaddr = backingWordAddr(itr)", placement)

        # These are all consumers downstream of successful admission. A later
        # direct-index refill may populate new iterations, but none of these
        # consumers rereads the admitted feeder value.
        for consumer in (build, bounded_response, drain, placement):
            self.assertNotIn("peekDirectIndex(", consumer)
            self.assertNotIn("direct_index_value", consumer)

    def test_poison_is_confined_to_private_feeder_copy(self):
        source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        begin = source.index("void IndirectAccessUnit::discardDirectIndex")
        end = source.index(
            "bool IndirectAccessUnit::receiveDirectIndex", begin
        )
        discard = source[begin:end]
        self.assertIn("word->second.value = feeder_poison", discard)
        self.assertIn("observed_poison = word->second.value", discard)
        self.assertIn("observed_poison != feeder_poison", discard)
        self.assertIn("direct_index_words.erase(word)", discard)
        self.assertNotIn("maa->spd->setData", discard)
        self.assertNotIn("maa->spd->setFakeData", discard)
        self.assertNotIn("dataptr", discard)

    def test_runner_fails_closed_on_gem5_trace_counts(self):
        runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()
        self.assertIn("feeder_descriptor_discards=0", runner)
        self.assertIn("expected_descriptor_discards=16384", runner)
        self.assertIn("poisoned=1 poison=0xd15ca4d", runner)
        self.assertIn("feeder_predicate_discards", runner)
        self.assertIn("expected_partition_discards", runner)
        self.assertIn("invalid private index-feeder discard evidence", runner)
        self.assertIn("feeder_descriptor_discards", runner)
        self.assertIn("feeder_partition_discards", runner)


if __name__ == "__main__":
    unittest.main()
