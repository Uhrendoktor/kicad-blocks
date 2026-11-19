"""Real Tk execute/result contracts for the searchable library record picker."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from collections.abc import Callable, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import BlockRecord, create_block
from ui_contracts import (
    ButtonContract,
    Entry,
    EntryInputContract,
    MonkeyPatch,
    Tk,
    Toplevel,
    Treeview,
    UiTestContext,
    assert_control_coverage,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="Real Tk interaction contracts require an X display (run under xvfb-run).",
)

WINDOW_TITLE = "Pick a functional block"
RECORD_PICKER_CONTROLS = {
    "record-picker.window",
    "record-picker.search",
    "record-picker.results",
    "record-picker.description",
    "record-picker.count",
    "record-picker.action.select",
    "record-picker.action.cancel",
}


def _new_root(monkeypatch: MonkeyPatch) -> tuple[Tk, UiTestContext]:
    plugin_actions.load_tkinter()
    monkeypatch.setattr(plugin_actions, "_create_action_icon", lambda *_args: None)
    root = plugin_actions.tk.Tk()
    root.title("Record picker UI contract host")
    root.geometry("1100x760")
    root.update()
    return root, UiTestContext(root)


def _make_records(tmp_path: Path) -> tuple[BlockRecord, BlockRecord, BlockRecord]:
    library = tmp_path / "shared-library"
    library.mkdir()
    first = create_block(
        library,
        "Buck Regulator",
        parse_pins("VIN:input, GND:passive, VOUT:output"),
        description="Synchronous regulator and power conversion stage.",
    )
    second = create_block(
        library,
        "USB Interface",
        parse_pins("VBUS:input, GND:passive, USB_P:bidirectional, USB_N:bidirectional"),
        description="USB host interface and protection circuit.",
    )
    third = create_block(
        library,
        "Audio Amplifier",
        parse_pins("IN:input, GND:passive, OUT:output"),
        description="Low-noise audio output amplifier.",
    )
    return first, second, third


def _find_dialog(root: Tk) -> Toplevel:
    return next(
        cast(Toplevel, child)
        for child in root.winfo_children()
        if child.winfo_class() == "Toplevel" and cast(Toplevel, child).title() == WINDOW_TITLE
    )


def _run_picker(
    root: Tk,
    context: UiTestContext,
    records: Sequence[BlockRecord],
    action: Callable[[Toplevel], None],
) -> BlockRecord | None:
    callback_errors: list[Exception] = []

    def drive() -> None:
        dialog: Toplevel | None = None
        try:
            dialog = _find_dialog(root)
            assert dialog.winfo_viewable()
            assert getattr(dialog, "_kicad_ui_screen_id", None) == "record-picker"
            context.refresh_widgets()
            assert_control_coverage(dialog, RECORD_PICKER_CONTROLS)
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
    result = plugin_actions.pick_record(list(records), root, WINDOW_TITLE)
    if callback_errors:
        raise callback_errors[0]
    return result


def _click(context: UiTestContext, contract_id: str, label: str, *, closes: bool = False) -> None:
    ButtonContract(
        contract_id=contract_id,
        expected_action=None,
        destroys_window=False,
        expected_label=label,
        expected_closed_window_title=WINDOW_TITLE if closes else None,
    ).run(context)
    context.refresh_widgets()


def _assert_initial_state(context: UiTestContext) -> None:
    widgets = context.refresh_widgets()
    tree = context.require_treeview("record-picker.results")
    assert len(tree.get_children()) == 3
    assert widgets["record-picker.count"].cget("text") == "3 of 3 blocks"
    assert tree.item(tree.selection()[0], "values")[0] == "Buck Regulator"
    assert widgets["record-picker.description"].cget("text") == (
        "Synchronous regulator and power conversion stage."
    )
    assert context.require_button("record-picker.action.select").instate(["!disabled"])
    assert context.require_button("record-picker.action.cancel").instate(["!disabled"])


def test_record_picker_filters_and_selects_the_matching_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    records = _make_records(tmp_path)
    root, context = _new_root(monkeypatch)

    def filter_and_select(_dialog: Toplevel) -> None:
        _assert_initial_state(context)
        EntryInputContract(
            contract_id="record-picker.search",
            value="usb",
            results_contract_id="record-picker.results",
            expected_result_count=1,
            summary_contract_id="record-picker.count",
            expected_summary="1 of 3 blocks",
        ).run(context)
        assert (
            context.require_treeview("record-picker.results").item(
                context.require_treeview("record-picker.results").get_children()[0],
                "values",
            )[0]
            == "USB Interface"
        )
        assert context.require_widget("record-picker.description").cget("text") == (
            "USB host interface and protection circuit."
        )
        _click(
            context,
            "record-picker.action.select",
            "Select block",
            closes=True,
        )

    try:
        chosen = _run_picker(root, context, records, filter_and_select)
        assert chosen is records[1]
    finally:
        root.destroy()


def test_record_picker_empty_search_disables_select_and_cancel_returns_none(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    records = _make_records(tmp_path)
    root, context = _new_root(monkeypatch)

    def no_match_then_cancel(_dialog: Toplevel) -> None:
        _assert_initial_state(context)
        search = context.require_entry("record-picker.search")
        search.delete(0, "end")
        search.insert(0, "not-a-real-block")
        root.update_idletasks()
        root.update()

        tree = context.require_treeview("record-picker.results")
        assert tree.get_children() == ()
        assert context.require_widget("record-picker.count").cget("text") == "0 of 3 blocks"
        assert context.require_widget("record-picker.description").cget("text") == (
            "No matching blocks."
        )
        assert context.require_button("record-picker.action.select").instate(["disabled"])
        assert context.require_button("record-picker.action.cancel").instate(["!disabled"])

        _click(
            context,
            "record-picker.action.cancel",
            "Cancel",
            closes=True,
        )

    try:
        chosen = _run_picker(root, context, records, no_match_then_cancel)
        assert chosen is None
    finally:
        root.destroy()


def test_record_picker_down_and_return_choose_the_highlighted_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    records = _make_records(tmp_path)
    root, context = _new_root(monkeypatch)

    def choose_with_keyboard(_dialog: Toplevel) -> None:
        _assert_initial_state(context)
        search = context.require_entry("record-picker.search")
        search.event_generate("<Down>")
        root.update_idletasks()
        root.update()
        tree = context.require_treeview("record-picker.results")
        assert root.focus_get() is tree
        assert tree.selection()
        tree.event_generate("<Return>")
        root.update_idletasks()
        root.update()
        assert not _find_dialog_exists(root)

    try:
        chosen = _run_picker(root, context, records, choose_with_keyboard)
        assert chosen is records[0]
    finally:
        root.destroy()


def _find_dialog_exists(root: Tk) -> bool:
    return any(
        child.winfo_class() == "Toplevel" and child.title() == WINDOW_TITLE
        for child in root.winfo_children()
    )


def test_record_picker_double_click_selects_the_clicked_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    records = _make_records(tmp_path)
    root, context = _new_root(monkeypatch)

    def double_click_target(_dialog: Toplevel) -> None:
        _assert_initial_state(context)
        tree = context.require_treeview("record-picker.results")
        row_id = next(
            iid for iid in tree.get_children() if tree.item(iid, "values")[0] == "USB Interface"
        )
        x, y, width, height = tree.bbox(row_id)
        click_x = x + min(10, width // 2)
        click_y = y + height // 2
        # Tk recognizes a double click from two presses within its
        # multi-click interval. The production <Double-1> handler destroys
        # the modal on the second press, so do not send a release to the
        # Treeview after that widget has been destroyed.
        tree.event_generate(
            "<ButtonPress-1>",
            x=click_x,
            y=click_y,
            time=1000,
            when="now",
        )
        tree.event_generate(
            "<ButtonRelease-1>",
            x=click_x,
            y=click_y,
            time=1005,
            when="now",
        )
        root.update()
        assert tree.winfo_exists()
        tree.event_generate(
            "<ButtonPress-1>",
            x=click_x,
            y=click_y,
            time=1100,
            when="now",
        )
        root.update()
        root.update_idletasks()
        assert not _find_dialog_exists(root)

    try:
        chosen = _run_picker(root, context, records, double_click_target)
        assert chosen is records[1]
    finally:
        root.destroy()


def test_record_picker_empty_and_single_record_fast_paths(monkeypatch: MonkeyPatch, tmp_path: Path):
    first, _second, _third = _make_records(tmp_path)
    root, _context = _new_root(monkeypatch)
    try:
        with pytest.raises(ValueError, match="No blocks were found"):
            plugin_actions.pick_record([], root, WINDOW_TITLE)
        assert not _find_dialog_exists(root)

        chosen = plugin_actions.pick_record([first], root, WINDOW_TITLE)
        assert chosen is first
        assert not _find_dialog_exists(root)
    finally:
        root.destroy()
