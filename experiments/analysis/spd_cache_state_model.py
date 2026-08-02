#!/usr/bin/env python3
"""Finite, executable safety model of a tagged logical-tile SPD cache.

The model intentionally has exactly two logical tile descriptors, two pages per
tile, and one physical SPD slot.  A response carries a (tile, page, generation)
token; it is accepted only while that exact token still owns the slot.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from dataclasses import (
    dataclass,
    replace,
)
from typing import (
    Callable,
    Iterable,
    Optional,
)

TILE_COUNT = 2
PAGES_PER_TILE = 2
MAX_GENERATION = 2
EMPTY = "empty"
FILLING = "filling"
CLEAN = "clean"
DIRTY = "dirty"
WRITEBACK = "writeback"


class PreconditionsError(ValueError):
    """Raised when a client action is not enabled by the model state."""


@dataclass(frozen=True, order=True)
class Token:
    tile: int
    page: int
    generation: int


@dataclass(frozen=True)
class Tile:
    allocated: bool = False
    generation: int = 0
    backing_acked: tuple[bool, bool] = (False, False)
    ready: tuple[bool, bool] = (False, False)


@dataclass(frozen=True)
class Slot:
    phase: str = EMPTY
    token: Token | None = None
    pins: int = 0

    @staticmethod
    def empty() -> Slot:
        return Slot()


@dataclass(frozen=True)
class State:
    tiles: tuple[Tile, ...]
    slot: Slot = Slot()
    miss_queue: tuple[Token, ...] = ()


Action = tuple[str, Callable[[State], State]]


def initial_state() -> State:
    return State(tiles=tuple(Tile() for _ in range(TILE_COUNT)))


def current_token(state: State, tile: int, page: int) -> Token:
    descriptor = state.tiles[tile]
    if not descriptor.allocated:
        raise PreconditionsError("tile descriptor is free")
    return Token(tile, page, descriptor.generation)


def _replace_tile(state: State, index: int, tile: Tile) -> State:
    tiles = list(state.tiles)
    tiles[index] = tile
    return replace(state, tiles=tuple(tiles))


def _require_tile_page(tile: int, page: int) -> None:
    if not 0 <= tile < TILE_COUNT or not 0 <= page < PAGES_PER_TILE:
        raise PreconditionsError("tile or page is outside the finite model")


def _require_slot(state: State, phases: Iterable[str]) -> Token:
    if state.slot.phase not in set(phases) or state.slot.token is None:
        raise PreconditionsError("slot is not in the required phase")
    return state.slot.token


def allocate(state: State, tile: int) -> State:
    _require_tile_page(tile, 0)
    old = state.tiles[tile]
    if old.allocated or old.generation >= MAX_GENERATION:
        raise PreconditionsError("descriptor cannot be allocated again")
    return _replace_tile(
        state, tile, Tile(allocated=True, generation=old.generation + 1)
    )


def backing_ack(state: State, token: Token) -> State:
    """Accept an ACK only for the live descriptor and named backing page."""
    _require_tile_page(token.tile, token.page)
    tile = state.tiles[token.tile]
    if not tile.allocated or tile.generation != token.generation:
        return state  # late acknowledgement is deliberately harmless
    acknowledgements = list(tile.backing_acked)
    acknowledgements[token.page] = True
    return _replace_tile(
        state, token.tile, replace(tile, backing_acked=tuple(acknowledgements))
    )


def backing_ready(state: State, token: Token) -> State:
    """Publish readiness for one acknowledged backing page token."""
    _require_tile_page(token.tile, token.page)
    descriptor = state.tiles[token.tile]
    if not descriptor.allocated or descriptor.generation != token.generation:
        return (
            state  # a stale ready event cannot authorize a reused descriptor
        )
    if not descriptor.backing_acked[token.page]:
        raise PreconditionsError(
            "backing readiness requires an acknowledgement"
        )
    readiness = list(descriptor.ready)
    readiness[token.page] = True
    return _replace_tile(
        state, token.tile, replace(descriptor, ready=tuple(readiness))
    )


def miss(state: State, tile: int, page: int) -> State:
    _require_tile_page(tile, page)
    token = current_token(state, tile, page)
    if not state.tiles[tile].ready[page]:
        raise PreconditionsError(
            "a page cannot miss before its backing-ready event"
        )
    if state.slot.token == token or token in state.miss_queue:
        raise PreconditionsError(
            "page is already resident, filling, or queued"
        )
    return replace(state, miss_queue=state.miss_queue + (token,))


def start_fill(state: State) -> State:
    if state.slot.phase != EMPTY or not state.miss_queue:
        raise PreconditionsError("fill requires an empty slot and queued miss")
    token = state.miss_queue[0]
    return replace(
        state,
        slot=Slot(phase=FILLING, token=token),
        miss_queue=state.miss_queue[1:],
    )


def fill_response(state: State, token: Token) -> State:
    """Handle a fill response; a stale matching in-flight fill only frees it."""
    if state.slot.phase != FILLING or state.slot.token != token:
        return state
    descriptor = state.tiles[token.tile]
    if (
        descriptor.allocated
        and descriptor.generation == token.generation
        and descriptor.ready[token.page]
    ):
        return replace(state, slot=Slot(phase=CLEAN, token=token))
    # The response completed an obsolete transfer, so it may release the slot
    # but cannot install stale data into a later descriptor generation.
    return replace(state, slot=Slot.empty())


def pin_read(state: State, token: Token) -> State:
    if state.slot.phase not in (CLEAN, DIRTY) or state.slot.token != token:
        raise PreconditionsError("read requires the exact resident page")
    descriptor = state.tiles[token.tile]
    if not descriptor.allocated or descriptor.generation != token.generation:
        raise PreconditionsError("read token is stale")
    return replace(state, slot=replace(state.slot, pins=state.slot.pins + 1))


def dirty_write(state: State, token: Token) -> State:
    if state.slot.phase not in (CLEAN, DIRTY) or state.slot.token != token:
        raise PreconditionsError("write requires the exact resident page")
    if state.slot.pins == 0:
        raise PreconditionsError("write requires a held read/pin lease")
    return replace(state, slot=replace(state.slot, phase=DIRTY))


def release(state: State, token: Token) -> State:
    if state.slot.token != token or state.slot.pins == 0:
        raise PreconditionsError("release requires a held token lease")
    return replace(state, slot=replace(state.slot, pins=state.slot.pins - 1))


def evict(state: State) -> State:
    token = _require_slot(state, (CLEAN, DIRTY))
    if state.slot.pins:
        raise PreconditionsError("a pinned slot cannot be evicted")
    if state.slot.phase == DIRTY:
        return replace(state, slot=Slot(phase=WRITEBACK, token=token))
    return replace(state, slot=Slot.empty())


def writeback_ack(state: State, token: Token) -> State:
    """Free only the still-pending writeback token; repeats are stale no-ops."""
    if state.slot.phase == WRITEBACK and state.slot.token == token:
        return replace(state, slot=Slot.empty())
    return state


def free(state: State, tile: int) -> State:
    """Release a descriptor; dirty contents always enter writeback first."""
    _require_tile_page(tile, 0)
    descriptor = state.tiles[tile]
    if not descriptor.allocated:
        raise PreconditionsError("descriptor is already free")
    if state.slot.token and state.slot.token.tile == tile and state.slot.pins:
        raise PreconditionsError("cannot free a descriptor with a pinned page")
    state = _replace_tile(state, tile, Tile(generation=descriptor.generation))
    state = replace(
        state,
        miss_queue=tuple(
            token for token in state.miss_queue if token.tile != tile
        ),
    )
    if state.slot.token and state.slot.token.tile == tile:
        if state.slot.phase == DIRTY:
            state = replace(
                state, slot=Slot(phase=WRITEBACK, token=state.slot.token)
            )
        elif state.slot.phase == CLEAN:
            state = replace(state, slot=Slot.empty())
    return state


def assert_invariants(before: State, after: State, action: str) -> None:
    """Safety properties checked after every enumerated transition."""
    assert len(after.tiles) == TILE_COUNT
    assert len(set(after.miss_queue)) == len(after.miss_queue)
    assert len(after.miss_queue) <= TILE_COUNT * PAGES_PER_TILE
    if after.slot.token:
        token = after.slot.token
        assert (
            0 <= token.tile < TILE_COUNT and 0 <= token.page < PAGES_PER_TILE
        )
        assert after.slot.phase != EMPTY
    else:
        assert after.slot.phase == EMPTY and after.slot.pins == 0
    assert after.slot.pins == 0 or after.slot.phase in (CLEAN, DIRTY)
    for tile in after.tiles:
        for page in range(PAGES_PER_TILE):
            # Each page, rather than the entire descriptor, is ACK-gated.
            assert not tile.ready[page] or tile.backing_acked[page]
    # A dirty resident cannot vanish; it must first become a tagged writeback.
    if before.slot.phase == DIRTY and after.slot.phase != DIRTY:
        assert action in ("evict", "free") and after.slot.phase == WRITEBACK
        assert after.slot.token == before.slot.token
    if action == "evict":
        assert before.slot.pins == 0


def _actions(state: State) -> list[Action]:
    actions: list[Action] = []
    for tile, descriptor in enumerate(state.tiles):
        if not descriptor.allocated and descriptor.generation < MAX_GENERATION:
            actions.append(
                (f"allocate({tile})", lambda s, t=tile: allocate(s, t))
            )
        if descriptor.allocated:
            for page in range(PAGES_PER_TILE):
                token = Token(tile, page, descriptor.generation)
                if not descriptor.backing_acked[page]:
                    actions.append(
                        (
                            f"backing_ack({tile},{page})",
                            lambda s, x=token: backing_ack(s, x),
                        )
                    )
                if (
                    descriptor.backing_acked[page]
                    and not descriptor.ready[page]
                ):
                    actions.append(
                        (
                            f"backing_ready({tile},{page})",
                            lambda s, x=token: backing_ready(s, x),
                        )
                    )
            if state.slot.pins == 0 or not (
                state.slot.token and state.slot.token.tile == tile
            ):
                actions.append((f"free({tile})", lambda s, t=tile: free(s, t)))
            for page in range(PAGES_PER_TILE):
                token = Token(tile, page, descriptor.generation)
                if descriptor.ready[page]:
                    if (
                        state.slot.token != token
                        and token not in state.miss_queue
                    ):
                        actions.append(
                            (
                                f"miss({tile},{page})",
                                lambda s, t=tile, p=page: miss(s, t, p),
                            )
                        )
    if state.slot.phase == EMPTY and state.miss_queue:
        actions.append(("start_fill", start_fill))
    if state.slot.phase == FILLING and state.slot.token:
        actions.append(
            (
                "fill_response",
                lambda s, x=state.slot.token: fill_response(s, x),
            )
        )
    if state.slot.phase in (CLEAN, DIRTY) and state.slot.token:
        token = state.slot.token
        actions.append(("pin_read", lambda s, x=token: pin_read(s, x)))
        if state.slot.pins:
            actions.append(("release", lambda s, x=token: release(s, x)))
            actions.append(
                ("dirty_write", lambda s, x=token: dirty_write(s, x))
            )
        if not state.slot.pins:
            actions.append(("evict", evict))
    if state.slot.phase == WRITEBACK and state.slot.token:
        actions.append(
            (
                "writeback_ack",
                lambda s, x=state.slot.token: writeback_ack(s, x),
            )
        )
    return actions


def successors(state: State) -> list[tuple[str, State]]:
    result = []
    for action, apply in _actions(state):
        next_state = apply(state)
        assert_invariants(state, next_state, action.split("(")[0])
        if next_state != state:
            result.append((action, next_state))
    return result


def can_drain(state: State, max_steps: int = 16) -> bool:
    """Can an idle client drain finite work if memory supplies every response?"""
    if state.slot.pins:
        return (
            True  # a client lease, not a memory deadlock, remains outstanding
        )
    todo = deque([(state, 0)])
    seen = {state}
    while todo:
        current, depth = todo.popleft()
        if current.slot.phase == EMPTY and not current.miss_queue:
            return True
        if depth == max_steps:
            continue
        for action, next_state in successors(current):
            name = action.split("(")[0]
            if name in {
                "start_fill",
                "fill_response",
                "evict",
                "writeback_ack",
            }:
                if next_state not in seen:
                    seen.add(next_state)
                    todo.append((next_state, depth + 1))
    return False


def explore(max_depth: int = 10) -> dict[str, int]:
    """Enumerate all enabled finite transitions through ``max_depth`` steps."""
    frontier = {initial_state()}
    seen = set(frontier)
    edges = 0
    for _ in range(max_depth):
        next_frontier = set()
        for state in frontier:
            assert can_drain(state), "reachable unpinned state cannot drain"
            for _, next_state in successors(state):
                edges += 1
                if next_state not in seen:
                    seen.add(next_state)
                    next_frontier.add(next_state)
        frontier = next_frontier
        if not frontier:
            break
    for state in seen:
        assert can_drain(state), "reachable unpinned state cannot drain"
    return {"depth": max_depth, "reachable_states": len(seen), "edges": edges}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(explore(args.depth), sort_keys=True))


if __name__ == "__main__":
    main()
