import hashlib
from pathlib import Path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_evidence(
    path: Path,
    manifest: dict[str, str],
    result: dict[str, str],
    artifact: str = "a" * 64,
) -> None:
    (path / "checkpoint.exit").write_text("0\n")
    (path / "restore.exit").write_text("0\n")
    (path / "virtual_tile_consumer_case.pass").touch()
    (path / "checkpoint.log").write_text(
        "VIRTUAL_TILE_CONSUMER_LAYOUT "
        f"mode={manifest['mode']} page_elements={manifest['page_elements']} "
        f"logical_elements={manifest['logical_tile_elements']} "
        "mem_size=2147483648\n"
    )
    (path / "restore.log").write_text(
        "VIRTUAL_TILE_CONSUMER_RESULT "
        f"mode={manifest['mode']} page_elements={manifest['page_elements']} "
        f"hash={result['output_hash']} errors=0\n"
        "ROI Ended\n"
        f"CH0_num_RD_commands_T: {result['dram_reads']}\n"
        f"CH0_num_ACT_commands_T: {result['dram_activates']}\n"
        f"CH0_num_PRE_commands_T: {result['dram_precharges']}\n"
        "Exiting @ tick 100 because m5_exit instruction encountered\n"
    )

    run = path / "run"
    run.mkdir()
    config_values = {
        "num_tile_elements": manifest["logical_tile_elements"],
        "physical_tile_elements": manifest["physical_tile_elements"],
        "num_initial_row_table_slices": manifest["row_table_slices"],
        "num_row_table_rows_per_slice": manifest["row_table_rows_per_slice"],
        "num_row_table_entries_per_subslice_row": manifest[
            "row_table_entries_per_subslice_row"
        ],
        "virtual_response_slots": manifest["virtual_response_slots"],
        "virtual_response_word_pool": manifest["virtual_response_word_pool"],
        "virtual_combine_slots": manifest["virtual_combine_slots"],
        "virtual_combine_words": manifest["virtual_combine_words"],
        "virtual_combine_ways": manifest["virtual_combine_ways"],
        "virtual_combine_victim_policy": manifest[
            "virtual_combine_victim_policy"
        ],
        "virtual_combine_banks": manifest["virtual_combine_banks"],
        "virtual_index_partitions": manifest["virtual_index_partitions"],
        "virtual_grow_order": (
            "true" if manifest["virtual_grow_order"] == "1" else "false"
        ),
    }
    if "virtual_index_filter_words_per_cycle" in manifest:
        config_values["virtual_index_filter_words_per_cycle"] = manifest[
            "virtual_index_filter_words_per_cycle"
        ]
    (run / "config.ini").write_text(
        "[system.maa]\n"
        + "".join(f"{key}={value}\n" for key, value in config_values.items())
    )

    stats = {
        "simTicks": result["simTicks"],
        "simInsts": result["simInsts"],
        "system.maa.I0_IND_VirtIndexLineReads": result["index_line_reads"],
        "system.maa.I0_IND_VirtIndexWords": result["index_words"],
        "system.maa.I0_IND_VirtIndexWordHighWater": result["index_hwm"],
        "system.maa.I0_IND_VirtWriteIssues": result["write_issues"],
        "system.maa.I0_IND_VirtWriteCompletions": result["write_completions"],
        "system.maa.I0_IND_VirtPagesReady": result["pages_ready"],
        "system.maa.I0_IND_NumCacheLineInserted": result[
            "row_table_cache_lines"
        ],
        "system.maa.I0_IND_NumRowsInserted": result["row_table_rows_inserted"],
        "system.maa.I0_IND_NumUniqueCacheLineInserted": result[
            "row_table_unique_cache_lines"
        ],
        "system.maa.I0_IND_NumUniqueRowsInserted": result[
            "row_table_unique_rows"
        ],
        "system.maa.I0_IND_NumRTFull": result["row_table_full_events"],
        "system.maa.I0_IND_VirtBuildRounds": result["virtual_build_rounds"],
        "system.maa.I0_IND_LoadsMemAccessing": str(
            int(result["source_reads"]) + int(result["index_line_reads"])
        ),
    }
    if "index_filter_words" in result:
        stats["system.maa.I0_IND_VirtIndexFilterWords"] = result[
            "index_filter_words"
        ]
        stats["system.maa.I0_IND_VirtIndexFilterCycles"] = result[
            "index_filter_cycles"
        ]
    (run / "stats.txt").write_text(
        "---------- Begin Simulation Statistics ----------\n"
        + "".join(f"{key} {value}\n" for key, value in stats.items())
        + "---------- End Simulation Statistics ----------\n"
    )

    artifacts = path / "artifacts"
    artifacts.mkdir()
    gem5 = artifacts / "gem5.opt"
    source = path / "source.diff"
    status = path / "source_status.txt"
    gem5.write_text(artifact)
    source.write_text("source\n")
    status.write_text("")
    (path / "artifact_sha256.txt").write_text(
        f"{_digest(gem5)}  {gem5}\n"
        f"{_digest(source)}  {source}\n"
        f"{_digest(status)}  {status}\n"
    )
