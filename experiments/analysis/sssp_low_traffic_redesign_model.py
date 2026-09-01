#!/usr/bin/env python3
"""Host-only cost and correctness model for the SSSP low-traffic redesign.

This model does not predict gem5 time.  It freezes the accepted matched-micro
counters, derives traffic/storage quantities from them, and provides small
semantic models for alternatives A--D in the accompanying design report.
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import (
    asdict,
    dataclass,
)

CACHE_LINE_BYTES = 64
WORD_BYTES = 4
RECORD_BYTES = 8
PHYSICAL_WORDS = 4096
LOGICAL_WORDS = 16384
WINDOWS = 4
SELECTED_WORDS = WINDOWS * LOGICAL_WORDS


@dataclass(frozen=True)
class Evidence:
    native4_sim_ticks: int = 672_489_890
    native16_sim_ticks: int = 618_231_027
    hybrid_sim_ticks: int = 8_597_114_973
    native4_maa_cycles: int = 2_148_530
    native16_maa_cycles: int = 1_975_179
    hybrid_maa_cycles: int = 27_466_821
    native4_idle_cycles: int = 1_826_561
    hybrid_idle_cycles: int = 26_672_476
    native4_busy_cycles: int = 321_969
    hybrid_busy_cycles: int = 794_345
    publisher_lines: int = 8_192
    publisher_terminals: int = 32
    publisher_credit_stalls: int = 7_936
    publisher_overlap_issues: int = 88
    index_read_lines: int = 4_096
    predicate_read_lines: int = 4_096
    value_read_lines: int = 10_176
    a_read_lines: int = 16_385
    a_write_lines: int = 16_385
    old_result_write_lines: int = 15_633
    old_result_pressure_writes: int = 15_606
    old_result_stalls: int = 206_560
    context_stalls: int = 404_039
    range_instructions: int = 16
    range_compute_cycles: int = 4_096
    range_spd_read_cycles: int = 768
    range_spd_write_cycles: int = 4_096


EVIDENCE = Evidence()


@dataclass(frozen=True)
class Lane:
    owner: int
    ordinal: int
    destination: int
    candidate: int


def apply_atomic_min(initial: tuple[int, ...], lanes: tuple[Lane, ...]):
    """Apply linearizable MINs in the supplied order and return successes."""
    distance = list(initial)
    successes = []
    for lane in lanes:
        old = distance[lane.destination]
        if lane.candidate < old:
            distance[lane.destination] = lane.candidate
            successes.append(
                (lane.owner, lane.ordinal, lane.destination, lane.candidate)
            )
    return tuple(distance), tuple(successes)


def option_a_inline_handoff(initial: tuple[int, ...], lanes: tuple[Lane, ...]):
    """Exact descriptor/value handoff; ordering may change but no lane may."""
    return apply_atomic_min(initial, lanes)[0]


def option_a_overwritten_page(
    initial: tuple[int, ...], pages: tuple[tuple[Lane, ...], ...]
):
    """Illegal four-page handoff that retains only the last 4K value page."""
    return apply_atomic_min(initial, pages[-1])[0]


def option_c_snapshot_reconstruction(
    initial: tuple[int, ...], lanes: tuple[Lane, ...]
):
    """Barriered pre-wave snapshot plus post-wave final-distance scan."""
    final, _ = apply_atomic_min(initial, lanes)
    pushes = tuple(
        (destination, value)
        for destination, value in enumerate(final)
        if value < initial[destination]
    )
    return final, pushes


def option_d_coupled_retirement(
    initial: tuple[int, ...], lanes: tuple[Lane, ...]
):
    """Linearizable MIN with an output event coupled to every strict change."""
    final, successes = apply_atomic_min(initial, lanes)
    retirements = tuple(
        (destination, value) for _, _, destination, value in successes
    )
    return final, retirements


def unconditional_same_bin_trace(iterations: int = 8):
    """Return the repeating option-B frontier for a positive same-bin cycle."""
    # 0 --1--> 1 --1--> 0, delta=4, already at fixed-point distances 0/1.
    distance = [0, 1]
    frontier = [0, 1]
    trace = []
    graph = (((1, 1),), ((0, 1),))
    for _ in range(iterations):
        trace.append(tuple(frontier))
        next_frontier = []
        for source in frontier:
            # The production active test is dist[u] >= delta * bin.  Both
            # vertices remain active in bin zero forever.
            for destination, weight in graph[source]:
                candidate = distance[source] + weight
                distance[destination] = min(distance[destination], candidate)
                next_frontier.append(destination)  # option B: unconditional
        frontier = next_frontier
    return tuple(distance), tuple(trace)


def range_loop_pages(
    bounds: tuple[tuple[int, int], ...], page_words: int = PHYSICAL_WORDS
):
    """Model RangeLoop's persistent (last_i,last_j) cursor across pages."""
    pages = []
    source = 0
    edge = -1
    while source < len(bounds):
        page = []
        while len(page) < page_words and source < len(bounds):
            lower, upper = bounds[source]
            if edge == -1:
                edge = lower
            while edge < upper and len(page) < page_words:
                page.append((source, edge))
                edge += 1
            if edge >= upper:
                source += 1
                edge = -1
        pages.append(tuple(page))
    return tuple(pages), (source, edge)


