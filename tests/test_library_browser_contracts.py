"""Reusable interaction contracts for the production Functional Block Library."""

from __future__ import annotations

import json
import os
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

from ui_contracts import (
    ChoiceViewContract,
    EntryInputContract,
    NotebookViewContract,
    PaneGroupContract,
    TreeSelectionContract,
    UiTestContext,
    WindowContract,
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
    "library-browser.action.publish",
    "library-browser.action.variants",
    "library-browser.action.open-pcb",
    "library-browser.action.manage-pcb-layouts",
    "library-browser.action.refresh",
    "library-browser.action.libraries",
}

PREVIEW_VIEWS = (
    ("Facade", ("library-browser.preview.facade",)),
    ("Schematic", ("library-browser.preview.schematic",)),
    ("PCB", ("library-browser.preview.pcb",)),
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
    monkeypatch.setattr(plugin_actions, "_draw_pcb_preview", lambda *_args, **_kwargs: "Test PCB")
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
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        discovered = assert_control_coverage(root, LIBRARY_CONTROLS)
        assert set(discovered) == LIBRARY_CONTROLS
        assert context.require_widget("library-browser.results").cget("height") == 2
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
        assert context.require_widget("library-browser.action.publish").cget("state").__str__() == "normal"
    finally:
        root.destroy()


def test_library_browser_publish_button_publishes_selected_record_and_reports_inline(
    monkeypatch, tmp_path
):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    _select_first_record(context)
    calls = []
    monkeypatch.setattr(
        plugin_actions,
        "_publish_record",
        lambda _roots, record, _kicad, _project, schematic: (
            calls.append((record.manifest["block_id"], schematic))
            or {
                "record": record,
                "refresh": {"version": 7},
                "instances": {"updated": ["X1"], "topology_skipped": ["X2"]},
                "sync_error": None,
            }
        ),
    )
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showinfo",
        lambda *_args, **_kwargs: pytest.fail("Success must not be a popup."),
    )
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showwarning",
        lambda *_args, **_kwargs: pytest.fail("Publish status must not be a popup."),
    )
    try:
        ButtonContract(
            contract_id="library-browser.action.publish",
            expected_action="refresh",
            destroys_window=False,
            expected_label="Publish",
        ).run(context)
        status = context.require_widget("library-browser.status")
        variable = str(status.cget("textvariable"))
        message = status.getvar(variable)
        assert "Published USB power block" in message
        assert "revision 7" in message
        assert "Updated 1 placed instance" in message
        assert "X2" in message and "explicit pin migration" in message
        assert calls == [("usb-power", None)]
    finally:
        root.destroy()


