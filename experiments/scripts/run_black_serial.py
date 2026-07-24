#!/usr/bin/env python3
"""Run Black on one file per process to avoid sandbox pool deadlocks."""

import subprocess
import sys


def main():
    result = 0
    for path in sys.argv[1:]:
        completed = subprocess.run(
            [sys.executable, "-m", "black", path],
            check=False,
        )
        if completed.returncode != 0 and result == 0:
            result = completed.returncode
    return result


if __name__ == "__main__":
    raise SystemExit(main())