def derived_evidence(evidence: Evidence = EVIDENCE):
    total_excess = evidence.hybrid_maa_cycles - evidence.native4_maa_cycles
    idle_excess = evidence.hybrid_idle_cycles - evidence.native4_idle_cycles
    ideal_old_lines = SELECTED_WORDS * WORD_BYTES // CACHE_LINE_BYTES
    ideal_value_lines = ideal_old_lines
    explicit_lines = (
        evidence.publisher_lines
        + evidence.index_read_lines
        + evidence.predicate_read_lines
        + evidence.value_read_lines
        + evidence.a_read_lines
        + evidence.a_write_lines
        + evidence.old_result_write_lines
    )
    return {
        "slowdown_vs_native4": (
            evidence.hybrid_sim_ticks / evidence.native4_sim_ticks
        ),
        "native16_speedup_vs_native4": (
            evidence.native4_sim_ticks / evidence.native16_sim_ticks
        ),
        "hybrid_speedup_vs_native4": (
            evidence.native4_sim_ticks / evidence.hybrid_sim_ticks
        ),
        "maa_cycle_ratio_vs_native4": (
            evidence.hybrid_maa_cycles / evidence.native4_maa_cycles
        ),
        "excess_maa_cycles": total_excess,
        "excess_idle_cycles": idle_excess,
        "excess_cycles_explained_by_idle_fraction": idle_excess / total_excess,
        "publisher_transport_bytes": (
            evidence.publisher_lines * CACHE_LINE_BYTES
        ),
        "old_result_semantic_bytes": SELECTED_WORDS * WORD_BYTES,
        "old_result_transport_bytes": (
            evidence.old_result_write_lines * CACHE_LINE_BYTES
        ),
        "old_result_line_amplification": (
            evidence.old_result_write_lines / ideal_old_lines
        ),
        "value_read_line_amplification": (
            evidence.value_read_lines / ideal_value_lines
        ),
        "explicit_accounted_request_lines": explicit_lines,
        "explicit_accounted_transport_bytes": (
            explicit_lines * CACHE_LINE_BYTES
        ),
    }


