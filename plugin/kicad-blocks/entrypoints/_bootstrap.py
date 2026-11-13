"""Shared import bootstrap for KiCad's script-based IPC action entrypoints."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path


def load_plugin():
    """Load the plugin module after making the package root importable."""
    plugin_root = Path(__file__).resolve().parents[1]
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    return import_module("kicad_blocks.plugin")


def run_action(action: str) -> None:
    """Invoke one registered plugin action."""
    load_plugin().run_action(action)
