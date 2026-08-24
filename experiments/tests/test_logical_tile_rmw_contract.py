from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_contract_and_dual_mode_unit_runner():
    header = (ROOT / "src/mem/MAA/LogicalTileRmwContract.hh").read_text()
    runner = (
        ROOT / "experiments/scripts/run_logical_tile_rmw_contract_unit.sh"
    ).read_text()
    assert "MaxLogicalInsertions = 16 * 1024" in header
    assert "NoOldValue, PageBackedOldValue" in header
    assert "AmbiguousAlias" in header
    assert "StaleGeneration" in header
    assert "DuplicateReadEx" in header and "DuplicateWriteResp" in header
    assert "bool complete() const" in header
    assert "-fsanitize=address,undefined" in runner
    assert "optimized sanitize" in runner
