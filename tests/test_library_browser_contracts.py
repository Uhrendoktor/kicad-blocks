"""Reusable interaction contracts for the production Functional Block Library."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from ui_contracts import (
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

CATALOG_PATH = ROOT / "tests" / "ui_contract_catalog.json"
LIBRARY_SCREEN = next(
    screen
    for screen in json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["screens"]
    if screen["id"] == "functional-block-library"
)
LIBRARY_CONTROLS = {
    control["contract_id"] for control in LIBRARY_SCREEN["controls"]
}
LIBRARY_ASSERTED_BUTTONS = {
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
            "symbol_name": f"{block_id}_facade",
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


def test_library_browser_catalog_labels_each_toolbar_action_coverage_level():
    controls = {
        control["contract_id"]: control
        for control in LIBRARY_SCREEN["controls"]
    }
    asserted_buttons = {
        contract_id
        for contract_id, control in controls.items()
        if control["type"] == "button" and control["assertion"] == "asserted"
    }
    assert asserted_buttons == LIBRARY_ASSERTED_BUTTONS
    assert all(
        control["assertion"] in {"asserted", "observed", "uncovered"}
        for control in controls.values()
    )
    assert all(
        controls[contract_id]["assertion"] == "asserted"
        for contract_id in (
            "library-browser.action.variants",
            "library-browser.action.manage-pcb-layouts",
            "library-browser.action.libraries",
            "library-browser.implementation",
            "library-browser.sheet",
        )
    )


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
            expected_label="Refresh",
        ).run(context)
        assert "2 of 2 blocks" in context.require_widget("library-browser.summary").cget("text")
    finally:
        root.destroy()


def test_place_button_registers_selected_block_and_starts_native_placement(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
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
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **_kwargs: context.events.append(
            f"unexpected error dialog: {title}: {message}"
        ),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.place",
            expected_action=("register-block", "start-placement"),
            destroys_window=False,
            expected_label="Place block",
        ).run(context)
        assert context.events == []
    finally:
        root.destroy()


def test_implementation_button_registers_and_opens_default_document(monkeypatch, tmp_path):
    root, context, _records, _library_root, _project = _new_browser(monkeypatch, tmp_path)
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
            expected_label="Implementation",
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
            expected_label="Edit facade",
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
            expected_label="Open PCB",
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



def _show_schematic_preview(root, context):
    notebook = context.require_widget("library-browser.preview.views")
    notebook.select(notebook.tabs()[1])
    root.update_idletasks()
    root.update()


def test_implementation_selector_renders_the_selected_variant(monkeypatch, tmp_path):
    root, context, records, _library, _project = _new_browser(monkeypatch, tmp_path)
    records[0].manifest["variants"] = [
        {"value": "1V8", "file": "implementation_1v8.kicad_sch"}
    ]
    rendered_implementations = []

    def render_pages(_record, selector, **_kwargs):
        rendered_implementations.append(selector)
        return [("Main", None), ("Power Stage", None)]

    monkeypatch.setattr(
        plugin_actions, "_native_schematic_preview_pages", render_pages
    )
    monkeypatch.setattr(
        plugin_actions, "_draw_schematic_preview", lambda *_args, **_kwargs: None
    )
    try:
        _select_first_record(context)
        _show_schematic_preview(root, context)
        selector = context.require_widget("library-browser.implementation")
        assert tuple(selector["values"]) == ("Default", "1V8")
        rendered_implementations.clear()
        selector.bind(
            "<<ComboboxSelected>>",
            lambda _event: context.events.append("implementation:1V8"),
            add="+",
        )
        ChoiceViewContract(
            contract_id="library-browser.implementation",
            value="1V8",
            expected_event="implementation:1V8",
        ).run(context)
        assert rendered_implementations[-1] == "1V8"
        assert context.require_widget("library-browser.sheet").get() == "Main"
    finally:
        root.destroy()


def test_sheet_selector_draws_the_selected_schematic_page(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    drawn_pages = []
    monkeypatch.setattr(
        plugin_actions, "_native_schematic_preview_pages",
        lambda *_args, **_kwargs: [("Main", None), ("Power Stage", None)],
    )
    monkeypatch.setattr(
        plugin_actions,
        "_draw_schematic_preview",
        lambda _canvas, _pages, selected: drawn_pages.append(selected),
    )
    try:
        _select_first_record(context)
        _show_schematic_preview(root, context)
        selector = context.require_widget("library-browser.sheet")
        assert tuple(selector["values"]) == ("Main", "Power Stage")
        assert selector.winfo_manager() == "pack"
        drawn_pages.clear()
        selector.bind(
            "<<ComboboxSelected>>",
            lambda _event: context.events.append("sheet:Power Stage"),
            add="+",
        )
        ChoiceViewContract(
            contract_id="library-browser.sheet",
            value="Power Stage",
            expected_event="sheet:Power Stage",
        ).run(context)
        assert drawn_pages[-1] == "Power Stage"
    finally:
        root.destroy()


def test_variants_toolbar_opens_manager_and_refreshes_records(
    monkeypatch, tmp_path
):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    context.action_calls.clear()
    monkeypatch.setattr(
        plugin_actions, "_show_block_variant_editor",
        lambda *_args, **_kwargs: context.action_calls.append("open-variants"),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.variants",
            expected_action=("open-variants", "refresh"),
            destroys_window=False,
            expected_label="Variants",
        ).run(context)
        assert "2 of 2 blocks" in context.require_widget(
            "library-browser.summary"
        ).getvar(context.require_widget("library-browser.summary").cget("textvariable"))
    finally:
        root.destroy()


def test_pcb_layouts_toolbar_opens_manager_and_refreshes_records(
    monkeypatch, tmp_path
):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    context.action_calls.clear()
    monkeypatch.setattr(
        plugin_actions, "_show_block_layout_editor",
        lambda *_args, **_kwargs: context.action_calls.append("open-pcb-layouts"),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.manage-pcb-layouts",
            expected_action=("open-pcb-layouts", "refresh"),
            destroys_window=False,
            expected_label="PCB layouts",
        ).run(context)
    finally:
        root.destroy()


def test_libraries_toolbar_opens_library_roots_manager_and_refreshes(
    monkeypatch, tmp_path
):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    context.action_calls.clear()
    monkeypatch.setattr(
        plugin_actions, "_show_library_roots_dialog",
        lambda *_args, **_kwargs: context.action_calls.append("open-libraries"),
    )
    try:
        ButtonContract(
            contract_id="library-browser.action.libraries",
            expected_action=("open-libraries", "refresh"),
            destroys_window=False,
            expected_label="Libraries",
        ).run(context)
    finally:
        root.destroy()



IMPLEMENTATION_CHOOSER_CONTROLS = {
    "open-implementation-chooser.window",
    "open-implementation-chooser.choice",
    "open-implementation-chooser.action.open",
    "open-implementation-chooser.action.cancel",
}

PCB_CHOOSER_CONTROLS = {
    "open-pcb-layout-chooser.window",
    "open-pcb-layout-chooser.choice",
    "open-pcb-layout-chooser.action.open",
    "open-pcb-layout-chooser.action.cancel",
    "open-pcb-layout-chooser.action.manage",
}


def _find_toplevel(root, title: str):
    return next(
        child
        for child in root.winfo_children()
        if child.winfo_class() == "Toplevel" and child.title() == title
    )


def test_open_implementation_chooser_opens_the_selected_named_variant(
    monkeypatch, tmp_path
):
    root, context, records, _library, _project = _new_browser(
        monkeypatch, tmp_path
    )
    records[0].manifest["variants"] = [
        {"value": "1V8", "file": "implementation_1v8.kicad_sch"}
    ]
    effects = []
    monkeypatch.setattr(
        plugin_actions, "_register_implementation_catalog",
        lambda *_args, **_kwargs: effects.append("registered"),
    )
    monkeypatch.setattr(
        plugin_actions, "_launch_block_asset",
        lambda _kicad, asset, *_args, **_kwargs: effects.append(Path(asset).name),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.implementation",
            expected_action="registered",
            destroys_window=False,
            expected_label="Implementation",
            expected_new_window_title="Open implementation — USB power block",
        ).run(context)
        chooser = _find_toplevel(
            root, "Open implementation — USB power block"
        )
        assert_control_coverage(chooser, IMPLEMENTATION_CHOOSER_CONTROLS)
        WindowContract(
            title="Open implementation — USB power block",
            screen_id="open-implementation-chooser",
            required_widget_ids=tuple(IMPLEMENTATION_CHOOSER_CONTROLS),
        ).run(UiTestContext(chooser))
        context.refresh_widgets()
        combo = context.require_widget("open-implementation-chooser.choice")
        assert tuple(combo["values"]) == (
            "Default — implementation.kicad_sch",
            "1V8",
        )
        combo.set("1V8")
        effects.clear()
        ButtonContract(
            contract_id="open-implementation-chooser.action.open",
            expected_action="implementation_1v8.kicad_sch",
            destroys_window=False,
            expected_label="Open",
            expected_closed_window_title="Open implementation — USB power block",
        ).run(context)
    finally:
        root.destroy()


def test_open_implementation_chooser_cancel_closes_without_opening(
    monkeypatch, tmp_path
):
    root, context, records, _library, _project = _new_browser(
        monkeypatch, tmp_path
    )
    records[0].manifest["variants"] = [
        {"value": "1V8", "file": "implementation_1v8.kicad_sch"}
    ]
    effects = []
    monkeypatch.setattr(
        plugin_actions, "_register_implementation_catalog",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        plugin_actions, "_launch_block_asset",
        lambda *_args, **_kwargs: effects.append("unexpected-open"),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.implementation",
            expected_action=None,
            destroys_window=False,
            expected_label="Implementation",
            expected_new_window_title="Open implementation — USB power block",
        ).run(context)
        chooser = _find_toplevel(
            root, "Open implementation — USB power block"
        )
        assert_control_coverage(chooser, IMPLEMENTATION_CHOOSER_CONTROLS)
        context.refresh_widgets()
        ButtonContract(
            contract_id="open-implementation-chooser.action.cancel",
            expected_action=None,
            destroys_window=False,
            expected_label="Cancel",
            expected_closed_window_title="Open implementation — USB power block",
        ).run(context)
        assert effects == []
    finally:
        root.destroy()


def test_open_pcb_chooser_opens_the_selected_layout(monkeypatch, tmp_path):
    layouts = [
        {"id": "default-pcb", "name": "Default", "file": "default.kicad_pcb"},
        {"id": "aux-pcb", "name": "Auxiliary", "file": "auxiliary.kicad_pcb"},
    ]
    root, context, _records, _library, _project = _new_browser(
        monkeypatch, tmp_path, layouts=layouts
    )
    effects = []
    monkeypatch.setattr(
        plugin_actions, "_launch_block_asset",
        lambda _kicad, asset, *_args, **_kwargs: effects.append(Path(asset).name),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.open-pcb",
            expected_action=None,
            destroys_window=False,
            expected_label="Open PCB",
            expected_new_window_title="Open PCB layout — USB power block",
        ).run(context)
        chooser = _find_toplevel(root, "Open PCB layout — USB power block")
        assert_control_coverage(chooser, PCB_CHOOSER_CONTROLS)
        WindowContract(
            title="Open PCB layout — USB power block",
            screen_id="open-pcb-layout-chooser",
            required_widget_ids=tuple(PCB_CHOOSER_CONTROLS),
        ).run(UiTestContext(chooser))
        context.refresh_widgets()
        combo = context.require_widget("open-pcb-layout-chooser.choice")
        assert tuple(combo["values"]) == (
            "Default [default-pcb]",
            "Auxiliary [aux-pcb]",
        )
        combo.set("Auxiliary [aux-pcb]")
        ButtonContract(
            contract_id="open-pcb-layout-chooser.action.open",
            expected_action="auxiliary.kicad_pcb",
            destroys_window=False,
            expected_label="Open",
            expected_closed_window_title="Open PCB layout — USB power block",
        ).run(context)
    finally:
        root.destroy()


def test_open_pcb_chooser_cancel_closes_without_opening(monkeypatch, tmp_path):
    layouts = [
        {"id": "default-pcb", "name": "Default", "file": "default.kicad_pcb"}
    ]
    root, context, _records, _library, _project = _new_browser(
        monkeypatch, tmp_path, layouts=layouts
    )
    effects = []
    monkeypatch.setattr(
        plugin_actions, "_launch_block_asset",
        lambda *_args, **_kwargs: effects.append("unexpected-open"),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.open-pcb",
            expected_action=None,
            destroys_window=False,
            expected_label="Open PCB",
            expected_new_window_title="Open PCB layout — USB power block",
        ).run(context)
        chooser = _find_toplevel(root, "Open PCB layout — USB power block")
        assert_control_coverage(chooser, PCB_CHOOSER_CONTROLS)
        context.refresh_widgets()
        ButtonContract(
            contract_id="open-pcb-layout-chooser.action.cancel",
            expected_action=None,
            destroys_window=False,
            expected_label="Cancel",
            expected_closed_window_title="Open PCB layout — USB power block",
        ).run(context)
        assert effects == []
    finally:
        root.destroy()


def test_open_pcb_chooser_manage_layouts_opens_manager_and_refreshes(
    monkeypatch, tmp_path
):
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
            expected_label="Open PCB",
            expected_new_window_title="Open PCB layout — USB power block",
        ).run(context)
        chooser = _find_toplevel(root, "Open PCB layout — USB power block")
        assert_control_coverage(chooser, PCB_CHOOSER_CONTROLS)
        context.action_calls.clear()
        monkeypatch.setattr(
            plugin_actions, "_show_block_layout_editor",
            lambda *_args, **_kwargs: context.action_calls.append("manage-layouts"),
        )
        context.refresh_widgets()
        ButtonContract(
            contract_id="open-pcb-layout-chooser.action.manage",
            expected_action=("manage-layouts", "refresh"),
            destroys_window=False,
            expected_label="Manage layouts…",
            expected_closed_window_title="Open PCB layout — USB power block",
        ).run(context)
    finally:
        root.destroy()
