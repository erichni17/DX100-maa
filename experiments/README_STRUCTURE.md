# DX100 Experiments Workspace

This directory is the landing zone for experiment orchestration and artifacts.

## Layout

- `scripts/`: experiment drivers and helper launch scripts.
- `campaigns/`: active, structured experiment campaigns.
- `archive/`: legacy or one-off dumps moved out of repo root.

## Campaign convention

Use one folder per campaign:

- `campaigns/YYYY-MM-DD_<topic>_<scope>/`

Each campaign should contain:

- `manifest.yaml`: question, hypothesis, commands, SHAs.
- `logs/`: raw run logs.
- `tables/`: curated `*.tsv`/`*.txt` metrics.
- `notes/summary.md`: concise interpretation and caveats.

## Reorg note (July 2026)

Root-level run directories and logs were moved to:

- `archive/pre_reorg_root_dump_2026-07/`

This keeps the DX100 repo root focused on source/config code paths.