def option_costs(evidence: Evidence = EVIDENCE):
    # A paired page-fed ingress can place FP32 bits in OffsetTableEntry.pass.
    # That field is already present in the current 16-byte software entry and
    # is unused by this mutually-exclusive page-fed mode.
    inline_control_bytes = 16
    retirement_context_mask_bytes = 8 * 2  # eight A contexts, 16 words/line
    retirement_credit_bytes = 8 * CACHE_LINE_BYTES
    retirement_packer_bytes = CACHE_LINE_BYTES
    retirement_control_bytes = 16
    incremental_sram = (
        inline_control_bytes
        + retirement_context_mask_bytes
        + retirement_credit_bytes
        + retirement_packer_bytes
        + retirement_control_bytes
    )
    retirement_lines = SELECTED_WORDS * RECORD_BYTES // CACHE_LINE_BYTES
    current_serial_write_lines = (
        evidence.publisher_lines + evidence.old_result_write_lines
    )
    combined_request_lines = (
        evidence.a_read_lines + evidence.a_write_lines + retirement_lines
    )
    return {
        "A_direct_inline_handoff": {
            "correctness": "conditional_pass",
            "incremental_sram_bytes_per_unit": inline_control_bytes,
            "live_inline_operand_bytes_in_existing_offset_aux": (
                LOGICAL_WORDS * WORD_BYTES
            ),
            "incremental_external_backing_bytes": 0,
            "coherent_publication_lines": 0,
            "coherent_index_read_lines": 0,
            "coherent_value_read_lines": 0,
            "spd_word_reads": 2 * SELECTED_WORDS,
            "row_offset_insertions": SELECTED_WORDS,
            "preserves_4k_spd": True,
            "preserves_only_4k_total_payload_without_aux_reuse": False,
        },
        "B_unconditional_push": {
            "correctness": "final_distance_only_progress_refuted",
            "incremental_sram_bytes_per_unit": 0,
            "incremental_external_backing_bytes": 0,
            "coherent_publication_lines": evidence.publisher_lines,
            "old_result_write_lines": 0,
            "worst_case_micro_frontier_words": SELECTED_WORDS,
            "preserves_4k_spd": True,
        },
        "C_post_update_snapshot_recompute": {
            "correctness": "pass_with_pre_wave_snapshot_and_barriers",
            "incremental_sram_bytes_per_unit": 0,
            "incremental_external_backing_bytes": 69_633 * WORD_BYTES,
            "snapshot_write_lines": 4_353,
            "relevant_snapshot_read_lines": 4_352,
            "graph_replay_read_lines": SELECTED_WORDS * 8 // 64,
            "post_distance_read_lines": evidence.a_read_lines,
            "old_result_write_lines": 0,
            "preserves_4k_spd": True,
            "fixed_geometry_backing": False,
        },
        "D_fused_min_retirement": {
            "correctness": "pass_if_min_linearizable_and_ack_coupled",
            "incremental_sram_bytes_per_unit": (
                retirement_context_mask_bytes
                + retirement_credit_bytes
                + retirement_packer_bytes
                + retirement_control_bytes
            ),
            "max_live_external_ring_bytes_per_unit": (
                PHYSICAL_WORDS * RECORD_BYTES
            ),
            "micro_retirement_records": SELECTED_WORDS,
            "micro_retirement_write_lines": retirement_lines,
            "old_result_write_lines": 0,
            "preserves_4k_spd": True,
        },
        "recommended_A_plus_D": {
            "incremental_sram_bytes_per_unit": incremental_sram,
            "coherent_index_value_publication_lines": 0,
            "coherent_index_predicate_value_read_lines": 0,
            "old_result_write_lines": 0,
            "a_read_write_lines": (
                evidence.a_read_lines + evidence.a_write_lines
            ),
            "retirement_write_lines": retirement_lines,
            "explicit_request_lines": combined_request_lines,
            "explicit_request_line_reduction_fraction": (
                1
                - combined_request_lines
                / derived_evidence(evidence)[
                    "explicit_accounted_request_lines"
                ]
            ),
            "serialized_write_line_reduction_fraction": (
                1 - retirement_lines / current_serial_write_lines
            ),
            "preserves_4k_spd": True,
            "row_offset_incremental_bytes": 0,
        },
    }


def exhaustive_correctness_summary():
    initial = (10, 9)
    destinations = (0, 0, 1, 0)
    candidate_values = (4, 7, 9)
    cases = 0
    schedules = 0
    stale_retirements = 0
    for candidates in itertools.product(candidate_values, repeat=4):
        lanes = tuple(
            Lane(index % 2, index, destination, candidate)
            for index, (destination, candidate) in enumerate(
                zip(destinations, candidates)
            )
        )
        oracle = tuple(
            min(
                initial[destination],
                *(
                    lane.candidate
                    for lane in lanes
                    if lane.destination == destination
                ),
            )
            for destination in range(len(initial))
        )
        for order in itertools.permutations(lanes):
            schedules += 1
            a_final = option_a_inline_handoff(initial, order)
            c_final, c_pushes = option_c_snapshot_reconstruction(
                initial, order
            )
            d_final, d_retirements = option_d_coupled_retirement(
                initial, order
            )
            if a_final != oracle or c_final != oracle or d_final != oracle:
                raise AssertionError("atomic MIN final distance changed")
            for destination, final in enumerate(oracle):
                if final >= initial[destination]:
                    continue
                if (destination, final) not in c_pushes:
                    raise AssertionError("snapshot lost a strict decrease")
                if (destination, final) not in d_retirements:
                    raise AssertionError("retirement lost the final decrease")
            stale_retirements += sum(
                value > oracle[destination]
                for destination, value in d_retirements
            )
        cases += 1
    _, b_trace = unconditional_same_bin_trace()
    return {
        "candidate_assignments": cases,
        "linearization_schedules": schedules,
        "stale_but_safe_D_retirements": stale_retirements,
        "B_trace_iterations": len(b_trace),
        "B_trace_unique_frontiers": len(set(b_trace)),
        "A_C_D_final_distance_and_progress": "PASS",
        "B_progress": "FAIL",
    }


def report():
    return {
        "schema": 1,
        "scope": "host_cost_and_correctness_only",
        "evidence": asdict(EVIDENCE),
        "derived": derived_evidence(),
        "options": option_costs(),
        "correctness_search": exhaustive_correctness_summary(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(report(), indent=2 if args.pretty else None, sort_keys=True)
    )


if __name__ == "__main__":
    main()
