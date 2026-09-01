#!/usr/bin/env python3
"""Exhaustive small-state model for conflict-tolerant GAPBS SSSP routing.

The model intentionally checks final-distance/progress properties separately
from exact frontier order and work.  It does not model gem5 timing.
"""

from __future__ import annotations

import heapq
import itertools
import pathlib
import unittest
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]
SSSP = ROOT / "benchmarks/gapbs/src/sssp.cc"
ADMISSION = ROOT / "benchmarks/gapbs/src/sssp_chunk_admission.hh"
INDIRECT = ROOT / "src/mem/MAA/IndirectAccess.cc"
INF = 1_000_000


@dataclass(frozen=True)
class Lane:
    owner: int
    page: int
    destination: int
    candidate: int


@dataclass(frozen=True)
class Window:
    owner: int
    pages: tuple[tuple[Lane, ...], ...]

    @property
    def lanes(self) -> tuple[Lane, ...]:
        return tuple(lane for page in self.pages for lane in page)


def schedules(lengths: tuple[int, ...]):
    """Yield every interleaving that preserves each window's event order."""
    positions = [0] * len(lengths)
    schedule = []

    def visit():
        if all(positions[i] == lengths[i] for i in range(len(lengths))):
            yield tuple(schedule)
            return
        for owner in range(len(lengths)):
            if positions[owner] == lengths[owner]:
                continue
            positions[owner] += 1
            schedule.append(owner)
            yield from visit()
            schedule.pop()
            positions[owner] -= 1

    yield from visit()


def execute(initial: tuple[int, ...], windows: tuple[Window, ...], schedule):
    """Run atomic MIN lanes plus each window's delayed reconstruction event."""
    dist = list(initial)
    positions = [0] * len(windows)
    old = [[None] * len(window.lanes) for window in windows]
    hybrid_pushes = []
    cas_pushes = []

    for window_index in schedule:
        window = windows[window_index]
        lanes = window.lanes
        position = positions[window_index]
        positions[window_index] += 1
        if position < len(lanes):
            lane = lanes[position]
            before = dist[lane.destination]
            old[window_index][position] = before
            if lane.candidate < before:
                dist[lane.destination] = lane.candidate
                cas_pushes.append(
                    (lane.owner, lane.destination, lane.candidate)
                )
            continue

        # This is RunSsspHybridWindow's reconstruction, including its
        # independent reverse/forward pass for every physical page.
        lane_base = 0
        for page in window.pages:
            page_finals = {}
            for page_offset in range(len(page) - 1, -1, -1):
                lane = page[page_offset]
                lane_old = old[window_index][lane_base + page_offset]
                page_finals.setdefault(
                    lane.destination, min(lane_old, lane.candidate)
                )
            for page_offset, lane in enumerate(page):
                lane_old = old[window_index][lane_base + page_offset]
                final = page_finals[lane.destination]
                if lane.candidate == final and lane_old > final:
                    hybrid_pushes.append((lane.owner, lane.destination, final))
            lane_base += len(page)

    return tuple(dist), tuple(hybrid_pushes), tuple(cas_pushes)


def make_windows(layout, candidates):
    cursor = iter(candidates)
    result = []
    for owner, pages in enumerate(layout):
        result.append(
            Window(
                owner,
                tuple(
                    tuple(
                        Lane(owner, page_index, destination, next(cursor))
                        for destination in page
                    )
                    for page_index, page in enumerate(pages)
                ),
            )
        )
    return tuple(result)


def wave_outcomes(initial, windows):
    lengths = tuple(len(window.lanes) + 1 for window in windows)
    for schedule in schedules(lengths):
        yield execute(initial, windows, schedule)


