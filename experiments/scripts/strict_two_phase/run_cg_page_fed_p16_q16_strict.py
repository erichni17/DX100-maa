#!/usr/bin/env python3
"""Primary Scott-style non-fused p16/product-pages/q16 strict CG gate."""

import importlib.util
from pathlib import Path

IMPLEMENTATION = Path(__file__).with_name("run_cg_fused_p16_q16_strict.py")
SPEC = importlib.util.spec_from_file_location(
    "strict_p16_q16_gate", IMPLEMENTATION
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {IMPLEMENTATION}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
main = MODULE.main


if __name__ == "__main__":
    raise SystemExit(main())
