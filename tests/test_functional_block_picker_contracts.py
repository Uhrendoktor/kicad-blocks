"""Real Tk interaction contracts for the Functional Block placement chooser."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_parameters, parse_pins
from kicad_blocks.repository import BlockRecord, create_block
from ui_contracts import (
    ButtonContract,
    Canvas,
    Misc,
    MonkeyPatch,
    Tk,
    Toplevel,
    UiTestContext,
    assert_control_coverage,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="Real Tk interaction contracts require an X display (run under xvfb-run).",
)

WINDOW_TITLE = "Place Functional Block"
PICKER_CONTROLS = {
    "functional-block-picker.window",
    "functional-block-picker.search",
    "functional-block-picker.panes",
    "functional-block-picker.results",
    "functional-block-picker.name",
    "functional-block-picker.description",
    "functional-block-picker.variant",
    "functional-block-picker.profile",
    "functional-block-picker.preview",
    "functional-block-picker.metadata",
    "functional-block-picker.action.cancel",
    "functional-block-picker.action.place",
}
PROFILE_1V8 = "Low voltage compact"
PROFILE_3V3 = "Standard 3.3 V"


def _fixture(tmp_path: Path) -> tuple[BlockRecord, BlockRecord]:
    library = tmp_path / "shared-library"
    library.mkdir()
    standard_board = tmp_path / "standard.kicad_pcb"
    compact_board = tmp_path / "compact.kicad_pcb"
    implementation_33 = tmp_path / "implementation_3v3.kicad_sch"
    implementation_18 = tmp_path / "implementation_1v8.kicad_sch"
    standard_board.write_text("(kicad_pcb)\n", encoding="utf-8")
    compact_board.write_text("(kicad_pcb)\n", encoding="utf-8")
    implementation_33.write_text(
        '(kicad_sch (version 20250114) (generator "picker-contract"))\n',
        encoding="utf-8",
    )
    implementation_18.write_text(
        '(kicad_sch (version 20250114) (generator "picker-contract"))\n',
        encoding="utf-8",
    )
    power_stage = create_block(
        library,
        "Power Stage",
        parse_pins("VIN:input, GND:passive, VOUT:output"),
        description="Programmable power conversion stage.",
        parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3"),
        variant_files=[
            {"value": "3V3", "path": implementation_33},
            {"value": "1V8", "path": implementation_18},
        ],
        layout_files=[
            {"id": "standard", "name": "Standard", "path": standard_board},
            {"id": "compact", "name": "Compact", "path": compact_board},
        ],
        profiles=[
            {
                "id": "standard_3v3",
                "name": PROFILE_3V3,
                "variant": "3V3",
                "parameters": {"OUTPUT_VOLTAGE": "3.3"},
                "layout": "standard",
            },
            {
                "id": "low_voltage_compact",
                "name": PROFILE_1V8,
                "variant": "1V8",
                "parameters": {"OUTPUT_VOLTAGE": "1.8"},
                "layout": "compact",
            },
        ],
    )
    usb_interface = create_block(
        library,
        "USB Interface",
        parse_pins("VBUS:input, GND:passive, USB_P:bidirectional, USB_N:bidirectional"),
        description="USB host interface and ESD protection.",
    )
    return power_stage, usb_interface


def _new_root(monkeypatch: MonkeyPatch) -> tuple[Tk, UiTestContext]:
    plugin_actions.load_tkinter()
    def no_icon(_parent: Misc, _name: str, _stroke_color: str | None = None) -> None:
        return None

    monkeypatch.setattr(plugin_actions, "_create_action_icon", no_icon)
    root = plugin_actions.tk.Tk()
    root.title("Functional Block picker contract host")
    root.geometry("1280x760")
    root.update()
    return root, UiTestContext(root)


def _find_dialog(root: Tk) -> Toplevel:
    return next(
        cast(Toplevel, child)
        for child in root.winfo_children()
        if child.winfo_class() == "Toplevel" and cast(Toplevel, child).title() == WINDOW_TITLE
    )


def _find_contract_widget(dialog: Toplevel, contract_id: str) -> Misc:
    pending: list[Misc] = [dialog]
    while pending:
        widget = pending.pop()
        if getattr(widget, "_kicad_ui_contract_id", None) == contract_id:
            return widget
        pending.extend(widget.winfo_children())
    raise AssertionError(f"No widget registered as {contract_id!r}.")


def _find_dialog_exists(root: Tk) -> bool:
    return any(
        child.winfo_class() == "Toplevel" and child.title() == WINDOW_TITLE
        for child in root.winfo_children()
    )


def _run_picker(
    root: Tk,
    context: UiTestContext,
    records: Sequence[BlockRecord],
    action: Callable[[Toplevel], None],
) -> plugin_actions.BlockPlacementSelection | None:
    callback_errors: list[Exception] = []

    def drive() -> None:
        dialog: Toplevel | None = None
        try:
            dialog = _find_dialog(root)
            assert dialog.winfo_viewable()
            assert getattr(dialog, "_kicad_ui_screen_id", None) == "functional-block-picker"
            context.refresh_widgets()
            assert_control_coverage(dialog, PICKER_CONTROLS)
            action(dialog)
        except (
            AssertionError,
            AttributeError,
            LookupError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            plugin_actions.tk.TclError,
        ) as exc:
            callback_errors.append(exc)
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()

    root.after(100, drive)
    result = plugin_actions.pick_block_for_placement(list(records), root)
    if callback_errors:
        raise callback_errors[0]
    return result


def _click(
    context: UiTestContext,
    contract_id: str,
    label: str,
    *,
    closes: bool = False,
) -> None:
    ButtonContract(
        contract_id=contract_id,
        expected_action=None,
        destroys_window=False,
        expected_label=label,
        expected_closed_window_title=WINDOW_TITLE if closes else None,
    ).run(context)
    context.refresh_widgets()


def _install_preview_spy(monkeypatch: MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def draw(canvas: Canvas, record: BlockRecord) -> None:
        calls.append(record.name)
        canvas.delete("all")
        canvas.create_rectangle(10, 10, 110, 70, outline="#4c789f", width=2)
        canvas.create_text(60, 40, text=record.name)

    monkeypatch.setattr(plugin_actions, "_draw_facade_preview", draw)
    return calls


def _assert_initial_state(
    context: UiTestContext, dialog: Toplevel, records: list[BlockRecord]
) -> None:
    context.refresh_widgets()
    tree = context.require_treeview("functional-block-picker.results")
    assert dialog.title() == WINDOW_TITLE
    assert dialog.winfo_width() >= 900
    assert dialog.winfo_height() >= 580
    assert len(tree.get_children()) == len(records)
    assert tree.selection()
    assert context.require_entry("functional-block-picker.search").get() == ""
    assert (
        _find_contract_widget(dialog, "functional-block-picker.name").cget("text") == "Power Stage"
    )
    assert (
        _find_contract_widget(dialog, "functional-block-picker.description").cget("text")
        == "Programmable power conversion stage."
    )
    assert "3 pins" in _find_contract_widget(dialog, "functional-block-picker.metadata").cget(
        "text"
    )
    assert context.require_combobox("functional-block-picker.variant").instate(["!disabled"])
    assert context.require_combobox("functional-block-picker.profile").instate(["!disabled"])
    assert context.require_button("functional-block-picker.action.cancel").instate(["!disabled"])
    assert context.require_button("functional-block-picker.action.place").instate(["!disabled"])


def test_functional_block_picker_filters_results_and_places_selected_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    records = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    preview_calls = _install_preview_spy(monkeypatch)

    def filter_and_place(_dialog: Toplevel) -> None:
        _assert_initial_state(context, _dialog, records)
        search = context.require_entry("functional-block-picker.search")
        search.insert(0, "usb")
        root.update_idletasks()
        root.update()

        tree = context.require_treeview("functional-block-picker.results")
        rows = tree.get_children()
        assert len(rows) == 1
        assert tree.item(rows[0], "values")[0] == "USB Interface"
        assert (
            _find_contract_widget(_dialog, "functional-block-picker.name").cget("text")
            == "USB Interface"
        )
        assert (
            _find_contract_widget(_dialog, "functional-block-picker.description").cget("text")
            == "USB host interface and ESD protection."
        )
        assert "4 pins" in _find_contract_widget(_dialog, "functional-block-picker.metadata").cget(
            "text"
        )
        assert context.require_canvas("functional-block-picker.preview").find_all()
        assert preview_calls[-1] == "USB Interface"
        assert context.require_combobox("functional-block-picker.variant").instate(["disabled"])
        assert context.require_combobox("functional-block-picker.profile").instate(["disabled"])

        _click(
            context,
            "functional-block-picker.action.place",
            "Place Block",
            closes=True,
        )

    try:
        selection = _run_picker(root, context, records, filter_and_place)
        assert selection is not None
        assert selection["record"] is records[1]
        assert selection["variant"] == ""
        assert selection["profile"] == ""
        assert selection["parameters"] == {}
        assert selection["layout"] == ""
    finally:
        root.destroy()


def test_functional_block_picker_profile_applies_variant_and_returns_typed_values(
    monkeypatch: MonkeyPatch, tmp_path
):
    records = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    preview_calls = _install_preview_spy(monkeypatch)

    def choose_profile(_dialog: Toplevel) -> None:
        _assert_initial_state(context, _dialog, records)
        assert context.require_combobox("functional-block-picker.variant").get() == "3V3"
        assert context.require_combobox("functional-block-picker.profile").get() == "Custom"
        assert preview_calls and preview_calls[0] == "Power Stage"

        profile = context.require_combobox("functional-block-picker.profile")
        profile.set(PROFILE_1V8)
        profile.event_generate("<<ComboboxSelected>>")
        root.update_idletasks()
        root.update()

        assert context.require_combobox("functional-block-picker.variant").get() == "1V8"
        _click(
            context,
            "functional-block-picker.action.place",
            "Place Block",
            closes=True,
        )

    try:
        selection = _run_picker(root, context, records, choose_profile)
        assert selection == {
            "record": records[0],
            "variant": "1V8",
            "profile": "low_voltage_compact",
            "parameters": {"OUTPUT_VOLTAGE": "1.8"},
            "layout": "compact",
        }
    finally:
        root.destroy()


def test_functional_block_picker_manual_variant_edit_switches_profile_to_custom(
    monkeypatch: MonkeyPatch, tmp_path
):
    records = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    _install_preview_spy(monkeypatch)

    def choose_custom(_dialog: Toplevel) -> None:
        _assert_initial_state(context, _dialog, records)
        profile = context.require_combobox("functional-block-picker.profile")
        profile.set(PROFILE_1V8)
        profile.event_generate("<<ComboboxSelected>>")
        root.update_idletasks()
        root.update()
        assert context.require_combobox("functional-block-picker.variant").get() == "1V8"

        variant = context.require_combobox("functional-block-picker.variant")
        variant.set("3V3")
        variant.event_generate("<<ComboboxSelected>>")
        root.update_idletasks()
        root.update()
        assert profile.get() == "Custom"
        _click(
            context,
            "functional-block-picker.action.place",
            "Place Block",
            closes=True,
        )

    try:
        selection = _run_picker(root, context, records, choose_custom)
        assert selection is not None
        assert selection["record"] is records[0]
        assert selection["variant"] == "3V3"
        assert selection["profile"] == ""
        assert selection["parameters"] == {"OUTPUT_VOLTAGE": "3.3"}
        assert selection["layout"] == "standard"
    finally:
        root.destroy()


def test_functional_block_picker_empty_search_disables_selectors_and_cancel_returns_none(
    monkeypatch: MonkeyPatch, tmp_path
):
    records = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    preview_calls = _install_preview_spy(monkeypatch)

    def no_match_and_cancel(_dialog: Toplevel) -> None:
        _assert_initial_state(context, _dialog, records)
        search = context.require_entry("functional-block-picker.search")
        search.insert(0, "no-such-block")
        root.update_idletasks()
        root.update()

        tree = context.require_treeview("functional-block-picker.results")
        assert tree.get_children() == ()
        assert (
            _find_contract_widget(_dialog, "functional-block-picker.name").cget("text")
            == "No matching functional blocks"
        )
        assert (
            _find_contract_widget(_dialog, "functional-block-picker.description").cget("text") == ""
        )
        assert _find_contract_widget(_dialog, "functional-block-picker.metadata").cget("text") == ""
        assert context.require_canvas("functional-block-picker.preview").find_all() == ()
        assert context.require_canvas("functional-block-picker.preview").cget("height") == "270"
        assert context.require_combobox("functional-block-picker.variant").instate(["disabled"])
        assert context.require_combobox("functional-block-picker.profile").instate(["disabled"])
        assert preview_calls[-1] == "Power Stage"
        assert context.require_button("functional-block-picker.action.place").instate(["!disabled"])

        # With no selected record the real Place button is a no-op and keeps
        # the picker open, allowing search recovery or explicit cancellation.
        _click(
            context,
            "functional-block-picker.action.place",
            "Place Block",
        )
        assert _find_dialog_exists(root)
        _click(
            context,
            "functional-block-picker.action.cancel",
            "Cancel",
            closes=True,
        )

    try:
        selection = _run_picker(root, context, records, no_match_and_cancel)
        assert selection is None
    finally:
        root.destroy()


def test_functional_block_picker_escape_cancels_and_empty_library_is_rejected(
    monkeypatch: MonkeyPatch, tmp_path
):
    records = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    _install_preview_spy(monkeypatch)

    def escape(_dialog: Toplevel) -> None:
        _assert_initial_state(context, _dialog, records)
        search = context.require_entry("functional-block-picker.search")
        search.focus_force()
        root.update()
        assert root.focus_get() is search
        search.event_generate("<Escape>", when="now")
        root.update_idletasks()
        root.update()
        assert not _find_dialog_exists(root)

    try:
        selection = _run_picker(root, context, records, escape)
        assert selection is None
        with pytest.raises(ValueError, match="No blocks were found"):
            plugin_actions.pick_block_for_placement([], root)
        assert not _find_dialog_exists(root)
    finally:
        root.destroy()
