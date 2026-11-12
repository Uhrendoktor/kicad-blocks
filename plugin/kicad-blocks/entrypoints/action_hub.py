"""KiCad IPC entrypoint for the grouped KiCad Blocks task hub."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

# KiCad runs this file from the entrypoints directory. Add the plugin root so
# the package import is stable for both the managed runtime and direct tests.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

run_action = import_module("kicad_blocks.plugin").run_action


if __name__ == "__main__":
    run_action("open-hub")
