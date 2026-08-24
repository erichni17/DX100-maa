"""Static and deterministic correctness gate for HashJoin's hybrid target."""

import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks/hashjoin/src/parallel_radix_join.cpp"
BUILD = ROOT / "benchmarks/hashjoin/compile_x86.sh"
RUNNER = ROOT / "benchmarks/hashjoin/run_hashjoin_hybrid_small.sh"
LOGICAL = 16 * 1024
THREADS = 4
RADIX_BITS_PER_PASS = 7
FANOUT = 1 << RADIX_BITS_PER_PASS
SMALL_PADDING_TUPLES = 3 * 64 // 8
FIRST_PADDING_TUPLES = SMALL_PADDING_TUPLES * (FANOUT + 1)


def frozen_relation(length: int) -> tuple[tuple[int, int], ...]:
    relation = []
    for ordinal in range(length):
        key = ((ordinal * 1103515245 + 12345) >> 9) & 0x7FFFFFFF
        if ordinal % 19 == 0:
            key = 23
        elif ordinal % 23 == 0:
            key = 23 + (1 << RADIX_BITS_PER_PASS)
        relation.append((key, ordinal ^ 0x5A5A))
    return tuple(relation)


def radix_index(key: int, shift: int) -> int:
    return (key >> shift) & (FANOUT - 1)


def legacy_histogram(
    relation: tuple[tuple[int, int], ...], shift: int
) -> list[int]:
    histogram = [0] * FANOUT
    for key, _ in relation:
        histogram[radix_index(key, shift)] += 1
    return histogram


def candidate_histogram(
    relation: tuple[tuple[int, int], ...], shift: int
) -> list[int]:
    histogram = [0] * FANOUT
    for begin in range(0, len(relation), LOGICAL):
        window = relation[begin : begin + LOGICAL]
        indices = [radix_index(key, shift) for key, _ in window]
        # A complete candidate window may be serviced in Row/Offset order.
        if len(window) == LOGICAL:
            indices.reverse()
        for index in indices:
            histogram[index] += 1
    return histogram


def thread_chunks(
    relation: tuple[tuple[int, int], ...]
) -> list[tuple[tuple[int, int], ...]]:
    per_thread = len(relation) // THREADS
    return [
        relation[tid * per_thread :]
        if tid == THREADS - 1
        else relation[tid * per_thread : (tid + 1) * per_thread]
        for tid in range(THREADS)
    ]


def threaded_padded_scatter(
    relation: tuple[tuple[int, int], ...], candidate: bool
) -> tuple[tuple[tuple[int, int] | None, ...], list[list[int]]]:
    chunks = thread_chunks(relation)
    histograms = [
        (candidate_histogram if candidate else legacy_histogram)(chunk, 0)
        for chunk in chunks
    ]
    cumulative = []
    for histogram in histograms:
        running = 0
        prefix = []
        for count in histogram:
            running += count
            prefix.append(running)
        cumulative.append(prefix)

    output_size = len(relation) + FANOUT * FIRST_PADDING_TUPLES
    scattered: list[tuple[int, int] | None] = [None] * output_size
    for tid, chunk in enumerate(chunks):
        destinations = []
        for bucket in range(FANOUT):
            destination = sum(
                cumulative[prior][bucket] for prior in range(tid)
            )
            if bucket:
                destination += sum(
                    cumulative[later][bucket - 1]
                    for later in range(tid, THREADS)
                )
            destination += bucket * FIRST_PADDING_TUPLES
            destinations.append(destination)
        page_starts = range(0, len(chunk), 4096) if candidate else (0,)
        for page_start in page_starts:
            page = (
                chunk[page_start : page_start + 4096] if candidate else chunk
            )
            for item in page:
                bucket = radix_index(item[0], 0)
                scattered[destinations[bucket]] = item
                destinations[bucket] += 1
    return tuple(scattered), histograms


def serial_shifted_padded_scatter(
    relation: tuple[tuple[int, int], ...], candidate: bool
) -> tuple[tuple[tuple[int, int] | None, ...], list[int]]:
    histogram = (candidate_histogram if candidate else legacy_histogram)(
        relation, RADIX_BITS_PER_PASS
    )
    scattered: list[tuple[int, int] | None] = [None] * (
        len(relation) + FANOUT * SMALL_PADDING_TUPLES
    )
    destinations = []
    offset = 0
    for bucket, count in enumerate(histogram):
        destinations.append(offset + bucket * SMALL_PADDING_TUPLES)
        offset += count
    page_starts = range(0, len(relation), 4096) if candidate else (0,)
    for page_start in page_starts:
        page = (
            relation[page_start : page_start + 4096] if candidate else relation
        )
        for item in page:
            bucket = radix_index(item[0], RADIX_BITS_PER_PASS)
            scattered[destinations[bucket]] = item
            destinations[bucket] += 1
    return tuple(scattered), histogram


