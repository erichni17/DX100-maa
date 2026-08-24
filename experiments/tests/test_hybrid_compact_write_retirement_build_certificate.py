from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCER = (
    "experiments/scripts/"
    "create_hybrid_compact_write_retirement_build_certificate.sh"
)
RUNNER = "experiments/scripts/run_hybrid_compact_write_retirement_ab.sh"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_producer_forces_clean_committed_head_relink():
    producer = read(PRODUCER)
    assert "status --porcelain --untracked-files=all" in producer
    assert 'mv "$gem5" "$backup_dir/gem5.opt"' in producer
    assert "scons --ignore-style build/X86/gem5.opt -j8" in producer
    assert "binary_mtime_epoch -ge $build_start_epoch" in producer
    assert "forced_relink=true" in producer


def test_certificate_binds_source_build_objects_binary_and_dependencies():
    producer = read(PRODUCER)
    for field in (
        "source_commit=",
        "source_tree=",
        "source_archive_sha256=",
        "changed-sources.sha256",
        "build-command.txt",
        "build_log_sha256=",
        "maa-objects.sha256",
        "gem5_sha256=",
        "gem5_mtime_epoch=",
        "ramulator_sha256=",
        "ramulator_spdlog_directory_sha256=",
        "ramulator_yaml_cpp_directory_sha256=",
        "util_m5op_s_sha256=",
        "certificate.sha256",
    ):
        assert field in producer


def test_runner_requires_and_revalidates_certificate():
    runner = read(RUNNER)
    for contract in (
        "certificate.txt",
        "certificate.sha256",
        "sha256sum -c certificate.sha256",
        "source_commit",
        "source_tree",
        "source_archive_sha256",
        "gem5_sha256",
        "gem5_mtime_epoch",
        "changed-sources.sha256",
        "build-certificate",
    ):
        assert contract in runner
