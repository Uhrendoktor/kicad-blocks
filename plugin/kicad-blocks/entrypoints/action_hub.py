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

plugin_module = import_module("kicad_blocks.plugin")
run_action = plugin_module.run_action


def _smoke_test() -> None:
    """Build and inspect the real Tk task hub without a window-manager dependency."""
    plugin_module._load_tkinter()
    root = plugin_module.tk.Tk()
    try:
        notebook = plugin_module._show_action_hub(root)
        root.update()
        tab_names = [
            notebook.tab(tab_id, "text")
            for tab_id in notebook.tabs()
        ]

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        buttons = {
            str(widget.cget("text"))
            for widget in descendants(root)
            if isinstance(widget, plugin_module.ttk.Button)
        }
        expected_tabs = ["Use & build", "Edit & maintain", "Checks & PCB"]
        expected_actions = {
            "Browse and place blocks",
            "Create block from sheet",
            "Open block implementation",
            "Configure selected instance",
            "Edit facade symbol",
            "Publish implementation",
            "Make selected instance local",
            "Synchronize local copy",
            "Validate registered libraries",
            "Export recursive BOM",
            "Run recursive ERC",
            "Apply selected PCB layout",
            "Apply multiple PCB layouts",
        }
        missing = sorted(expected_actions - buttons)
        if tab_names != expected_tabs or missing:
            raise AssertionError(
                f"Unexpected task hub UI: tabs={tab_names!r}; missing actions={missing!r}"
            )
        print("KiCad Blocks task hub UI passed.")
    finally:
        root.destroy()


if __name__ == "__main__":
    if sys.argv[1:] == ["--smoke-test"]:
        _smoke_test()
    elif len(sys.argv) == 1:
        run_action("open-hub")
    else:
        raise SystemExit("Usage: action_hub.py [--smoke-test]")
