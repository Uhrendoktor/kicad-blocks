"""Reusable interaction contracts for the production Functional Block Library."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions  # noqa: E402
from ui_contracts import (  # noqa: E402
    ButtonContract,
    EntryInputContract,
    NotebookViewContract,
    PaneGroupContract,
    TreeSelectionContract,
    UiTestContext,
    assert_control_coverage,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="Real Tk interaction contracts require an X display (run under xvfb-run).",
)

LIBRARY_CONTROLS = {
    "library-browser.panes",
    "library-browser.results-pane",
    "library-browser.preview-pane",
    "library-browser.search",
    "library-browser.results",
    "library-browser.summary",
    "library-browser.selected-name",
    "library-browser.preview.views",
    "library-browser.preview.facade",
    "library-browser.preview.schematic",
    "library-browser.implementation",
    "library-browser.sheet",
    "library-browser.action.place",
    "library-browser.action.implementation",
    "library-browser.action.facade",
    "library-browser.action.variants",
    "library-browser.action.open-pcb",
    "library-browser.action.manage-pcb-layouts",
    "library-browser.action.refresh",
    "library-browser.action.libraries",
}

PREVIEW_VIEWS = (
    ("Facade", ("library-browser.preview.facade",)),
    ("Schematic", ("library-browser.preview.schematic",)),
)


def _record(library_root: Path, block_id: str, name: str, description: str):
    block_path = library_root / block_id
    block_path.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        name=name,
        path=block_path,
        implementation=block_path / "implementation.kicad_sch",
        manifest={
            "block_id": block_id,
            "name": name,
            "description": description,
            "slug": block_id,
            "pins": [],
            "parameters": [],
            "variants": [],
            "implementation_file": "implementation.kicad_sch",
        },
    )


def _new_browser(monkeypatch, tmp_path: Path, *, layouts=None):
    plugin_actions._load_tkinter()
    monkeypatch.setattr(plugin_actions, "_create_action_icon", lambda *_args: None)
    library_root = tmp_path / "library"
    library_root.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    records = [
        _record(library_root, "usb-power", "USB power block", "USB power delivery control."),
        _record(library_root, "audio-amplifier", "Audio amplifier", "Small audio output amplifier."),
    ]
    effects: list[str] = []

    monkeypatch.setattr(
        plugin_actions, "configured_library_roots", lambda: [library_root]
    )
    monkeypatch.setattr(
        plugin_actions, "_library_roots_for", lambda _value=None: [library_root]
    )
    monkeypatch.setattr(
        plugin_actions, "_list_library_records",
        lambda _roots=None: (effects.append("refresh") or list(records)),
    )
    monkeypatch.setattr(plugin_actions, "_layout_choices", lambda _record: list(layouts or []))
    monkeypatch.setattr(
        plugin_actions, "_ensure_project_facade_catalog",
        lambda *_args, **_kwargs: project_dir / "project.kicad_sym",
    )
    monkeypatch.setattr(plugin_actions, "_draw_facade_preview", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        plugin_actions, "_native_schematic_preview_pages",
        lambda *_args, **_kwargs: [("Main", None)],
    )
    monkeypatch.setattr(plugin_actions, "_draw_schematic_preview", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin_actions, "_block_overview_text", lambda record: record.name)

    root = plugin_actions.tk.Tk()
    plugin_actions._show_library_browser(
        root,
        schematic=None,
        kicad=object(),
        project_dir=project_dir,
        library_root=[library_root],
        editor_window_id=None,
    )
    root.update_idletasks()
    root.update()
    effects.clear()
    context = UiTestContext(root)
    context.action_calls = effects
    context.refresh_widgets()
    return root, context, records, library_root, project_dir


def _select_first_record(context: UiTestContext, expected_name: str = "USB power block"):
    tree = context.require_widget("library-browser.results")
    row_id = tree.get_children()[0]
    TreeSelectionContract(
        contract_id="library-browser.results",
        row_id=row_id,
        summary_contract_id="library-browser.selected-name",
        expected_summary=expected_name,
    ).run(context)
    return row_id


def test_library_browser_widget_inventory_covers_major_controls(monkeypatch, tmp_path):
    root, _context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        discovered = assert_control_coverage(root, LIBRARY_CONTROLS)
        assert set(discovered) == LIBRARY_CONTROLS
    finally:
        root.destroy()


def test_library_browser_search_filters_records_and_updates_count(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        EntryInputContract(
            contract_id="library-browser.search",
            value="usb",
            results_contract_id="library-browser.results",
            expected_result_count=1,
            summary_contract_id="library-browser.summary",
            expected_summary="1 libraries  ·  1 of 2 blocks",
        ).run(context)
        assert context.require_widget("library-browser.selected-name").cget("text") == "USB power block"
    finally:
        root.destroy()


def test_library_browser_selection_updates_selected_block_details(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        tree = context.require_widget("library-browser.results")
        row_id = tree.get_children()[1]
        TreeSelectionContract(
            contract_id="library-browser.results",
            row_id=row_id,
            summary_contract_id="library-browser.selected-name",
            expected_summary="Audio amplifier",
        ).run(context)
    finally:
        root.destroy()


def test_library_browser_preview_switches_between_facade_and_schematic(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        _select_first_record(context)
        NotebookViewContract(
            contract_id="library-browser.preview.views",
            views=PREVIEW_VIEWS,
        ).run(context)
    finally:
        root.destroy()


def test_library_browser_split_panes_have_visible_geometry(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        PaneGroupContract(
            contract_id="library-browser.panes",
            orientation="horizontal",
            pane_ids=(
                "library-browser.results-pane",
                "library-browser.preview-pane",
            ),
        ).run(context)
    finally:
        root.destroy()


def test_refresh_button_reloads_records_through_real_ui_callback(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        ButtonContract(
            contract_id="library-browser.action.refresh",
            expected_action="refresh",
            destroys_window=False,
        ).run(context)
        assert "2 of 2 blocks" in context.require_widget("library-browser.summary").cget("text")
    finally:
        root.destroy()


def test_place_button_registers_selected_block_and_starts_native_placement(monkeypatch, tmp_path):
    root, context, _records, library_root, _project = _new_browser(monkeypatch, tmp_path)
    context.action_calls.clear()
    monkeypatch.setattr(
        plugin_actions, "_register_block",
        lambda *_args, **_kwargs: context.action_calls.append("register-block"),
    )
    monkeypatch.setattr(plugin_actions, "_LAST_LIBRARY_RELOAD_OK", True)
    monkeypatch.setattr(
        plugin_actions, "_minimize_placement_windows", lambda _window: []
    )
    monkeypatch.setattr(
        plugin_actions, "_start_native_symbol_placement",
        lambda *_args, **_kwargs: context.action_calls.append("start-placement"),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.place",
            expected_action=("register-block", "start-placement"),
            destroys_window=False,
        ).run(context)
    finally:
        root.destroy()


def test_implementation_button_registers_and_opens_default_document(monkeypatch, tmp_path):
    root, context, _records, library_root, _project = _new_browser(monkeypatch, tmp_path)
    context.action_calls.clear()
    monkeypatch.setattr(
        plugin_actions, "_register_implementation_catalog",
        lambda *_args, **_kwargs: context.action_calls.append("register-implementation"),
    )
    monkeypatch.setattr(
        plugin_actions, "_launch_block_asset",
        lambda *_args, **_kwargs: context.action_calls.append("open-implementation"),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.implementation",
            expected_action=("register-implementation", "open-implementation"),
            destroys_window=False,
        ).run(context)
    finally:
        root.destroy()


def test_edit_facade_button_targets_selected_record(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    context.action_calls.clear()
    monkeypatch.setattr(
        plugin_actions, "_edit_facade_symbol",
        lambda record, *_args, **_kwargs: context.action_calls.append(record.manifest["block_id"]),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.facade",
            expected_action="usb-power",
            destroys_window=False,
        ).run(context)
    finally:
        root.destroy()


def test_open_pcb_button_creates_chooser_for_selected_block(monkeypatch, tmp_path):
    layouts = [
        {"id": "default-pcb", "name": "Default", "file": "default.kicad_pcb"}
    ]
    root, context, _records, _library, _project = _new_browser(
        monkeypatch, tmp_path, layouts=layouts
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.open-pcb",
            expected_action=None,
            destroys_window=False,
            expected_new_window_title="Open PCB layout — USB power block",
        ).run(context)
        dialogs = [
            child
            for child in root.winfo_children()
            if isinstance(child, plugin_actions.tk.Toplevel)
            and child.title() == "Open PCB layout — USB power block"
        ]
        assert len(dialogs) == 1
        def walk_widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from walk_widgets(child)

        combos = [
            item
            for item in walk_widgets(dialogs[0])
            if item.winfo_class() == "TCombobox"
        ]
        assert len(combos) == 1
        assert combos[0].get() == "Default [default-pcb]"
        assert tuple(combos[0]["values"]) == ("Default [default-pcb]",)
    finally:
        root.destroy()
