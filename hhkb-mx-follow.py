#!/usr/bin/env python3
"""Legacy command name for desk-switch. Same program, same flags."""

from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().with_name("desk-switch.py")), run_name="__main__")
