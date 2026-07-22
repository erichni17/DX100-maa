#!/usr/bin/env python3
"""Create a disjoint workflow by copying exact tasks from a source workflow."""

import argparse
import json
from pathlib import Path


def select_tasks(document, task_ids):
    tasks = document.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise SystemExit("source workflow has no task list")
    by_id = {task.get("id"): task for task in tasks}
    if len(by_id) != len(tasks) or None in by_id:
        raise SystemExit("source workflow task ids are missing or duplicated")
    missing = [task_id for task_id in task_ids if task_id not in by_id]
    if missing:
        raise SystemExit(
            "source workflow tasks missing: " + ", ".join(missing)
        )
    if len(set(task_ids)) != len(task_ids):
        raise SystemExit("requested task ids are duplicated")
    return [by_id[task_id] for task_id in task_ids]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--task", action="append", required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise SystemExit(f"source workflow missing: {source}")
    document = json.loads(source.read_text())
    selected = select_tasks(document, args.task)
    result = {
        "version": document.get("version", 1),
        "name": args.name,
        "tasks": selected,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(output)


if __name__ == "__main__":
    main()
