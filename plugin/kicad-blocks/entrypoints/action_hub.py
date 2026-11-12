"""KiCad IPC entrypoint for the grouped KiCad Blocks task hub."""

from __future__ import annotations

import sys
from pathlib import Path

# KiCad runs this file from the entrypoints directory. Add the plugin root so
# the package import is stable for both the managed runtime and direct tests.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from kicad_blocks.plugin import run_action


if __name__ == "__main__":
    run_action("open-hub")