def test_publish_button_is_disabled_when_browser_has_no_matching_records(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        EntryInputContract(
            contract_id="library-browser.search",
            value="no-such-block",
            results_contract_id="library-browser.results",
            expected_result_count=0,
            summary_contract_id="library-browser.summary",
            expected_summary="1 libraries  ·  0 of 2 blocks",
        ).run(context)
        assert context.require_widget("library-browser.action.publish").cget("state").__str__() == "disabled"
    finally:
        root.destroy()


def test_library_browser_disables_block_actions_when_filter_has_no_results(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        EntryInputContract(
            contract_id="library-browser.search",
            value="no-such-block",
            results_contract_id="library-browser.results",
            expected_result_count=0,
            summary_contract_id="library-browser.summary",
            expected_summary="1 libraries  ·  0 of 2 blocks",
        ).run(context)
        for contract_id in (
            "library-browser.action.place",
            "library-browser.action.implementation",
            "library-browser.action.facade",
            "library-browser.action.publish",
            "library-browser.action.variants",
            "library-browser.action.manage-pcb-layouts",
        ):
            assert context.require_widget(contract_id).cget("state").__str__() == "disabled"
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



def test_facade_schematic_and_pcb_previews_support_zoom_and_fit_reset(
    monkeypatch, tmp_path
):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        canvases = []

        def collect(widget):
            if getattr(widget, "_kicad_preview_state", None) is not None:
                canvases.append(widget)
            for child in widget.winfo_children():
                collect(child)

        collect(root)
        assert len(canvases) == 3

        buffer = BytesIO()
        Image.new("RGB", (1600, 1200), "white").save(buffer, format="PNG")
        png_data = buffer.getvalue()
        notebook = context.require_widget("library-browser.preview.views")

        for index, canvas in enumerate(canvases):
            notebook.select(notebook.tabs()[index])
            root.update_idletasks()
            root.update()
            assert canvas.winfo_width() > 10
            assert canvas.winfo_height() > 10

            canvas.set_preview_image(png_data)
            root.update_idletasks()
            root.update()
            assert canvas._kicad_preview_state["zoom"] == 1.0

            canvas.event_generate(
                "<MouseWheel>",
                delta=120,
                x=canvas.winfo_width() // 2,
                y=canvas.winfo_height() // 2,
            )
            root.update_idletasks()
            root.update()
            assert canvas._kicad_preview_state["zoom"] > 1.0
            assert canvas.find_withtag("preview-image")

            canvas.event_generate(
                "<Double-Button-1>",
                x=canvas.winfo_width() // 2,
                y=canvas.winfo_height() // 2,
            )
            root.update_idletasks()
            root.update()
            assert canvas._kicad_preview_state["zoom"] == 1.0

            assert canvas.bind("<ButtonPress-1>")
            assert canvas.bind("<B1-Motion>")
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


def test_library_browser_preview_and_overview_heights_are_resizable(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    try:
        PaneGroupContract(
            contract_id="library-browser.preview-stack",
            orientation="vertical",
            pane_ids=(
                "library-browser.preview-area",
                "library-browser.overview-pane",
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


def test_publish_button_publishes_the_selected_record_without_a_picker(monkeypatch, tmp_path):
    root, context, records, _library, _project = _new_browser(monkeypatch, tmp_path)
    outcome = {
        "record": records[0],
        "refresh": {"version": 4, "changed": ["implementation.kicad_sch"]},
        "instances": {"updated": ["X1"], "topology_skipped": []},
        "sync_error": None,
    }
    monkeypatch.setattr(
        plugin_actions,
        "_publish_record",
        lambda *args: (context.action_calls.append(("publish", args[1].manifest["block_id"])) or outcome),
    )
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showinfo",
        lambda *_args, **_kwargs: pytest.fail("Publish success must not use a popup."),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.publish",
            expected_action=(("publish", "usb-power"), "refresh"),
            destroys_window=False,
            expected_label="Publish",
        ).run(context)
        status = context.require_widget("library-browser.status")
        variable = str(status.cget("textvariable"))
        assert "Published USB power block" in status.getvar(variable)
        assert "Updated 1 placed instance" in status.getvar(variable)
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


def test_facade_editor_exposes_geometry_fields_and_saves_immediately(monkeypatch, tmp_path):
    from kicad_blocks.model import parse_pins
    from kicad_blocks.repository import (
        build_project_symbol_catalog,
        create_block,
    )

    plugin_actions._load_tkinter()
    monkeypatch.setattr(plugin_actions, "_create_action_icon", lambda *_args: None)
    library_root = tmp_path / "facade-library"
    project_dir = tmp_path / "facade-project"
    library_root.mkdir()
    project_dir.mkdir()
    record = create_block(
        library_root,
        "Facade layout test",
        parse_pins("VIN:input, GND:passive, VOUT:output"),
    )
    project_catalog = build_project_symbol_catalog(project_dir, [library_root])
    kicad = SimpleNamespace()
    reload_library = []
    monkeypatch.setattr(
        plugin_actions,
        "_reload_symbol_library",
        lambda actual_kicad, nickname: reload_library.append((actual_kicad, nickname)) or True,
    )

    owner = plugin_actions.tk.Tk()
    try:
        assert plugin_actions._edit_facade_symbol(
            record,
            kicad,
            project_catalog=project_catalog,
            parent=owner,
        )
        owner.update_idletasks()
        owner.update()
        dialog = next(
            child for child in owner.winfo_children()
            if child.winfo_class() == "Toplevel"
            and child.title() == f"Facade pin layout — {record.name}"
        )
        context = UiTestContext(dialog)
        WindowContract(
            title=f"Facade pin layout — {record.name}",
            screen_id="facade-editor",
            required_widget_ids=(
                "facade-editor.pins",
                "facade-editor.x",
                "facade-editor.y",
                "facade-editor.angle",
                "facade-editor.length",
                "facade-editor.status",
                "facade-editor.action.apply",
                "facade-editor.action.close",
            ),
        ).run(context)

        table = context.require_widget("facade-editor.pins")
        selected = table.get_children()[0]
        table.selection_set(selected)
        table.event_generate("<<TreeviewSelect>>")
        dialog.update()
        x_entry = context.require_widget("facade-editor.x")
        y_entry = context.require_widget("facade-editor.y")
        angle_entry = context.require_widget("facade-editor.angle")
        length_entry = context.require_widget("facade-editor.length")
        for entry, value in (
            (x_entry, "25.4"),
            (y_entry, "17.78"),
            (angle_entry, "180"),
            (length_entry, "5.08"),
        ):
            entry.delete(0, "end")
            entry.insert(0, value)

        ButtonContract(
            contract_id="facade-editor.action.apply",
            expected_action=None,
            destroys_window=False,
            expected_label="Apply pin geometry",
        ).run(context)

        status = context.require_widget("facade-editor.status")
        status_var = str(status.cget("textvariable"))
        assert "Saved" in status.getvar(status_var)
        assert reload_library == [(kicad, plugin_actions.LIBRARY_NICKNAME)]
        catalog_text = project_catalog.read_text(encoding="utf-8")
        assert "(at 25.4 17.78 180)" in catalog_text
        assert "(length 5.08)" in catalog_text
        assert record.symbol_library.read_bytes() != project_catalog.read_bytes() or (
            record.symbol_library.read_text(encoding="utf-8")
            != catalog_text
        )
        ButtonContract(
            contract_id="facade-editor.action.close",
            expected_action=None,
            destroys_window=True,
            expected_label="Close",
        ).run(context)
    finally:
        owner.destroy()


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
    monkeypatch.setattr(
        plugin_actions, "_register_implementation_catalog",
        lambda *_args, **_kwargs: context.action_calls.append("registered"),
    )
    monkeypatch.setattr(
        plugin_actions, "_launch_block_asset",
        lambda _kicad, asset, *_args, **_kwargs: context.action_calls.append(Path(asset).name),
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
        context.action_calls.clear()
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
    monkeypatch.setattr(
        plugin_actions, "_launch_block_asset",
        lambda _kicad, asset, *_args, **_kwargs: context.action_calls.append(Path(asset).name),
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



def test_library_browser_refresh_error_keeps_existing_records(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda *_args, **_kwargs: pytest.fail("Refresh failures must be shown inline."),
    )

    def fail_refresh(_roots=None):
        context.action_calls.append("refresh-error")
        raise OSError("library folder is temporarily unavailable")

    monkeypatch.setattr(plugin_actions, "_list_library_records", fail_refresh)
    try:
        ButtonContract(
            contract_id="library-browser.action.refresh",
            expected_action="refresh-error",
            destroys_window=False,
            expected_label="Refresh",
        ).run(context)
        status = context.require_widget("library-browser.status")
        variable = str(status.cget("textvariable"))
        assert "Could not refresh registered libraries" in status.getvar(variable)
        assert "library folder is temporarily unavailable" in status.getvar(variable)
        assert context.require_widget("library-browser.results").get_children()
        assert "2 of 2 blocks" in context.require_widget("library-browser.summary").getvar(
            context.require_widget("library-browser.summary").cget("textvariable")
        )
    finally:
        root.destroy()


def test_place_block_reload_failure_uses_inline_status_without_popup(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    placements = []
    monkeypatch.setattr(
        plugin_actions, "_register_block",
        lambda *_args, **_kwargs: context.action_calls.append("registered"),
    )
    monkeypatch.setattr(plugin_actions, "_LAST_LIBRARY_RELOAD_OK", False)
    monkeypatch.setattr(
        plugin_actions,
        "_LAST_LIBRARY_RELOAD_WARNING",
        "Restart Eeschema to reload the project symbol table.",
    )
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showwarning",
        lambda *_args, **_kwargs: pytest.fail("Placement feedback must not use a popup."),
    )
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showinfo",
        lambda *_args, **_kwargs: pytest.fail("Placement success must not use a popup."),
    )
    monkeypatch.setattr(
        plugin_actions, "_start_native_symbol_placement",
        lambda *_args, **_kwargs: placements.append(True),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.place",
            expected_action="registered",
            destroys_window=False,
            expected_label="Place block",
        ).run(context)
        status = context.require_widget("library-browser.status")
        variable = str(status.cget("textvariable"))
        assert status.getvar(variable) == "Restart Eeschema to reload the project symbol table."
        assert placements == []
    finally:
        root.destroy()


def test_place_button_reports_ready_state_without_success_popup(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    monkeypatch.setattr(
        plugin_actions, "_register_block",
        lambda *_args, **_kwargs: context.action_calls.append("registered"),
    )
    monkeypatch.setattr(plugin_actions, "_LAST_LIBRARY_RELOAD_OK", True)
    monkeypatch.setattr(plugin_actions, "_minimize_placement_windows", lambda _window: [])
    monkeypatch.setattr(
        plugin_actions, "_start_native_symbol_placement",
        lambda *_args, **_kwargs: (context.action_calls.append("placement-started") or object(), False),
    )
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showinfo",
        lambda *_args, **_kwargs: pytest.fail("Placement success must not use a popup."),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.place",
            expected_action=("registered", "placement-started"),
            destroys_window=False,
            expected_label="Place block",
        ).run(context)
        status = context.require_widget("library-browser.status")
        variable = str(status.cget("textvariable"))
        assert "active sheet center" in status.getvar(variable)
        assert "selected it in Eeschema" in status.getvar(variable)
    finally:
        root.destroy()


def test_place_block_registration_failure_uses_inline_status_and_keeps_browser_open(
    monkeypatch, tmp_path
):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)

    def fail_register(*_args, **_kwargs):
        context.action_calls.append("registration-failed")
        raise RuntimeError("project library table could not be written")

    monkeypatch.setattr(plugin_actions, "_register_block", fail_register)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda *_args, **_kwargs: pytest.fail("Recoverable placement failures must be inline."),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.place",
            expected_action="registration-failed",
            destroys_window=False,
            expected_label="Place block",
        ).run(context)
        status = context.require_widget("library-browser.status")
        variable = str(status.cget("textvariable"))
        assert "Placement could not complete" in status.getvar(variable)
        assert "project library table could not be written" in status.getvar(variable)
        assert _select_first_record(context) is not None
    finally:
        root.destroy()


def test_open_implementation_registration_failure_shows_error(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    errors = []

    def fail_register(*_args, **_kwargs):
        context.action_calls.append("implementation-registration-failed")
        raise RuntimeError("implementation catalog write failed")

    monkeypatch.setattr(plugin_actions, "_register_implementation_catalog", fail_register)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append(
            (title, str(message), kwargs.get("parent"))
        ),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.implementation",
            expected_action="implementation-registration-failed",
            destroys_window=False,
            expected_label="Implementation",
        ).run(context)
        assert len(errors) == 1
        assert errors[0][0] == "Open implementation"
        assert errors[0][1] == "implementation catalog write failed"
        assert errors[0][2] is not None and errors[0][2].winfo_exists()
        assert not any(
            child.winfo_class() == "Toplevel"
            and child.title().startswith("Open implementation — ")
            for child in root.winfo_children()
        )
    finally:
        root.destroy()


def test_open_default_implementation_launch_failure_shows_error(
    monkeypatch, tmp_path
):
    root, context, records, _library, _project = _new_browser(monkeypatch, tmp_path)
    errors = []
    records[0].manifest["variants"] = []
    monkeypatch.setattr(
        plugin_actions,
        "_register_implementation_catalog",
        lambda *_args, **_kwargs: context.action_calls.append("registered"),
    )

    def fail_launch(*_args, **_kwargs):
        context.action_calls.append("launch-default-failed")
        raise OSError("Eeschema executable is unavailable")

    monkeypatch.setattr(plugin_actions, "_launch_block_asset", fail_launch)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append(
            (title, str(message), kwargs.get("parent"))
        ),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.implementation",
            expected_action=("registered", "launch-default-failed"),
            destroys_window=False,
            expected_label="Implementation",
        ).run(context)
        assert len(errors) == 1
        assert errors[0][0] == "Open implementation"
        assert errors[0][1] == "Eeschema executable is unavailable"
        assert errors[0][2] is not None and errors[0][2].winfo_exists()
    finally:
        root.destroy()


def test_open_named_implementation_launch_failure_shows_error_after_chooser(
    monkeypatch, tmp_path
):
    root, context, records, _library, _project = _new_browser(monkeypatch, tmp_path)
    records[0].manifest["variants"] = [
        {"value": "1V8", "file": "implementation_1v8.kicad_sch"}
    ]
    errors = []
    monkeypatch.setattr(
        plugin_actions,
        "_register_implementation_catalog",
        lambda *_args, **_kwargs: context.action_calls.append("registered"),
    )

    def fail_launch(*_args, **_kwargs):
        context.action_calls.append("launch-named-failed")
        raise OSError("named implementation file is missing")

    monkeypatch.setattr(plugin_actions, "_launch_block_asset", fail_launch)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append(
            (title, str(message), kwargs.get("parent"))
        ),
    )
    chooser_title = "Open implementation — USB power block"
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.implementation",
            expected_action="registered",
            destroys_window=False,
            expected_label="Implementation",
            expected_new_window_title=chooser_title,
        ).run(context)
        chooser = _find_toplevel(root, chooser_title)
        combo = next(
            widget
            for widget in chooser.winfo_children()[0].winfo_children()
            if widget.winfo_class() == "TCombobox"
        )
        combo.set("1V8")
        context.action_calls.clear()
        context.refresh_widgets()
        ButtonContract(
            contract_id="open-implementation-chooser.action.open",
            expected_action="launch-named-failed",
            destroys_window=False,
            expected_label="Open",
            expected_closed_window_title=chooser_title,
        ).run(context)
        assert len(errors) == 1
        assert errors[0][0] == "Open implementation"
        assert errors[0][1] == "named implementation file is missing"
        assert errors[0][2] is not None and errors[0][2].winfo_exists()
        assert not _find_dialog_exists(root, chooser_title)
    finally:
        root.destroy()


