import importlib.util
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "experiments/analysis/model_soa_value_owner_reuse.py"
SPEC = importlib.util.spec_from_file_location("owner_reuse", MODULE)
assert SPEC is not None and SPEC.loader is not None
owner_reuse = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owner_reuse)


def request(unit: int, generation: int, paddr: int, action: str) -> str:
    return (
        "event=soa_jit_value_request schema=1 "
        f"unit={unit} generation={generation} paddr={paddr:#x} "
        f"action={action}\n"
    )


def test_replay_is_per_unit_per_generation_and_bounded():
    trace = "".join(
        (
            request(0, 1, 0x1000, "fill"),
            request(1, 1, 0x1000, "fill"),
            request(0, 1, 0x1040, "fill"),
            request(0, 1, 0x1000, "hit"),
            request(0, 2, 0x1000, "fill"),
            request(1, 1, 0x1000, "merge"),
        )
    )
    report = owner_reuse.replay(io.StringIO(trace), [1, 2])
    assert report["requests"] == 6
    assert report["observed"] == {"hits_or_merges": 2, "fills": 4}
    one, two = report["capacities"]
    assert one["ideal_hits"] == 1
    assert two["ideal_hits"] == 2
    assert one["payload_bytes"] == 64
    assert two["payload_bytes"] == 128
