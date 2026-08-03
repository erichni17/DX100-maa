import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UME = ROOT / "benchmarks/UME"


def compile_policy(defines, expected):
    source = (
        '#include "gzz_chunk_policy.h"\n'
        f'static_assert(GZZ_LOOP_CHUNK_SIZE == {expected}, "policy");\n'
        "int main() { return 0; }\n"
    )
    subprocess.run(
        [
            "g++",
            "-std=c++11",
            "-fsyntax-only",
            "-x",
            "c++",
            *[f"-D{item}" for item in defines],
            "-I",
            str(UME),
            "-",
        ],
        input=source,
        text=True,
        check=True,
    )


def test_production_caps_large_gzz_feed_at_16k():
    compile_policy(["TILE_SIZE=32768"], 16384)
    compile_policy(["TILE_SIZE=65536"], 16384)


def test_production_preserves_small_physical_chunks():
    compile_policy(["TILE_SIZE=1024"], 1024)
    compile_policy(["TILE_SIZE=16384"], 16384)


def test_legacy_and_explicit_attribution_controls_remain_available():
    compile_policy(
        ["TILE_SIZE=65536", "GZZ_LEGACY_TILE_COUPLED_CHUNKS=1"], 65536
    )
    compile_policy(["TILE_SIZE=32768", "GZZ_LOGICAL_CHUNK_SIZE=8192"], 8192)