def _find_dialog_exists(root, title: str) -> bool:
    return any(
        child.winfo_class() == "Toplevel" and child.title() == title
        for child in root.winfo_children()
    )


def test_edit_facade_failure_shows_recoverable_error(monkeypatch, tmp_path):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    errors = []

    def fail_edit(*_args, **_kwargs):
        context.action_calls.append("facade-edit-failed")
        raise RuntimeError("KiCad Symbol Editor IPC cannot select this library entry")

    monkeypatch.setattr(plugin_actions, "_edit_facade_symbol", fail_edit)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append(
            (title, str(message), kwargs.get("parent"))
        ),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.facade",
            expected_action="facade-edit-failed",
            destroys_window=False,
            expected_label="Edit facade",
        ).run(context)
        assert len(errors) == 1
        assert errors[0][0] == "Edit facade"
        assert "cannot select this library entry" in errors[0][1]
        assert errors[0][2] is not None and errors[0][2].winfo_exists()
    finally:
        root.destroy()


def test_manage_pcb_layouts_failure_shows_error_and_preserves_browser(
    monkeypatch, tmp_path
):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    errors = []

    def fail_manage(*_args, **_kwargs):
        context.action_calls.append("manage-layouts-failed")
        raise RuntimeError("PCB layout manifest is invalid")

    monkeypatch.setattr(plugin_actions, "_show_block_layout_editor", fail_manage)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append(
            (title, str(message), kwargs.get("parent"))
        ),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.manage-pcb-layouts",
            expected_action="manage-layouts-failed",
            destroys_window=False,
            expected_label="PCB layouts",
        ).run(context)
        assert len(errors) == 1
        assert errors[0][0] == "Manage PCB layouts"
        assert errors[0][1] == "PCB layout manifest is invalid"
        assert errors[0][2] is not None and errors[0][2].winfo_exists()
    finally:
        root.destroy()