def test_both_histogram_sites_and_real_padded_scatters_are_exact():
    relation = frozen_relation(THREADS * LOGICAL + 37)

    legacy_first, legacy_thread_histograms = threaded_padded_scatter(
        relation, candidate=False
    )
    candidate_first, candidate_thread_histograms = threaded_padded_scatter(
        relation, candidate=True
    )
    assert candidate_thread_histograms == legacy_thread_histograms
    assert candidate_first == legacy_first
    assert Counter(
        item for item in candidate_first if item is not None
    ) == Counter(relation)
    assert candidate_first.count(None) == FANOUT * FIRST_PADDING_TUPLES

    legacy_second, legacy_shifted_histogram = serial_shifted_padded_scatter(
        relation, candidate=False
    )
    (
        candidate_second,
        candidate_shifted_histogram,
    ) = serial_shifted_padded_scatter(relation, candidate=True)
    assert candidate_shifted_histogram == legacy_shifted_histogram
    assert candidate_second == legacy_second
    assert Counter(
        item for item in candidate_second if item is not None
    ) == Counter(relation)
    assert candidate_second.count(None) == FANOUT * SMALL_PADDING_TUPLES


def test_source_uses_compile_time_candidate_at_both_histogram_sites():
    source = SOURCE.read_text(encoding="utf-8")
    assert "getenv(" not in source
    assert source.count("maa_indirect_rmw_scalar_soa_jit<int32_t>") == 2
    assert "#ifdef HASHJOIN_HYBRID_SOA_JIT" in source
    assert "static_assert(TILE_SIZE == 16384" in source
    assert "HASHJOIN_HYBRID_PHYSICAL_ELEMENTS = 4096" in source
    assert "HASHJOIN_HYBRID_SOA_JIT requires 32-bit HashJoin keys" in source

    shifted = source[
        source.index("void radix_cluster_maa(") : source.index(
            "void radix_cluster_nopadding("
        )
    ]
    threaded = source[
        source.index("void parallel_radix_partition_maa(") : source.index(
            "typedef union"
        )
    ]
    for implementation in (shifted, threaded):
        assert "HASHJOIN_HYBRID_LOGICAL_ELEMENTS" in implementation
        assert (
            "args->hybrid_soa_indices[lane] = HASH_BIT_MODULO("
            in implementation
        )
        assert "maa_indirect_rmw_scalar_soa_jit<int32_t>" in implementation
    assert "rel[i + lane].key, MASK, R" in threaded
    assert "inRel->tuples[i + lane].key, M, R" in shifted
    assert "tmp[dst[idx]] = rel[i]" in threaded
    assert (
        "maa_indirect_store_vector<double>(tmp_double, tile2, tile4)"
        in threaded
    )
    assert (
        source.count("scatter_step = HASHJOIN_HYBRID_PHYSICAL_ELEMENTS") == 2
    )
    assert (
        source.count("maa_const<int>((i + physical_elements) * 2, reg1)") == 2
    )
    assert "hybrid_first_scatter_4k_actions" in source
    assert "hybrid_second_scatter_4k_actions" in source


def test_one_contiguous_backing_region_stays_within_region_limit():
    source = SOURCE.read_text(encoding="utf-8")
    assert (
        "static_cast<size_t>(nthreads) * HASHJOIN_HYBRID_LOGICAL_ELEMENTS"
        in source
    )
    assert (
        source.count("m5_add_mem_region(\n        hybrid_soa_indices_base,")
        == 1
    )
    assert "args[i].hybrid_soa_indices = hybrid_soa_indices_base +" in source
    assert source.count("free(hybrid_soa_indices_base);") == 1
    assert "free(args[i].hybrid_soa_indices)" not in source
    assert "HASHJOIN_HYBRID_MAX_REGION_ID = 31" in source
    assert (
        "assert(hybrid_max_region_id <= HASHJOIN_HYBRID_MAX_REGION_ID)"
        in source
    )

    first_region_id = 7
    base_regions = 4
    candidate_backing_regions = 1
    per_thread_regions = 5
    maximum = (
        first_region_id
        + base_regions
        + candidate_backing_regions
        + THREADS * per_thread_regions
        - 1
    )
    assert maximum == 31