def dijkstra(graph, source):
    dist = [INF] * len(graph)
    dist[source] = 0
    queue = [(0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != dist[node]:
            continue
        for destination, weight in graph[node]:
            candidate = distance + weight
            if candidate < dist[destination]:
                dist[destination] = candidate
                heapq.heappush(queue, (candidate, destination))
    return tuple(dist)


def freeze_bins(bins):
    return tuple(
        tuple(
            (bin_index, tuple(nodes))
            for bin_index, nodes in sorted(owner.items())
        )
        for owner in bins
    )


def thaw_bins(frozen):
    return [
        {bin_index: list(nodes) for bin_index, nodes in owner}
        for owner in frozen
    ]


def explore_repeated_iterations():
    """Explore all RMW/reconstruction and local-bin merge orders to closure."""
    graph = (
        ((1, 4), (2, 7)),
        ((2, 1), (3, 5)),
        ((3, 1),),
        (),
    )
    delta = 4
    owners = 2
    initial_bins = [{0: [0]}, {}]
    initial = ((0, INF, INF, INF), freeze_bins(initial_bins))
    pending = [initial]
    seen = set()
    terminal_distances = set()
    transition_count = 0
    repeated_bin = False
    active_source_hazard = False
    cross_owner_alias = False
    multi_owner_merge = False

    while pending:
        state = pending.pop()
        if state in seen:
            continue
        seen.add(state)
        dist, frozen_bins = state
        bins = thaw_bins(frozen_bins)
        available = [
            bin_index
            for owner in bins
            for bin_index, nodes in owner.items()
            if nodes
        ]
        if not available:
            terminal_distances.add(dist)
            continue
        current_bin = min(available)
        blocks = [
            (owner, tuple(bins[owner].get(current_bin, ())))
            for owner in range(owners)
            if bins[owner].get(current_bin)
        ]
        if len(blocks) > 1:
            multi_owner_merge = True

        # fetch_and_add can concatenate complete per-owner local-bin blocks in
        # either owner order.  Explore each distinct merge order.
        frontier_orders = set()
        for block_order in itertools.permutations(blocks):
            frontier_orders.add(
                tuple(node for _, block in block_order for node in block)
            )

        base_bins = thaw_bins(frozen_bins)
        for owner, _ in blocks:
            del base_bins[owner][current_bin]

        for frontier in frontier_orders:
            snapshot = dist  # all-source snapshot precedes every write
            owner_lanes = [[] for _ in range(owners)]
            destinations_by_owner = [set() for _ in range(owners)]
            active_sources = {
                source
                for source in frontier
                if snapshot[source] >= current_bin * delta
            }
            for source in frontier:
                if source not in active_sources:
                    continue
                owner = source % owners
                for destination, weight in graph[source]:
                    owner_lanes[owner].append(
                        Lane(owner, 0, destination, snapshot[source] + weight)
                    )
                    destinations_by_owner[owner].add(destination)
                    if destination in active_sources:
                        active_source_hazard = True
            if set.intersection(*destinations_by_owner):
                cross_owner_alias = True

            windows = tuple(
                Window(owner, (tuple(lanes),))
                for owner, lanes in enumerate(owner_lanes)
                if lanes
            )
            outcomes = (
                wave_outcomes(dist, windows) if windows else ((dist, (), ()),)
            )
            for new_dist, pushes, _ in outcomes:
                transition_count += 1
                next_bins = [
                    {index: list(nodes) for index, nodes in owner.items()}
                    for owner in base_bins
                ]
                for owner, destination, pushed_distance in pushes:
                    destination_bin = pushed_distance // delta
                    next_bins[owner].setdefault(destination_bin, []).append(
                        destination
                    )
                    if destination_bin == current_bin:
                        repeated_bin = True
                pending.append((new_dist, freeze_bins(next_bins)))

    return {
        "oracle": dijkstra(graph, 0),
        "terminal_distances": terminal_distances,
        "states": len(seen),
        "transitions": transition_count,
        "repeated_bin": repeated_bin,
        "active_source_hazard": active_source_hazard,
        "cross_owner_alias": cross_owner_alias,
        "multi_owner_merge": multi_owner_merge,
    }


class SourceGroundingTest(unittest.TestCase):
    def test_model_is_bound_to_current_base_and_hybrid_shapes(self):
        source = SSSP.read_text()
        base = source[source.index("pvector<WeightT> DeltaStep(") :]
        self.assertLess(
            base.index("WeightT dist_u = dist[u]"), base.index("for (int j")
        )
        self.assertIn("compare_and_swap(dist[v], old_dist, new_dist)", base)
        self.assertIn("local_bins[dest_bin].push_back(v)", base)

        hybrid_start = source.index("RunSsspHybridWindow(")
        hybrid_end = source.index("#endif", hybrid_start)
        hybrid = source[hybrid_start:hybrid_end]
        for text in (
            "maa_indirect_rmw_vector_soa_jit_old_result(",
            "wait_ready(completion_tile);",
            "for (size_t page = 0; page < 4; ++page)",
            "for (size_t lane = end; lane-- > begin;)",
            "if (candidate == final_distance &&",
            "sssp_hybrid_old_results[tid][lane] > final_distance",
        ):
            self.assertIn(text, hybrid)

        admission = ADMISSION.read_text()
        self.assertIn("reasons[owner] |= ActiveSource", admission)
        self.assertEqual(admission.count("|= CrossOwner"), 2)
        self.assertIn("safeForConflictTolerantSnapshot", admission)
        self.assertIn("(reasons[owner] & Bounds) == 0", admission)

        snapshot = source[
            source.index("hybrid_snapshot_iteration =") : source.index(
                "if ((int)curr_frontier_tail <",
                source.index("hybrid_snapshot_iteration ="),
            )
        ]
        self.assertLess(
            snapshot.index("hybrid_source_snapshot[pos] ="),
            snapshot.index("fill(hybrid_active_sources.begin()"),
        )
        self.assertIn("hybrid_source_snapshot[pos]", snapshot)
        self.assertIn("source_distance", snapshot)

        indirect = INDIRECT.read_text()
        ordered_apply = indirect[
            indirect.index("const size_t apply_start") : indirect.index(
                "bool IndirectAccessUnit::applySoaJitValue"
            )
        ]
        self.assertIn("candidate.offset == context.nextOffset", ordered_apply)
        self.assertIn(
            "offset_table->consume_entry(context.nextOffset)", ordered_apply
        )
        capture = indirect.index("soa_jit_old_result_buffer.capture(")
        apply_value = indirect.index("#define APPLY_SOA_JIT", capture)
        self.assertLess(capture, apply_value)


class ConflictTolerantModelTest(unittest.TestCase):
    def test_exhaustive_cross_owner_pages_preserve_distances_not_exact_work(
        self,
    ):
        # Two owners, two physical pages each, and duplicate aliases within and
        # across pages/owners.  Values 4 and 7 exercise equal and decreasing
        # candidates against initial A=9 and B=7.
        layout = (
            ((0, 0), (0, 1)),
            ((0, 1), (0, 0)),
        )
        initial = (9, 7)
        frontier_signatures = set()
        exact_mismatches = 0
        stale_push_witnesses = 0
        cases = 0
        schedules_per_case = None

        for candidates in itertools.product((4, 7), repeat=8):
            windows = make_windows(layout, candidates)
            expected = list(initial)
            for lane in (lane for window in windows for lane in window.lanes):
                expected[lane.destination] = min(
                    expected[lane.destination], lane.candidate
                )
            case_schedules = 0
            for final_dist, hybrid_pushes, cas_pushes in wave_outcomes(
                initial, windows
            ):
                case_schedules += 1
                self.assertEqual(final_dist, tuple(expected))
                for destination, distance in enumerate(expected):
                    if distance < initial[destination]:
                        self.assertIn(
                            (destination, distance),
                            {(push[1], push[2]) for push in hybrid_pushes},
                        )
                if hybrid_pushes != cas_pushes:
                    exact_mismatches += 1
                if any(
                    push[2] > final_dist[push[1]] for push in hybrid_pushes
                ):
                    stale_push_witnesses += 1
                frontier_signatures.add(hybrid_pushes)
            schedules_per_case = schedules_per_case or case_schedules
            self.assertEqual(case_schedules, schedules_per_case)
            cases += 1

        self.assertEqual(cases, 256)
        self.assertEqual(schedules_per_case, 252)
        self.assertGreater(exact_mismatches, 0)
        self.assertGreater(stale_push_witnesses, 0)
        self.assertGreater(len(frontier_signatures), 1)

    def test_page_local_reconstruction_matches_legacy_page_boundaries(self):
        windows = make_windows((((0, 0), (0, 0)),), (8, 6, 5, 7))
        final_dist, hybrid_pushes, cas_pushes = next(
            wave_outcomes((10,), windows)
        )
        self.assertEqual(final_dist, (5,))
        self.assertEqual(hybrid_pushes, ((0, 0, 6), (0, 0, 5)))
        self.assertEqual(cas_pushes, ((0, 0, 8), (0, 0, 6), (0, 0, 5)))

    def test_serial_min_without_intra_page_lane_order_is_insufficient(self):
        # Original lane order is candidate 5 then 7.  If a merely atomic MIN
        # implementation applies lane 7 first, old[7]=10 and old[5]=7.  The
        # current "last original alias" reconstruction derives page-final 7,
        # pushes 7, and misses the architectural final decrease to 5.
        distance = 10
        old = [None, None]
        for original_lane, candidate in ((1, 7), (0, 5)):
            old[original_lane] = distance
            distance = min(distance, candidate)
        reconstructed_page_final = min(old[1], 7)
        pushes = [
            candidate
            for lane, candidate in enumerate((5, 7))
            if candidate == reconstructed_page_final
            and old[lane] > reconstructed_page_final
        ]
        self.assertEqual(distance, 5)
        self.assertEqual(reconstructed_page_final, 7)
        self.assertEqual(pushes, [7])
        self.assertNotIn(distance, pushes)

    def test_active_source_snapshot_is_a_sufficient_phase_boundary(self):
        # R captures source U, L atomically lowers U from 8 to 3 and pushes it,
        # and U uses the captured value to relax V through a weight-2 edge.
        one_wave_v = set()
        for order in itertools.permutations("RLU"):
            if order.index("R") > order.index("U"):
                continue
            source = 8
            captured = None
            destination = 20
            for event in order:
                if event == "R":
                    captured = source
                elif event == "L":
                    source = min(source, 3)
                else:
                    destination = min(destination, captured + 2)
            one_wave_v.add(destination)
        self.assertEqual(one_wave_v, {5, 10})

        # Snapshot-before-writes selects the legal base schedule R,L,U.  Its
        # stale V=10 push is extra work; the U=3 push repeats the relaxation
        # and converges V to 5.
        snapshot_source = 8
        source_after_wave = min(snapshot_source, 3)
        destination_after_wave = min(20, snapshot_source + 2)
        destination_after_repeat = min(
            destination_after_wave, source_after_wave + 2
        )
        self.assertEqual(destination_after_wave, 10)
        self.assertEqual(destination_after_repeat, 5)

    def test_exhaustive_repeated_iterations_and_local_bin_merges_converge(
        self,
    ):
        result = explore_repeated_iterations()
        self.assertEqual(result["terminal_distances"], {result["oracle"]})
        self.assertGreater(result["states"], 1)
        self.assertGreater(result["transitions"], result["states"])
        self.assertTrue(result["repeated_bin"])
        self.assertTrue(result["active_source_hazard"])
        self.assertTrue(result["cross_owner_alias"])
        self.assertTrue(result["multi_owner_merge"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
