#!/usr/bin/env python3
"""Focused static contract for the production GZP publisher slice."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_gzp_has_no_host_result_staging_loop() -> None:
    source = read("benchmarks/UME/gradzatp.cpp")
    block = source[source.index("if (soa_both_full_window) {") :]
    block = block[: block.index("} else {")]
    assert block.count("maa_publish_spd_page_logical16_response_bearing") == 2
    assert "get_cacheable_tile_pointer" not in block
    assert "std::memcpy" not in block
    assert "for (int i = 0; i < page_size; ++i)" not in block
    assert block.count("wait_ready(soa_") == 2


def test_gzp_first_touches_publication_spans_before_checkpoint() -> None:
    source = read("benchmarks/UME/gradzatp.cpp")
    assert "alignas(4096) static uint32_t soa_predicates" in source
    assert "alignas(4096) static DATATYPE soa_gradient_values" in source
    helper = source[
        source.index(
            "static void first_touch_soa_publication_buffers()"
        ) : source.index("enum class GzpRmwTreatment")
    ]
    assert "volatile uint32_t *predicates" in helper
    assert "volatile DATATYPE *values" in helper
    assert helper.count("PageBytes / sizeof") == 2
    main_tail = source[source.index("int main(int argc") :]
    assert main_tail.index("first_touch_soa_publication_buffers();") < (
        main_tail.index("m5_checkpoint(0, 0);")
    )


def test_publisher_accounting_includes_overlap() -> None:
    header = read("src/mem/MAA/MAA.hh")
    implementation = read("src/mem/MAA/StreamAccess.cc")
    for counter in (
        "STR_PublishIssues",
        "STR_PublishAccepts",
        "STR_PublishRetries",
        "STR_PublishWriteResponses",
        "STR_PublishCreditStalls",
        "STR_PublishOverlapIssues",
    ):
        assert counter in header
        assert counter in implementation
    assert "overlap=%d" in implementation


def test_publisher_write_enters_coherence_through_cache() -> None:
    implementation = read("src/mem/MAA/StreamAccess.cc")
    port = read("src/mem/MAA/Port.cc")
    packet = implementation[
        implementation.index(
            "makeResponseBearingPublishPacket("
        ) : implementation.index("responseBearingPublishState(")
    ]
    assert "MemCmd::WriteReq" in packet
    assert "dataStatic" in packet
    assert "Request::UNCACHEABLE" not in packet

    issue_begin = implementation.index(
        "StreamAccessUnit::captureAndIssueResponseBearingLine()"
    )
    issue = implementation[
        issue_begin : implementation.index(
            "StreamAccessUnit::makeResponseBearingPublishPacket(",
            issue_begin,
        )
    ]
    normalized_issue = " ".join(issue.split())
    assert (
        "maa->sendPacket(FuncUnitType::STREAM, my_stream_id, packet, "
        "my_SPD_read_finish_tick, true, true);"
    ) in normalized_issue

    for queue, next_queue in (
        (
            "my_outstanding_stream_cache_write_pkts",
            "my_outstanding_stream_cache_read_pkts",
        ),
        (
            "my_outstanding_stream_mem_write_pkts",
            "my_outstanding_stream_mem_read_pkts",
        ),
    ):
        begin = port.index(f"for (auto it = {queue}")
        block = port[begin : port.index(f"for (auto it = {next_queue}", begin)]
        normalized_block = " ".join(block.split())
        assert (
            "it->virtualRetirement ? sendPacketRetirementCache(it->packet) "
            ": sendPacketCache(it->packet)"
        ) in normalized_block
        assert "my_outstanding_pkt_map[paddr].sent = true" in block
        assert "responseBearingPublishPacketRetried" in block
        assert "responseBearingPublishPacketAccepted" in block


def test_publisher_packet_flags_match_gem5_snoop_contract() -> None:
    packet = read("src/mem/packet.cc")

    def attributes(command: str) -> set[str]:
        marker = f'"{command}"'
        end = packet.index(marker)
        begin = packet.rindex("{{", 0, end) + 2
        return {
            attribute.strip()
            for attribute in packet[begin : packet.index("}", begin)].split(
                ","
            )
            if attribute.strip()
        }

    write = attributes("WriteReq")
    promoted_write = attributes("WriteLineReq")
    read_exclusive = attributes("ReadExReq")
    writeback = attributes("WritebackDirty")
    assert {
        "IsWrite",
        "NeedsWritable",
        "IsRequest",
        "NeedsResponse",
        "HasData",
    } <= write
    assert "IsInvalidate" not in write
    assert "FromCache" not in write
    assert promoted_write == write
    assert {
        "NeedsWritable",
        "IsInvalidate",
        "NeedsResponse",
        "FromCache",
    } <= read_exclusive
    assert {
        "IsWrite",
        "IsRequest",
        "IsEviction",
        "HasData",
        "FromCache",
    } <= writeback
    assert "NeedsResponse" not in writeback

    # This is the invariant enforced by SnoopFilter::lookupSnoop for an
    # ordinary cacheable request. Direct WriteReq injection fails it; the
    # coherent cache converts its miss to an invalidating ownership request.
    assert (("IsInvalidate" in write) == ("NeedsWritable" in write)) is False
    assert ("IsInvalidate" in read_exclusive) == (
        "NeedsWritable" in read_exclusive
    )


def test_runner_is_uncapped_and_fails_closed() -> None:
    runner = read("experiments/scripts/run_gzp_live_publisher_correctness.sh")
    assert "timeout" not in runner
    assert "STR_PublishIssues') -eq 2048" in runner
    assert "STR_PublishWriteResponses') -eq 2048" in runner
    assert "STR_PublishTerminals') -eq 8" in runner
    assert "speedup_claim=0" in runner
    assert "published_predicates=16384" in runner
    assert "--maa_num_initial_row_table_slices=16" in runner
    assert "--mem-channels=2" in runner
