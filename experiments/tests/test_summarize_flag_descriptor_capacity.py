import csv
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUMMARIZER = ROOT / "experiments/scripts/summarize_flag_descriptor_capacity.py"


def write_tsv(path: Path, row: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=row.keys(), delimiter="\t")
        writer.writeheader()
        writer.writerow(row)


class FlagDescriptorCapacityTest(unittest.TestCase):
    def create_case(self, root: Path, case_id: str, candidate: bool) -> Path:
        case = root / "cases" / case_id
        if not candidate:
            case /= "direct4"
        (case / "run").mkdir(parents=True)
        marker = (
            "xrage_checkpoint_recovery.pass"
            if candidate
            else "xrage_attribution_smoke.pass"
        )
        (case / marker).touch()
        (case / "restore.exit").write_text("0\n", encoding="ascii")
        output_hash = 1000 + int(case_id.split("-")[-1])
        (case / "restore.log").write_text(
            f"MAA_GATHER_VERIFY_PASS length=16 hash={output_hash}\n"
            "Exiting @ tick 200 because m5_exit instruction encountered\n",
            encoding="ascii",
        )
        ticks = 105 if candidate else 100
        (case / "run/stats.txt").write_text(
            "---------- Begin Simulation Statistics ----------\n"
            f"simTicks {ticks}\n"
            "---------- End Simulation Statistics ----------\n"
            "---------- Begin Simulation Statistics ----------\n"
            "simTicks 200\n"
            "---------- End Simulation Statistics ----------\n",
            encoding="ascii",
        )
        rows = 16 if candidate else 64
        extra = (
            "virtual_index_force_cache=false\n"
            "virtual_partition_keep_combiner=false\n"
            if candidate
            else ""
        )
        (case / "run/config.ini").write_text(
            "[system.maa]\n"
            "num_tile_elements=16384\n"
            "physical_tile_elements=4096\n"
            f"num_row_table_rows_per_slice={rows}\n"
            f"{extra}",
            encoding="ascii",
        )
        (case / "run/xrage-debug.log").write_text(
            "unit=0 instruction_tick=1 count=16 "
            "fnv=0x0000000000000001 mix=0x0000000000000002\n",
            encoding="ascii",
        )
        input_path = root.parent / "input.json"
        guest_path = root.parent / "guest"
        input_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
        guest_hash = hashlib.sha256(guest_path.read_bytes()).hexdigest()
        manifest = {
            "arm": "direct_index_4k",
            "guest_arm": "direct4",
            "physical_tile_elements": "4096",
            "maa_logical_tile_elements": "16384",
            "workload_chunk_elements": "16384",
            "virtual_grow_order": "0",
            "virtual_native_issue_order": "1",
            "virtual_index_buffer_lines": "128",
            "initial_row_table_slices": "32",
            "row_table_rows_per_slice": str(rows),
            "num_indirect_units_per_maa": "1",
            "debug_flags": "MAAIssueDigest",
            "input": str(input_path),
            "timeout": "none",
        }
        if candidate:
            manifest.update(
                {
                    "checkpoint_run": str(
                        root.parent / "baseline/cases" / case_id / "direct4"
                    ),
                    "checkpoint_retargeted": "0",
                    "virtual_index_force_cache": "0",
                    "virtual_index_partitions": "1",
                    "virtual_index_filter_words_per_cycle": "0",
                    "virtual_partition_keep_combiner": "0",
                    "retirement_cache_size": "1kB",
                    "virtual_combine_slots": "384",
                    "virtual_combine_words": "4096",
                    "virtual_combine_ways": "4",
                    "virtual_response_slots": "128",
                    "virtual_response_word_pool": "480",
                    "virtual_words_per_cycle": "4",
                }
            )
        (case / "manifest.txt").write_text(
            "".join(f"{key}={value}\n" for key, value in manifest.items()),
            encoding="ascii",
        )
        (case / "restore.command").write_text(
            f"gem5 config --cmd {guest_path}\n", encoding="ascii"
        )
        (case / "artifact_sha256.txt").write_text(
            f"{input_hash}  {input_path}\n{guest_hash}  {guest_path}\n",
            encoding="ascii",
        )
        result = {
            "output_hash": output_hash,
            "roi_simTicks": ticks,
            "final_simTicks": 200,
            "stats_blocks": 2,
            "virtual_write_issues": 8,
        }
        if candidate:
            result.update(
                {
                    "row_table_full_events": 2,
                    "virtual_build_rounds": 3,
                }
            )
        write_tsv(case / "result.tsv", result)
        write_tsv(
            case / "dram_commands.tsv",
            {"dram_reads": 10, "dram_activates": 4, "dram_precharges": 3},
        )
        return case

    def test_compares_fourteen_treatment_only_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input.json").write_text("[]\n", encoding="ascii")
            (root / "guest").write_text("guest\n", encoding="ascii")
            baseline = root / "baseline"
            candidate = root / "candidate"
            summary = baseline / "summary"
            summary.mkdir(parents=True)
            (summary / "flag_gather_generalization.json").write_text(
                "{}\n", encoding="ascii"
            )
            for index in range(14):
                case_id = f"gather-{index:02d}"
                self.create_case(baseline, case_id, False)
                self.create_case(candidate, case_id, True)

            output = root / "output"
            result = subprocess.run(
                [str(SUMMARIZER), str(baseline), str(candidate), str(output)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("latency +5.000%", result.stdout)
            self.assertTrue((output / "flag_descriptor_capacity.pass").is_file())

            bad_result = candidate / "cases/gather-00/result.tsv"
            row = read_one_row(bad_result)
            row["output_hash"] = "9999"
            write_tsv(bad_result, row)
            rejected = subprocess.run(
                [
                    str(SUMMARIZER),
                    str(baseline),
                    str(candidate),
                    str(root / "rejected"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("exact output hashes differ", rejected.stderr)


def read_one_row(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as stream:
        return next(csv.DictReader(stream, delimiter="\t"))


if __name__ == "__main__":
    unittest.main()
