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


def test_flat_tabs_preserve_notebook_api_and_support_pointer_and_keyboard():
    import tkinter as tk
    from tkinter import ttk

    from kicad_blocks.theme import apply_tk_theme
    from kicad_blocks.ui_components import FlatTabControl

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

        changed_tabs = []
        tabs.bind(
            "<<NotebookTabChanged>>",
            lambda _event: changed_tabs.append(tabs.index("current")),
        )
        tabs.tab_labels[1].event_generate("<Button-1>")
        root.update()
        assert tabs.select() == str(schematic)
        assert tabs.index(tabs.select()) == 1
        assert changed_tabs == [1]
        assert schematic.winfo_ismapped()

        tabs.tab_labels[1].focus_force()
        root.update()
        tabs.tab_labels[1].event_generate("<Left>")
        root.update()
        assert tabs.select() == str(facade)
        assert tabs.index("current") == 0
        assert changed_tabs == [1, 0]
        assert facade.winfo_ismapped()
    finally:
        root.destroy()


def test_auto_hide_scrollbar_switches_between_idle_and_active_states():
    import tkinter as tk

    from kicad_blocks.theme import AutoHideScrollbar, apply_tk_theme, palette_for_theme

    assert AutoHideScrollbar.IDLE_WIDTH == 2
    assert AutoHideScrollbar.ACTIVE_WIDTH == 10
    assert AutoHideScrollbar.HIDE_DELAY_MS == 900

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
        scroll._cancel_hide()
        scroll._hide_if_idle()
        assert scroll.cget("style") == scroll.idle_style
    finally:
        root.destroy()

def test_search_tooltip_appears_above_focused_field_without_covering_results():
    import tkinter as tk
    from tkinter import ttk

    from kicad_blocks import plugin as plugin_actions
    from kicad_blocks.theme import apply_tk_theme

    plugin_actions._load_tkinter()
    root = tk.Tk()
    try:
        apply_tk_theme(root, "light")
        root.geometry("520x300+0+0")
        search_label = ttk.Label(root, text="Search blocks")
        search_label.pack(anchor="w", padx=12, pady=(48, 3))
        search = ttk.Entry(root)
        search.pack(fill="x", padx=12, pady=(0, 6))
        results = ttk.Treeview(root, columns=("block",), show="headings", height=4)
        results.heading("block", text="Block")
        results.pack(fill="both", expand=True, padx=12)

        plugin_actions._add_tooltip(
            search,
            "Filter blocks · ↑/↓ browse · Enter place",
            placement="above-right",
        )
        root.update_idletasks()
        root.update()
        # The real browser opens with this field focused; generate the same
        # focus event so the tooltip callback is deterministic under Xvfb.
        search.event_generate("<FocusIn>")
        root.update()

        tooltip = next(
            child for child in search.winfo_children()
            if isinstance(child, tk.Toplevel)
        )
        tooltip_top = tooltip.winfo_rooty()
        tooltip_bottom = tooltip_top + tooltip.winfo_height()
        search_top = search.winfo_rooty()
        search_right = search.winfo_rootx() + search.winfo_width()
        tooltip_left = tooltip.winfo_rootx()
        tooltip_right = tooltip_left + tooltip.winfo_width()
        label_right = search_label.winfo_rootx() + search_label.winfo_width()

        assert tooltip_bottom <= search_top
        assert tooltip_left >= label_right
        assert tooltip_right <= search_right
        assert tooltip_top >= 0
        assert results.winfo_ismapped()
    finally:
        root.destroy()
