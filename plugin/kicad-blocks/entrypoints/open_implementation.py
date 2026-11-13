"""KiCad IPC entrypoint for opening the selected block implementation."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

plugin_module = import_module("kicad_blocks.plugin")


if __name__ == "__main__":
    plugin_module.run_action("open-implementation")
