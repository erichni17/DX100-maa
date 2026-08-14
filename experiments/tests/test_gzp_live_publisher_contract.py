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
    helper = source[
        source.index("static void first_touch_soa_publication_buffers()") :
        source.index("enum class GzpRmwTreatment")
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


def test_runner_is_uncapped_and_fails_closed() -> None:
    runner = read("experiments/scripts/run_gzp_live_publisher_correctness.sh")
    assert "timeout" not in runner
    assert "STR_PublishIssues') -eq 2048" in runner
    assert "STR_PublishWriteResponses') -eq 2048" in runner
    assert "STR_PublishTerminals') -eq 8" in runner
    assert "speedup_claim=0" in runner
    assert "published_predicates=16384" in runner