def test_manage_implementations_failure_shows_error_and_preserves_browser(
    monkeypatch, tmp_path
):
    root, context, _records, _library, _project = _new_browser(monkeypatch, tmp_path)
    errors = []

    def fail_manage(*_args, **_kwargs):
        context.action_calls.append("manage-implementations-failed")
        raise RuntimeError("implementation manifest cannot be opened")

    monkeypatch.setattr(plugin_actions, "_show_block_variant_editor", fail_manage)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append(
            (title, str(message), kwargs.get("parent"))
        ),
    )
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.variants",
            expected_action="manage-implementations-failed",
            destroys_window=False,
            expected_label="Variants",
        ).run(context)
        assert len(errors) == 1
        assert errors[0][0] == "Manage implementations"
        assert errors[0][1] == "implementation manifest cannot be opened"
        assert errors[0][2] is not None and errors[0][2].winfo_exists()
    finally:
        root.destroy()


def test_open_pcb_layout_launch_failure_shows_error_after_chooser(
    monkeypatch, tmp_path
):
    layouts = [
        {"id": "default-pcb", "name": "Default", "file": "default.kicad_pcb"}
    ]
    root, context, _records, _library, _project = _new_browser(
        monkeypatch, tmp_path, layouts=layouts
    )
    errors = []

    def fail_launch(*_args, **_kwargs):
        context.action_calls.append("open-layout-failed")
        raise OSError("PCB Editor could not open the selected layout")

    monkeypatch.setattr(plugin_actions, "_launch_block_asset", fail_launch)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append(
            (title, str(message), kwargs.get("parent"))
        ),
    )
    chooser_title = "Open PCB layout — USB power block"
    try:
        _select_first_record(context)
        ButtonContract(
            contract_id="library-browser.action.open-pcb",
            expected_action=None,
            destroys_window=False,
            expected_label="Open PCB",
            expected_new_window_title=chooser_title,
        ).run(context)
        chooser = _find_toplevel(root, chooser_title)
        combo = next(
            widget
            for widget in chooser.winfo_children()[0].winfo_children()
            if widget.winfo_class() == "TCombobox"
        )
        combo.set("Default [default-pcb]")
        context.refresh_widgets()
        ButtonContract(
            contract_id="open-pcb-layout-chooser.action.open",
            expected_action="open-layout-failed",
            destroys_window=False,
            expected_label="Open",
            expected_closed_window_title=chooser_title,
        ).run(context)
        assert len(errors) == 1
        assert errors[0][0] == "Open PCB layout"
        assert errors[0][1] == "PCB Editor could not open the selected layout"
        assert errors[0][2] is not None and errors[0][2].winfo_exists()
        assert not _find_dialog_exists(root, chooser_title)
    finally:
        root.destroy()
