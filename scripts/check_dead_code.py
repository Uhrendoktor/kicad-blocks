#!/usr/bin/env python3
"""Run Vulture on high-confidence, statically analyzable production modules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_FILES = (
    "plugin/kicad-blocks/kicad_blocks/model.py",
    "plugin/kicad-blocks/kicad_blocks/extraction.py",
    "plugin/kicad-blocks/kicad_blocks/repository.py",
    "plugin/kicad-blocks/kicad_blocks/composition.py",
    "plugin/kicad-blocks/kicad_blocks/operations.py",
    "plugin/kicad-blocks/kicad_blocks/pcb_layout.py",
    "plugin/kicad-blocks/kicad_blocks/pcb_placement.py",
)


def main() -> int:
    """Fail on dead code Vulture can identify with 100% confidence."""
    paths = [str(ROOT / path) for path in CORE_FILES]
    return subprocess.call(
        [sys.executable, "-m", "vulture", "--min-confidence", "100", *paths],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
