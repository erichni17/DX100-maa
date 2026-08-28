import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/analyze_virtual_combiner_reuse.py"
SPEC = importlib.util.spec_from_file_location("combiner_reuse", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
reuse = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reuse
SPEC.loader.exec_module(reuse)


def test_compact_lines_close_once_under_every_policy() -> None:
    events = [(line * 64, word) for line in range(16) for word in range(16)]
    for policy in (
        "round_robin",
        "fewest_words",
        "most_words",
        "lru",
        "tree_plru",
        "belady",
    ):
        result = reuse.replay_operation(events, policy, 16, 4)
        assert result.writes == 16
        assert result.full_writes == 16
        assert result.eviction_writes == 0
        assert result.written_words == len(events)


def test_belady_improves_a_fixed_capacity_thrashing_sequence() -> None:
    events = [(line * 64, word) for word in range(16) for line in range(32)]
    round_robin = reuse.replay_operation(events, "round_robin", 16, 4)
    belady = reuse.replay_operation(events, "belady", 16, 4)
    assert round_robin.writes == 512
    assert belady.writes == 304
    assert belady.writes < round_robin.writes
    assert round_robin.written_words == belady.written_words == len(events)


def test_fp64_lines_close_after_eight_words() -> None:
    events = [(line * 64, word) for line in range(16) for word in range(8)]
    result = reuse.replay_operation(
        events, "round_robin", 16, 0, words_per_line=8
    )
    assert result.writes == 16
    assert result.full_writes == 16
    assert result.eviction_writes == 0
    assert result.written_words == len(events)


def test_shared_word_pool_can_bind_before_line_slots() -> None:
    events = [(line * 64, word) for word in range(4) for line in range(4)]
    uncapped = reuse.replay_operation(
        events, "round_robin", 4, 0, words_per_line=4
    )
    capped = reuse.replay_operation(
        events,
        "round_robin",
        4,
        0,
        words_per_line=4,
        word_capacity=4,
    )
    assert uncapped.writes == 4
    assert uncapped.full_writes == 4
    assert capped.writes == 12
    assert capped.full_writes == 0
    assert capped.written_words == len(events)
