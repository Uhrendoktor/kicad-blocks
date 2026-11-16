"""Real Tk interaction contracts for the Add PCB layout dialog."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
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


def _new_parent_and_record(tmp_path: Path):
    plugin_actions._load_tkinter()
    root = plugin_actions.tk.Tk()
    root.title("UI contract host")
    root.geometry("640x360")
    block_path = tmp_path / "usb-power"
    block_path.mkdir()
    record = SimpleNamespace(
        path=block_path,
        manifest={
            "variants": [
                {"value": "Boost", "file": "boost.kicad_sch"},
            ]
        },
    )
    root.update()
    return root, record


def _schedule_dialog_action(root, context, action):
    """Drive the modal dialog after its production wait_window starts."""

    def drive():
        dialog = next(
            child
            for child in root.winfo_children()
            if child.winfo_class() == "Toplevel"
            and child.title() == "Add PCB layout"
        )
        context.refresh_widgets()
        action(dialog)

    root.after(100, drive)


def _contract_button(context, contract_id, label, *, closes=False):
    ButtonContract(
        contract_id=contract_id,
        expected_action=None,
        destroys_window=False,
        expected_label=label,
        expected_closed_window_title="Add PCB layout" if closes else None,
    ).run(context)


def test_add_pcb_layout_creates_blank_board_and_returns_selection(
    monkeypatch, tmp_path
):
    root, record = _new_parent_and_record(tmp_path)
    blank_board = tmp_path / "new-board.kicad_pcb"
    blank_board.write_text("(kicad_pcb (version 20250114))\n", encoding="utf-8")
    monkeypatch.setattr(
        plugin_actions, "_new_blank_pcb_source", lambda: blank_board
    )
    context = UiTestContext(root)
    observed = {}

    def interact(dialog):
        assert dialog._kicad_ui_screen_id == "add-pcb-layout"
        assert_control_coverage(dialog, ADD_LAYOUT_CONTROLS)
        context.refresh_widgets()
        _contract_button(context, "add-pcb-layout.add", "Add layout", closes=True)

    _schedule_dialog_action(root, context, interact)
    try:
        result = plugin_actions._ask_layout_details(root, record)
        observed["result"] = result
        assert result == {
            "name": "New layout",
            "source": str(blank_board),
            "linked_implementation": "Default",
        }
        assert blank_board.is_file()
    finally:
        root.destroy()


def test_add_pcb_layout_switches_to_existing_file_and_uses_named_implementation(
    monkeypatch, tmp_path
):
    root, record = _new_parent_and_record(tmp_path)
    existing_board = tmp_path / "existing.kicad_pcb"
    existing_board.write_text("(kicad_pcb (version 20250114))\n", encoding="utf-8")
    monkeypatch.setattr(
        plugin_actions, "_pick_kicad_file",
        lambda *_args, **_kwargs: str(existing_board),
    )
    context = UiTestContext(root)

    def interact(_dialog):
        widgets = context.refresh_widgets()
        mode = widgets["add-pcb-layout.mode"]
        mode.bind(
            "<<ComboboxSelected>>",
            lambda _event: context.events.append("existing-board"),
            add="+",
        )
        ChoiceViewContract(
            contract_id="add-pcb-layout.mode",
            value="Choose an existing PCB file",
            expected_event="existing-board",
        ).run(context)
        assert widgets["add-pcb-layout.source"].winfo_ismapped()
        _contract_button(context, "add-pcb-layout.browse", "Choose file…")
        widgets = context.refresh_widgets()
        assert widgets["add-pcb-layout.source"].get() == str(existing_board)
        widgets["add-pcb-layout.implementation"].set("Boost")
        _contract_button(context, "add-pcb-layout.add", "Add layout", closes=True)

    _schedule_dialog_action(root, context, interact)
    try:
        result = plugin_actions._ask_layout_details(root, record)
        assert result == {
            "name": "New layout",
            "source": str(existing_board.resolve()),
            "linked_implementation": "Boost",
        }
    finally:
        root.destroy()


def test_add_pcb_layout_rejects_missing_name_and_keeps_dialog_open(
    monkeypatch, tmp_path
):
    root, record = _new_parent_and_record(tmp_path)
    errors = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )
    context = UiTestContext(root)

    def interact(_dialog):
        context.refresh_widgets()
        widgets = context.widgets
        widgets["add-pcb-layout.name"].delete(0, "end")
        _contract_button(context, "add-pcb-layout.add", "Add layout")
        assert errors and "1 to 80 characters" in errors[0][1]
        assert any(
            child.winfo_class() == "Toplevel"
            and child.title() == "Add PCB layout"
            for child in root.winfo_children()
        )
        _contract_button(context, "add-pcb-layout.cancel", "Cancel", closes=True)

    _schedule_dialog_action(root, context, interact)
    try:
        result = plugin_actions._ask_layout_details(root, record)
        assert result is None
        assert len(errors) == 1
    finally:
        root.destroy()


def test_add_pcb_layout_cancel_returns_none_without_creating_board(
    monkeypatch, tmp_path
):
    root, record = _new_parent_and_record(tmp_path)
    created = []
    monkeypatch.setattr(
        plugin_actions, "_new_blank_pcb_source",
        lambda: (created.append(True) or tmp_path / "should-not-exist.kicad_pcb"),
    )
    context = UiTestContext(root)
    _schedule_dialog_action(
        root,
        context,
        lambda _dialog: _contract_button(
            context, "add-pcb-layout.cancel", "Cancel", closes=True
        ),
    )
    try:
        result = plugin_actions._ask_layout_details(root, record)
        assert result is None
        assert created == []
    finally:
        root.destroy()
