"""GUI interaction tests for shared Tk components."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="Tk component interaction tests require a display (run under xvfb-run).",
)

from kicad_blocks.theme import AutoHideScrollbar, apply_tk_theme, palette_for_theme  # noqa: E402
from kicad_blocks.ui_components import FlatTabControl  # noqa: E402


def test_flat_tabs_preserve_notebook_api_and_support_pointer_and_keyboard():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    try:
        apply_tk_theme(root, "light")
        root.geometry("520x260")
        tabs = FlatTabControl(root, contract_id="test.views")
        tabs.pack(fill="both", expand=True)
        facade = ttk.Frame(tabs)
        ttk.Label(facade, text="Facade page").pack(padx=12, pady=12)
        schematic = ttk.Frame(tabs)
        ttk.Label(schematic, text="Schematic page").pack(padx=12, pady=12)
        tabs.add(facade, text="Facade")
        tabs.add(schematic, text="Schematic")
        root.update_idletasks()
        root.update()

        page_paths = tabs.tabs()
        assert page_paths == [str(facade), str(schematic)]
        assert tabs.tab(page_paths[0], "text") == "Facade"
        assert tabs.tab(page_paths[1], "text") == "Schematic"
        assert tabs.nametowidget(page_paths[0]) is facade
        assert tabs.select() == str(facade)
        assert facade.winfo_ismapped()
        assert not schematic.winfo_ismapped()

        tabs.tab_labels[1].event_generate("<Button-1>")
        root.update()
        assert tabs.select() == str(schematic)
        assert schematic.winfo_ismapped()

        tabs.tab_labels[1].focus_set()
        tabs.tab_labels[1].event_generate("<Left>")
        root.update()
        assert tabs.select() == str(facade)
        assert facade.winfo_ismapped()
    finally:
        root.destroy()


def test_auto_hide_scrollbar_switches_between_idle_and_active_states():
    import tkinter as tk

    root = tk.Tk()
    try:
        apply_tk_theme(root, "light")
        root.geometry("180x120")
        scroll = AutoHideScrollbar(
            root,
            orient="vertical",
            command=lambda *_args: None,
            palette=palette_for_theme("light"),
        )
        scroll.pack(fill="y")
        root.update_idletasks()
        root.update()

        scroll.set("0", "1")
        assert scroll.scrollable is False
        assert scroll.cget("style") == scroll.idle_style

        scroll.set("0", "0.45")
        assert scroll.scrollable is True
        assert scroll.cget("style") == scroll.active_style
        scroll._hide_if_idle()
        assert scroll.cget("style") == scroll.idle_style
    finally:
        root.destroy()
