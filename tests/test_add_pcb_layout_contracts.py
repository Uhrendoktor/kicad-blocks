"""Real Tk interaction contracts for the Add PCB layout dialog."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from _pytest.monkeypatch import MonkeyPatch

if TYPE_CHECKING:
    from tkinter import Event as TkEvent
    from tkinter import Misc, Tk, Toplevel
else:
    TkEvent = Misc = Tk = Toplevel = object

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.manifest_types import BlockManifest
from kicad_blocks.repository import BlockRecord
from ui_contracts import (
    ButtonContract,
    ChoiceViewContract,
    UiTestContext,
    assert_control_coverage,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="Real Tk interaction contracts require an X display (run under xvfb-run).",
)

ADD_LAYOUT_CONTROLS = {
    "add-pcb-layout.name",
    "add-pcb-layout.mode",
    "add-pcb-layout.source",
    "add-pcb-layout.browse",
    "add-pcb-layout.implementation",
    "add-pcb-layout.cancel",
    "add-pcb-layout.add",
}


_load_tkinter = plugin_actions.load_tkinter
_ask_layout_details = cast(
    Callable[..., dict[str, str] | None], getattr(plugin_actions, "_ask_layout_details")
)


def _new_parent_and_record(tmp_path: Path) -> tuple[Tk, BlockRecord]:
    _load_tkinter()
    root = plugin_actions.tk.Tk()
    root.title("UI contract host")
    root.geometry("640x360")
    block_path = tmp_path / "usb-power"
    block_path.mkdir()
    manifest = cast(
        BlockManifest,
        {
            "schema_version": 1,
            "block_id": "usb-power",
            "name": "USB power block",
            "slug": "usb-power",
            "description": "",
            "version": 1,
            "revision": "",
            "created_by": "test fixture",
            "interface_source": "manual",
            "library_nickname": "KBlocks",
            "symbol_name": "usb_power_facade",
            "catalog_file": "KBlocks.kicad_sym",
            "symbol_file": "block.kicad_sym",
            "implementation_file": "implementation.kicad_sch",
            "layout_file": None,
            "layout_project_file": None,
            "layouts": [],
            "default_layout": None,
            "reference_prefix": "X",
            "pins": [],
            "parameters": [],
            "variants": [{"value": "Boost", "file": "boost.kicad_sch"}],
            "profiles": [],
            "dependencies": [],
            "files": ["implementation.kicad_sch"],
            "hashes": {},
        },
    )
    record = BlockRecord(path=block_path, manifest=manifest)
    root.update()
    return root, record


def _schedule_dialog_action(
    root: Tk,
    context: UiTestContext,
    action: Callable[[Toplevel], None],
) -> list[Exception]:
    """Drive a modal dialog and return any exception from its Tk after callback."""
    callback_errors: list[Exception] = []

    def drive() -> None:
        dialog: Toplevel | None = None
        try:
            matching_dialog = next(
                (
                    child
                    for child in root.winfo_children()
                    if child.winfo_class() == "Toplevel"
                    and cast(Toplevel, child).title() == "Add PCB layout"
                ),
                None,
            )
            if matching_dialog is None:
                raise AssertionError("The Add PCB layout dialog was not created.")
            dialog = cast(Toplevel, matching_dialog)
            context.refresh_widgets()
            action(dialog)
        except Exception as exc:
            callback_errors.append(exc)
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()

    root.after(100, drive)
    return callback_errors


def _raise_callback_errors(callback_errors: list[Exception]) -> None:
    """Re-raise failures from Tk's asynchronous callback in the pytest thread."""
    if callback_errors:
        raise callback_errors[0]


def _contract_button(
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
        expected_closed_window_title="Add PCB layout" if closes else None,
    ).run(context)


