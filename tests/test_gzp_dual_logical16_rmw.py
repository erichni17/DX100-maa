#!/usr/bin/env python3
"""Focused contracts for the default-off GZP dual-logical16 arm."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_arm_is_default_off_and_uses_two_masked_logical_rmws() -> None:
    text = source("benchmarks/UME/gradzatp.cpp")
    assert (
        "static GzpRmwTreatment gzp_rmw_treatment =\n    GzpRmwTreatment::Legacy4K"
        not in text
    )
    assert (
        "static GzpRmwTreatment gzp_rmw_treatment = GzpRmwTreatment::Legacy4K"
        in text
    )
    assert 'treatment == "dual_logical16"' in text
    assert 'return "dual_logical16_soa_jit"' in text
    block = text[text.index("if (soa_volume_only_full_window ||") :]
    block = block[: block.index("#else\n            maa_const<int>(c, reg0);")]
    assert (
        block.count("maa_indirect_rmw_vector_soa_jit_masked_indices<DATATYPE>")
        == 2
    )
    assert "corner_volume.data() + c" in block
    assert "soa_gradient_values[omp_thread_id]" in block
    assert (
        "soa_predicates[omp_thread_id], reg0" in block
    )  # legacy correctness arm


def test_dual_arm_publishes_only_gradient_and_fences_source_reuse() -> None:
    text = source("benchmarks/UME/gradzatp.cpp")
    publish = text[text.index("if (soa_both_full_window ||") :]
    publish = publish[
        : publish.index(
            "} else {\n                    maa_indirect_rmw_vector"
        )
    ]
    assert "if (soa_both_full_window)" in publish
    assert "soa_predicates[omp_thread_id]" in publish
    assert "soa_gradient_values[omp_thread_id]" in publish
    assert (
        "wait_ready(soa_gradient_completion_tiles[omp_thread_id])" in publish
    )
    assert "get_cacheable_tile_pointer<DATATYPE>(tile2)" not in text
    assert "std::atomic_thread_fence" not in text
    registration = text[text.index("Existing arms retain their original") :]
    registration = registration[
        : registration.index('std::cout << "ROI Begin"')
    ]
    dual = registration[
        registration.index("DualLogical16SoaJit") : registration.index(
            "} else {"
        )
    ]
    assert "soa_gradient_values" in dual
    assert "soa_predicates" not in dual
    legacy = registration[registration.index("} else {") :]
    assert "soa_predicates" in legacy
    assert "corner_predicate_soa" in legacy


def test_terminal_has_exact_payload_and_byte_accounting() -> None:
    text = source("benchmarks/UME/gradzatp.cpp")
    for required in (
        "dual_logical16_windows=",
        "published_gradient_bytes=",
        "producer_staging_elements=",
        "producer_staging_bytes=",
        "publisher_credit_payload_bytes=",
        "coherent_gradient_backing_elements=",
        "coherent_gradient_backing_bytes=",
        "hidden_logical16_payload_bytes=0",
        "cpu_untimed_copy_bytes=0",
        "response_bearing_gradient_only",
    ):
        assert required in text


def test_one_window_runner_is_shared_checkpoint_and_fail_closed() -> None:
    text = source("experiments/scripts/run_gzp_dual_logical16_one_window.py")
    for required in (
        "LOGICAL_ELEMENTS = 16384",
        '1_000_000: "11225737641199706160"',
        "EXPECTED_REFERENCE_ELEMENTS = {16384: 196384, 1_000_000: 1180000}",
        '"token_stream_ld volume_masked_index"',
        '"token_stream_ld dual_logical16"',
        '"shared_checkpoint": True',
        '"full_gzp_authorized": args.n == 1_000_000',
        '"--debug-flags=MAAVirtualTrace,MAATrace"',
        '"--active-contexts"',
        '"--active-value-owners"',
        '"--replicas"',
        '"--n"',
        '"--parallel-restores"',
        'f"--maa_soa_jit_active_contexts={args.active_contexts}"',
        'f"--maa_soa_jit_active_value_owners={args.active_value_owners}"',
        '"--maa_soa_jit_pre_a_value_lookahead"',
        "PUBLISH_LINES_PER_WINDOW = 4 * 256",
        '"STR_PublishWriteResponses"',
        '"IND_SoaJitAWriteResponses"',
        '"IND_SoaJitTerminalCompletions"',
        "expected_instructions = full_windows * instruction_multiplier",
        'int(ledger["full_selected"]) * instruction_multiplier',
        '"numInst_INDRMW"',
        '"cycles_INDRMW"',
        '"rmw_instructions": rmw_instructions',
        '"rmw_cycles": rmw_cycles',
        '"host_time_metric_authorized": False',
        '"decision": decision',
        'decision = "ACCEPT" if tick_delta < 0 else "REJECT"',
        "matrix.tree_identity(checkpoint) != checkpoint_identity",
        "for replica in range(1, args.replicas + 1)",
        '"deterministic_replicas": True',
        "ThreadPoolExecutor(max_workers=len(jobs))",
        '"--ro-bind"',
        "str(frozen_selector.resolve())",
        "str(selector.resolve())",
        'row = analyze_run(str(job["arm"]), run, args.n)',
    ):
        assert required in text
