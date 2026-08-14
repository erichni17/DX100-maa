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


def test_publication_backings_are_prefaulted_before_checkpoint() -> None:
    source = read("benchmarks/UME/gradzatp.cpp")
    checkpoint = source.index('cout << "Starting checkpoint"')
    predicate_touch = source.index("std::fill(soa_predicates[core]")
    gradient_touch = source.index("std::fill(soa_gradient_values[core]")
    assert predicate_touch < gradient_touch < checkpoint
    assert "first touch were left to alternating" in source


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
    assert "n=65536" in runner
    assert "expected_publications=32" in runner
    assert "expected_lines=8192" in runner
    assert "STR_PublishIssues') -eq $expected_lines" in runner
    assert "STR_PublishWriteResponses') -eq $expected_lines" in runner
    assert "STR_PublishTerminals') -eq $expected_publications" in runner
    assert "speedup_claim=0" in runner
    assert "published_predicates=$n" in runner