def test_add_pcb_layout_creates_blank_board_and_returns_selection(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    root, record = _new_parent_and_record(tmp_path)
    blank_board = tmp_path / "new-board.kicad_pcb"
    blank_board.write_text("(kicad_pcb (version 20250114))\n", encoding="utf-8")
    monkeypatch.setattr(plugin_actions, "_new_blank_pcb_source", lambda: blank_board)
    context = UiTestContext(root)

    def interact(dialog: Toplevel) -> None:
        assert getattr(dialog, "_kicad_ui_screen_id", None) == "add-pcb-layout"
        assert_control_coverage(dialog, ADD_LAYOUT_CONTROLS)
        context.refresh_widgets()
        _contract_button(context, "add-pcb-layout.add", "Add layout", closes=True)

    callback_errors = _schedule_dialog_action(root, context, interact)
    try:
        result = _ask_layout_details(root, record)
        _raise_callback_errors(callback_errors)
        assert result == {
            "name": "New layout",
            "source": str(blank_board),
            "linked_implementation": "Default",
        }
        assert blank_board.is_file()
    finally:
        root.destroy()


def test_add_pcb_layout_switches_to_existing_file_and_uses_named_implementation(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    root, record = _new_parent_and_record(tmp_path)
    existing_board = tmp_path / "existing.kicad_pcb"
    existing_board.write_text("(kicad_pcb (version 20250114))\n", encoding="utf-8")

    def pick_kicad_file(
        _parent: Misc,
        *,
        title: str,
        suffix: str,
        initial_path: str | Path = "",
    ) -> str:
        del title, suffix, initial_path
        return str(existing_board)

    monkeypatch.setattr(plugin_actions, "_pick_kicad_file", pick_kicad_file)
    context = UiTestContext(root)

    def on_mode_selected(_event: TkEvent[Misc]) -> str | None:
        context.events.append("existing-board")
        return None

    def interact(_dialog: Toplevel) -> None:
        mode = context.require_combobox("add-pcb-layout.mode")
        mode.bind(
            "<<ComboboxSelected>>",
            on_mode_selected,
            add="+",
        )
        ChoiceViewContract(
            contract_id="add-pcb-layout.mode",
            value="Choose an existing PCB file",
            expected_event="existing-board",
        ).run(context)
        assert context.require_entry("add-pcb-layout.source").winfo_ismapped()
        _contract_button(context, "add-pcb-layout.browse", "Choose file…")
        assert context.require_entry("add-pcb-layout.source").get() == str(existing_board)
        context.require_combobox("add-pcb-layout.implementation").set("Boost")
        _contract_button(context, "add-pcb-layout.add", "Add layout", closes=True)

    callback_errors = _schedule_dialog_action(root, context, interact)
    try:
        result = _ask_layout_details(root, record)
        _raise_callback_errors(callback_errors)
        assert result == {
            "name": "New layout",
            "source": str(existing_board.resolve()),
            "linked_implementation": "Boost",
        }
    finally:
        root.destroy()


def test_add_pcb_layout_rejects_missing_name_and_keeps_dialog_open(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    root, record = _new_parent_and_record(tmp_path)
    errors: list[tuple[str, str]] = []

    def show_error(title: str, message: str, **_kwargs: str | bool) -> None:
        errors.append((title, message))

    monkeypatch.setattr(plugin_actions.messagebox, "showerror", show_error)
    context = UiTestContext(root)

    def interact(_dialog: Toplevel) -> None:
        context.require_entry("add-pcb-layout.name").delete(0, "end")
        _contract_button(context, "add-pcb-layout.add", "Add layout")
        assert errors and "1 to 80 characters" in errors[0][1]
        assert any(
            child.winfo_class() == "Toplevel" and child.title() == "Add PCB layout"
            for child in root.winfo_children()
        )
        _contract_button(context, "add-pcb-layout.cancel", "Cancel", closes=True)

    callback_errors = _schedule_dialog_action(root, context, interact)
    try:
        result = _ask_layout_details(root, record)
        _raise_callback_errors(callback_errors)
        assert result is None
        assert len(errors) == 1
    finally:
        root.destroy()


def test_add_pcb_layout_cancel_returns_none_without_creating_board(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    root, record = _new_parent_and_record(tmp_path)
    created: list[bool] = []

    def create_blank_board() -> Path:
        created.append(True)
        return tmp_path / "should-not-exist.kicad_pcb"

    monkeypatch.setattr(plugin_actions, "_new_blank_pcb_source", create_blank_board)
    context = UiTestContext(root)

    def cancel_dialog(_dialog: Toplevel) -> None:
        _contract_button(context, "add-pcb-layout.cancel", "Cancel", closes=True)

    callback_errors = _schedule_dialog_action(root, context, cancel_dialog)
    try:
        result = _ask_layout_details(root, record)
        _raise_callback_errors(callback_errors)
        assert result is None
        assert created == []
    finally:
        root.destroy()


def test_add_pcb_layout_rejects_invalid_existing_file_then_recovers(monkeypatch, tmp_path):
    root, record = _new_parent_and_record(tmp_path)
    invalid = tmp_path / "notes.txt"
    invalid.write_text("not a KiCad board", encoding="utf-8")
    valid = tmp_path / "board.kicad_pcb"
    valid.write_text("(kicad_pcb (version 20250114))\\n", encoding="utf-8")
    errors = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append((title, str(message), kwargs.get("parent"))),
    )
    context = UiTestContext(root)

    def interact(dialog):
        mode = context.require_widget("add-pcb-layout.mode")
        mode.set("Choose an existing PCB file")
        mode.event_generate("<<ComboboxSelected>>")
        root.update_idletasks()
        root.update()
        source = context.require_widget("add-pcb-layout.source")
        source.insert(0, str(invalid))
        _contract_button(context, "add-pcb-layout.add", "Add layout")
        assert errors == [
            (
                "Add PCB layout",
                "Choose an existing KiCad .kicad_pcb file.",
                dialog,
            )
        ]
        assert dialog.winfo_exists()
        source.delete(0, "end")
        source.insert(0, str(valid))
        _contract_button(context, "add-pcb-layout.add", "Add layout", closes=True)

    _schedule_dialog_action(root, context, interact)
    try:
        result = plugin_actions._ask_layout_details(root, record)
        assert result == {
            "name": "New layout",
            "source": str(valid.resolve()),
            "linked_implementation": "Default",
        }
        assert len(errors) == 1
    finally:
        root.destroy()
