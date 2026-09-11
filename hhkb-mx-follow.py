#!/usr/bin/env python3
"""Deprecated alias for desk. Same program, same flags."""

from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().with_name("desk.py")), run_name="__main__")