def test_build_and_runner_are_candidate_only_and_close_mechanism():
    source = SOURCE.read_text(encoding="utf-8")
    build = BUILD.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    candidate_line = next(
        line for line in build.splitlines() if "hj_maa_16K_hybrid" in line
    )
    legacy_line = next(
        line for line in build.splitlines() if "-o bin/x86/hj_maa_16K " in line
    )
    assert "-DHASHJOIN_HYBRID_SOA_JIT" in candidate_line
    assert "-DHASHJOIN_HYBRID_SOA_JIT" not in legacy_line
    assert "hj_base" not in runner
    assert "for kernel in PRO PRH; do" in runner
    assert 'timeout "$' not in runner
    assert "HASHJOIN_HYBRID_SOA_JIT=" not in runner
    assert "GEM5_BINARY:?set GEM5_BINARY" in runner
    assert "--cpu-type=AtomicSimpleCPU" in runner
    assert "--max-checkpoints=1" in runner
    assert "--cpu-type=X86O3CPU -r 1" in runner
    assert '--checkpoint-dir="$checkpoint"' in runner
    assert "MODE=${HASHJOIN_HYBRID_MODE:-small}" in runner
    assert "R_SIZE=65536" in runner and "S_SIZE=65536" in runner
    assert "R_SIZE=2000000" in runner and "S_SIZE=2000000" in runner
    assert "EXPECTED_RESULT=$S_SIZE" in runner
    assert "HASHJOIN_HYBRID_RESULT result=$EXPECTED_RESULT" in runner
    assert 'printf("HASHJOIN_HYBRID_RESULT result=%ld\\n"' in source
    assert "fflush(stdout);" in source
    for geometry in (
        "--mem-channels=2",
        "--maa_num_initial_row_table_slices=32",
        "--maa_num_indirect_units_per_maa=4",
        "--maa_num_tile_elements=16384",
        "--maa_physical_tile_elements=4096",
    ):
        assert geometry in runner
    for closure in (
        "enabled",
        "routed -gt 0",
        "EXPECTED_FIRST_SCATTER_4K_ACTIONS=32",
        "first_scatter_4k_actions -eq $EXPECTED_FIRST_SCATTER_4K_ACTIONS",
        "second_scatter_4k_actions -gt 0",
        "max_region_id",
        "IND_SoaJitInstructions",
        "IND_SoaJitTerminalCompletions",
        "IND_SoaJitSelected",
        "IND_SoaJitAReadResponses",
        "IND_SoaJitAWriteResponses",
        "event=soa_jit_complete",
        "second_eligible -gt 0",
        "EXPECTED_FIRST_SCATTER_4K_ACTIONS",
        "result_sha256.txt",
        "source_fingerprint",
    ):
        assert closure in runner


def test_full_runner_contract_is_pinned_and_fails_closed():
    runner = RUNNER.read_text(encoding="utf-8")
    assert (
        "FULL_GEM5_BINARY=/data1/nier/dx100-binaries/gem5-2d02fa40568d3ed374258d717f15cad3afeca62343fc1ccaa1640215a8586152.opt"
        in runner
    )
    assert (
        "FULL_GEM5_SHA256=2d02fa40568d3ed374258d717f15cad3afeca62343fc1ccaa1640215a8586152"
        in runner
    )
    assert "full mode requires an immutable clean source worktree" in runner
    assert "source changed while the HashJoin gate was running" in runner
    assert "full mode requires gem5 binary" in runner
    assert "native_rerun=0" in runner and "wall_timeout=none" in runner
    assert "for kernel in PRO PRH; do" in runner
    assert "EXPECTED_FIRST_SCATTER_4K_ACTIONS=984" in runner


def test_pro_and_prh_probe_and_collision_functions_remain_legacy():
    source = SOURCE.read_text(encoding="utf-8")
    assert (
        "join_init_run(relR, relS, bucket_chaining_join, nthreads)" in source
    )
    assert "join_init_run(relR, relS, histogram_join, nthreads)" in source
    bucket_join = source[
        source.index("bucket_chaining_join(") : source.index(
            "/** computes and returns the histogram size"
        )
    ]
    histogram_join = source[
        source.index("histogram_join(") : source.index(
            "/** software prefetching"
        )
    ]
    assert "hybrid" not in bucket_join.lower()
    assert "soa_jit" not in histogram_join


class HashJoinHybridSoaJitCandidateGate(unittest.TestCase):
    def test_correctness_model(self):
        test_both_histogram_sites_and_real_padded_scatters_are_exact()

    def test_source_contract(self):
        test_source_uses_compile_time_candidate_at_both_histogram_sites()

    def test_region_contract(self):
        test_one_contiguous_backing_region_stays_within_region_limit()

    def test_runner_contract(self):
        test_build_and_runner_are_candidate_only_and_close_mechanism()

    def test_full_runner_contract(self):
        test_full_runner_contract_is_pinned_and_fails_closed()

    def test_probe_contract(self):
        test_pro_and_prh_probe_and_collision_functions_remain_legacy()
