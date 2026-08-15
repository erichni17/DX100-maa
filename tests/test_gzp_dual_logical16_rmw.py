"""Focused source contracts for the default-off GZP split-2K publisher."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_split_treatment_is_default_off_and_preserves_masked_dual_rmw() -> (
    None
):
    text = source("benchmarks/UME/gradzatp.cpp")
    assert (
        "static GzpRmwTreatment gzp_rmw_treatment = GzpRmwTreatment::Legacy4K"
        in text
    )
    assert 'treatment == "dual_logical16_split2k"' in text
    assert 'return "dual_logical16_split2k_soa_jit"' in text
    assert "soa_dual_logical16_any_full_window" in text
    assert (
        text.count("maa_indirect_rmw_vector_soa_jit_masked_indices<DATATYPE>(")
        == 2
    )
    assert "corner_volume.data() + c" in text
    assert "soa_gradient_values[omp_thread_id]" in text
    assert "get_cacheable_tile_pointer<DATATYPE>(tile2)" not in text
    assert "std::atomic_thread_fence" not in text


def test_split_producer_is_two_2k_owners_of_one_4k_payload() -> None:
    benchmark = source("benchmarks/UME/gradzatp.cpp")
    api = source("benchmarks/API/MAA_gem5.hpp")
    maa_hh = source("src/mem/MAA/MAA.hh")
    maa_cc = source("src/mem/MAA/MAA.cc")
    spd_cc = source("src/mem/MAA/SPD.cc")

    assert "for (uint32_t half = 0; half < 2; ++half)" in benchmark
    assert "maa_alu_vector_split_2k<DATATYPE>" in benchmark
    assert "maa_publish_spd_half_logical16_response_bearing" in benchmark
    assert "0x80000000U | subpage" in benchmark
    assert "producer_staging_elements=" in benchmark
    assert "producer_owner_regions=" in benchmark
    assert "producer_owner_region_elements=" in benchmark
    assert "hidden_logical16_payload_bytes=0" in benchmark
    assert "cpu_untimed_copy_bytes=0" in benchmark

    assert "logical16_backing + subpage * 2048" in api
    assert "split producer ALU is FP32-only" in api
    assert "two halves target" in api
    assert (
        "static constexpr std::size_t Split2KPublisherOwnerSlots = 2" in maa_hh
    )
    assert "static_assert(sizeof(Split2KPublisherSourceOwner) == 4" in maa_hh
    assert "physical_tile_elements != 4096 || elements != 2048" in maa_cc
    assert "event=split2k_owner_reserve" in maa_cc
    assert "event=split2k_owner_release" in maa_cc
    assert "write_resp=1" in maa_cc
    assert "setRangeNotReady" in spd_cc
    assert "first_element + elements >" in spd_cc


def test_publisher_captures_only_the_owned_half_and_releases_at_writeresp() -> (
    None
):
    stream = source("src/mem/MAA/StreamAccess.cc")
    publisher = source("src/mem/MAA/ResponseBearingSpdPublisher.hh")

    assert "my_publish_source_elements = my_publish_split_2k" in stream
    assert "? Split2KElements : maa->physical_tile_elements" in stream
    assert "my_publish_source_first_element +" in stream
    assert "response_publisher.begin(" in stream
    assert "my_publish_source_elements);" in stream
    assert "event=spd_publish_terminal schema=2" in stream
    assert "source_elements=%u" in stream
    assert "releaseSplit2KPublisherSource(" in source("src/mem/MAA/MAA.cc")

    assert "std::size_t elementsPerPage = PageElements" in publisher
    assert "elementsPerPage > PageElements" in publisher
    assert "activePageBytes = pageBytes" in publisher
    assert (
        "activeLinesPerPage = static_cast<uint16_t>(linesPerPage)" in publisher
    )
    assert "std::array<std::byte, LineBytes>" in publisher
    assert "std::array<Payload, Credits>" in publisher


def test_two_replica_runner_holds_the_optimized_hybrid_constant() -> None:
    text = source("experiments/scripts/run_gzp_dual_logical16_one_window.py")
    for required in (
        "REPETITIONS = 2",
        '"control_dual_logical16", "token_stream_ld dual_logical16"',
        '"treatment_split2k", "token_stream_ld dual_logical16_split2k"',
        '"--maa_soa_jit_active_contexts=32"',
        '"--maa_soa_jit_active_value_owners=64"',
        '"--maa_soa_jit_pre_a_value_lookahead"',
        '"soa_jit_active_contexts=32"',
        '"soa_jit_active_value_owners=64"',
        '"soa_jit_pre_a_value_lookahead=true"',
        '"--debug-flags=MAAVirtualTrace,MAATrace"',
        '"shared_checkpoint": True',
        '"masked_indices": True',
        "verify_split_dependency_timeline",
        "split2k_alu_finish",
        "split2k_owner_release",
        "strict_overlap_proven",
        'decision = "ACCEPT" if tick_delta < 0 else "REJECT"',
        'all(summary["decision"] == "ACCEPT" for summary in comparisons)',
        "matrix.tree_identity(checkpoint) != checkpoint_identity",
        '"host_time_metric_authorized": False',
    ):
        assert required in text
