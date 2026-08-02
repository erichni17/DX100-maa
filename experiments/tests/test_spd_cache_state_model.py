import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/analysis/spd_cache_state_model.py"
SPEC = importlib.util.spec_from_file_location(
    "spd_cache_state_model", MODULE_PATH
)
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)


def ready(state, tile):
    state = MODEL.allocate(state, tile)
    token = MODEL.Token(tile, 0, state.tiles[tile].generation)
    state = MODEL.backing_ack(state, token)
    return MODEL.backing_ready(state, tile)


class SPDCacheStateModelTest(unittest.TestCase):
    def test_bounded_reachability_checks_all_safety_properties(self):
        report = MODEL.explore(max_depth=10)
        self.assertGreater(report["reachable_states"], 100)
        self.assertGreater(report["edges"], report["reachable_states"])

    def test_stale_fill_after_descriptor_reuse_cannot_install(self):
        state = ready(MODEL.initial_state(), 0)
        old = MODEL.current_token(state, 0, 0)
        state = MODEL.start_fill(MODEL.miss(state, 0, 0))
        state = MODEL.free(
            state, 0
        )  # transfer is now stale but still occupies slot
        state = ready(state, 0)
        new = MODEL.current_token(state, 0, 1)
        state = MODEL.miss(state, 0, 1)
        state = MODEL.fill_response(state, old)
        self.assertEqual(state.slot.phase, MODEL.EMPTY)
        state = MODEL.start_fill(state)
        state = MODEL.fill_response(state, new)
        self.assertEqual(state.slot.token, new)
        self.assertEqual(state.slot.phase, MODEL.CLEAN)

    def test_late_writeback_ack_cannot_free_a_reused_fill(self):
        state = ready(MODEL.initial_state(), 0)
        old = MODEL.current_token(state, 0, 0)
        state = MODEL.fill_response(
            MODEL.start_fill(MODEL.miss(state, 0, 0)), old
        )
        state = MODEL.pin_read(state, old)
        state = MODEL.dirty_write(state, old)
        state = MODEL.release(state, old)
        state = MODEL.evict(state)
        state = MODEL.writeback_ack(state, old)
        state = MODEL.free(state, 0)
        state = ready(state, 0)
        new = MODEL.current_token(state, 0, 1)
        state = MODEL.start_fill(MODEL.miss(state, 0, 1))
        self.assertEqual(MODEL.writeback_ack(state, old), state)
        self.assertEqual(MODEL.fill_response(state, new).slot.token, new)

    def test_competing_misses_share_one_slot_without_aliasing(self):
        state = ready(MODEL.initial_state(), 0)
        state = ready(state, 1)
        first = MODEL.current_token(state, 0, 0)
        second = MODEL.current_token(state, 1, 0)
        state = MODEL.miss(state, 0, 0)
        state = MODEL.miss(state, 1, 0)
        self.assertEqual(state.miss_queue, (first, second))
        state = MODEL.fill_response(MODEL.start_fill(state), first)
        state = MODEL.evict(state)
        state = MODEL.fill_response(MODEL.start_fill(state), second)
        self.assertEqual(state.slot.token, second)
        self.assertEqual(state.slot.phase, MODEL.CLEAN)

    def test_dirty_and_pinned_eviction_rules(self):
        state = ready(MODEL.initial_state(), 0)
        token = MODEL.current_token(state, 0, 0)
        state = MODEL.fill_response(
            MODEL.start_fill(MODEL.miss(state, 0, 0)), token
        )
        state = MODEL.pin_read(state, token)
        with self.assertRaisesRegex(MODEL.PreconditionsError, "pinned"):
            MODEL.evict(state)
        state = MODEL.dirty_write(state, token)
        state = MODEL.release(state, token)
        self.assertEqual(MODEL.evict(state).slot.phase, MODEL.WRITEBACK)


if __name__ == "__main__":
    unittest.main()
