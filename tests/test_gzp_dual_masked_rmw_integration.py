import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks/UME/gradzatp.cpp"


def text() -> str:
    return SOURCE.read_text(encoding="utf-8")


def between(source: str, begin: str, end: str) -> str:
    start = source.index(begin)
    return source[start : source.index(end, start)]


def test_new_selector_is_opt_in_and_preserves_existing_names() -> None:
    source = text()
    for selector in (
        'treatment == "volume_soa_jit"',
        'treatment == "volume_masked_index"',
        'treatment == "dual_masked_index"',
        'treatment == "soa_jit"',
    ):
        assert selector in source
    assert 'return "dual_masked_index_soa_jit"' in source
    assert 'return "volume_masked_index_soa_jit"' in source


def test_dual_arm_publishes_only_gradient_and_uses_masked_indices_twice() -> (
    None
):
    source = text()
    registration = between(
        source,
        "// Existing arms retain their original publication registrations",
        'std::cout << "ROI Begin"',
    )
    dual_registration = between(
        registration,
        "if (gzp_rmw_treatment == GzpRmwTreatment::DualMaskedIndexSoaJit)",
        "} else if",
    )
    assert "soa_gradient_values" in dual_registration
    assert "soa_predicates" not in dual_registration
    assert "corner_predicate_soa" not in dual_registration

    assert (
        source.count(
            "maa_indirect_rmw_vector_soa_jit_masked_indices<DATATYPE>"
        )
        == 2
    )
    gradient = between(
        source,
        "soa_dual_gradient_issues.fetch_add",
        "wait_ready(soa_gradient_completion_tiles",
    )
    assert "point_gradient.data()" in gradient
    assert "c_to_p_map.data() + c" in gradient
    assert "soa_gradient_values[omp_thread_id]" in gradient
    assert "soa_predicates" not in gradient
    assert "corner_predicate_soa" not in gradient


def test_dual_arm_fences_every_page_and_preserves_physical_tail() -> None:
    source = text()
    page_path = between(
        source,
        "if (soa_both_full_window ||\n"
        "                    soa_dual_masked_index_full_window)",
        "wait_ready(tile1);",
    )
    assert (
        "maa_publish_spd_page_logical16_response_bearing<DATATYPE>"
        in page_path
    )
    assert "soa_dual_gradient_page_issues.fetch_add" in page_path
    assert "wait_ready(soa_gradient_completion_tiles" in page_path
    assert "soa_dual_gradient_page_completions.fetch_add" in page_path
    assert "soa_published_gradient_values.fetch_add" in page_path

    full_window = between(
        source,
        "const bool soa_dual_masked_index_full_window",
        "const bool soa_volume_full_window",
    )
    assert "gather_size == TILE_SIZE" in full_window
    physical_fallback = between(
        source,
        "} else {\n                    maa_indirect_rmw_vector<DATATYPE>(",
        "wait_ready(tile1);",
    )
    assert "point_gradient.data()" in physical_fallback
    assert "tile4" in physical_fallback
    assert "tileCond" in physical_fallback


def test_terminal_is_fail_closed_for_order_and_publication_counts() -> None:
    source = text()
    terminal = between(
        source,
        "static void validate_gzp_dual_masked_terminal()",
        "static int gzp_separate_predicate_publications",
    )
    for exact in (
        "soa_dual_volume_issues.load() == windows",
        "soa_dual_volume_completions.load() == windows",
        "soa_dual_gradient_page_issues.load() == pages",
        "soa_dual_gradient_page_completions.load() == pages",
        "soa_dual_gradient_issues.load() == windows",
        "soa_dual_gradient_completions.load() == windows",
        "soa_published_gradient_values.load() == values",
        "soa_published_predicates.load() == 0",
    ):
        assert exact in terminal
    assert '"UME_GZP_DUAL_MASKED_TERMINAL result=FAIL"' in terminal
    assert "std::abort()" in terminal
    assert '"UME_GZP_DUAL_MASKED_TERMINAL result=PASS"' in source


def test_publisher_hardware_ledger_matches_instantiated_cpp_type(
    tmp_path: Path,
) -> None:
    source = text()
    expected = {
        "GzpPublisherCreditsPerInstance": 8,
        "GzpPublisherLineBytes": 64,
        "GzpPublisherControlBytesPerInstance": 408,
        "GzpPublisherPayloadBytesPerInstance": 512,
        "GzpPublisherTotalBytesPerInstance": 920,
    }
    for name, value in tuple(expected.items())[:3]:
        match = re.search(
            rf"static constexpr uint64_t {name} = (\d+);", source
        )
        assert match and int(match.group(1)) == value
    assert "GzpPublisherCreditsPerInstance * GzpPublisherLineBytes" in source
    assert (
        "GzpPublisherPayloadBytesPerInstance +\n"
        "    GzpPublisherControlBytesPerInstance" in source
    )

    probe = tmp_path / "publisher_accounting.cc"
    probe.write_text(
        '#include "mem/MAA/ResponseBearingSpdPublisher.hh"\n'
        "#include <iostream>\n"
        "int main() {\n"
        "  using P = gem5::ResponseBearingSpdPublisher<4, 2, 8>;\n"
        "  static_assert(P::chargedPayloadBytes() == 512);\n"
        "  static_assert(P::chargedControlBytes() == 408);\n"
        "  static_assert(P::chargedBytes() == 920);\n"
        "  static_assert(P::chargedPayloadBytes() + "
        "P::chargedControlBytes() == P::chargedBytes());\n"
        "  std::cout << P::chargedPayloadBytes() << ' ' "
        "<< P::chargedControlBytes() << ' ' << P::chargedBytes();\n"
        "}\n",
        encoding="utf-8",
    )
    for mode, flags in (
        ("optimized", ["-O2"]),
        (
            "sanitized",
            [
                "-O1",
                "-g",
                "-fno-omit-frame-pointer",
                "-fsanitize=address,undefined",
            ],
        ),
    ):
        binary = tmp_path / f"publisher_accounting_{mode}"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-Werror",
                f"-I{ROOT / 'src'}",
                *flags,
                str(probe),
                "-o",
                str(binary),
            ],
            check=True,
        )
        env = os.environ.copy()
        env["ASAN_OPTIONS"] = "detect_leaks=0:halt_on_error=1"
        env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        assert (
            subprocess.check_output([str(binary)], text=True, env=env)
            == "512 408 920"
        )
