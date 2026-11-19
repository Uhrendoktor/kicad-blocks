"""Real Tk interaction contracts for the Create Functional Block form."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from ui_contracts import (
    ButtonContract,
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

SCREEN_ID = "create-block-form"
WINDOW_TITLE = "Create Functional Block"
CREATE_BLOCK_CONTROLS = {
    "create-block-form.window",
    "create-block-form.name",
    "create-block-form.description",
    "create-block-form.action.cancel",
    "create-block-form.action.create",
}


def _new_root(monkeypatch: MonkeyPatch) -> tuple[Tk, UiTestContext]:
    plugin_actions.load_tkinter()
    monkeypatch.setattr(plugin_actions, "_create_action_icon", lambda *_args: None)
    root = plugin_actions.tk.Tk()
    root.title("Create Block UI contract host")
    root.geometry("820x580")
    root.update()
    return root, UiTestContext(root)


def _find_dialog(root: Tk) -> Toplevel:
    return next(
        child
        for child in root.winfo_children()
        if child.winfo_class() == "Toplevel" and child.title() == WINDOW_TITLE
    )


def _click(
    context: UiTestContext,
    contract_id: str,
    label: str,
    *,
    closes: bool = False,
):
    ButtonContract(
        contract_id=contract_id,
        expected_action=None,
        destroys_window=False,
        expected_label=label,
        expected_closed_window_title=WINDOW_TITLE if closes else None,
    ).run(context)
    context.refresh_widgets()


def _run_form(
    root: Tk, context: UiTestContext, action: Callable[[Toplevel], None]
) -> tuple[str, str] | None:
    callback_errors: list[Exception] = []

    def drive() -> None:
        dialog: Toplevel | None = None
        try:
            dialog = _find_dialog(root)
            assert dialog.winfo_viewable()
            assert dialog._kicad_ui_screen_id == SCREEN_ID
            context.refresh_widgets()
            assert_control_coverage(dialog, CREATE_BLOCK_CONTROLS)
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
    result = plugin_actions.ask_create_block_details(root, Path("power_stage.kicad_sch"))
    if callback_errors:
        raise callback_errors[0]
    return result


def _assert_initial_form_state(context: UiTestContext, dialog: Toplevel) -> None:
    widgets = context.refresh_widgets()
    assert dialog.title() == WINDOW_TITLE
    assert dialog.winfo_width() >= 580
    assert dialog.winfo_height() >= 340

    name_entry = widgets["create-block-form.name"]
    description = widgets["create-block-form.description"]
    cancel_button = widgets["create-block-form.action.cancel"]
    create_button = widgets["create-block-form.action.create"]

    assert name_entry.winfo_viewable()
    assert name_entry.get() == "Power Stage"
    assert description.winfo_viewable()
    assert description.get("1.0", "end-1c") == ""
    assert cancel_button.winfo_viewable()
    assert create_button.winfo_viewable()
    assert cancel_button.instate(["!disabled"])
    assert create_button.instate(["!disabled"])

    window_bottom = dialog.winfo_rooty() + dialog.winfo_height()
    for widget in (name_entry, description, cancel_button, create_button):
        assert widget.winfo_rooty() + widget.winfo_height() <= window_bottom


def test_create_block_form_submits_name_and_optional_description(monkeypatch: MonkeyPatch):
    root, context = _new_root(monkeypatch)

    def create(dialog: Toplevel) -> None:
        _assert_initial_form_state(context, dialog)
        context.require_widget("create-block-form.name").delete(0, "end")
        context.require_widget("create-block-form.name").insert(0, "USB Power Stage")
        description = context.require_widget("create-block-form.description")
        description.insert("1.0", "Isolated USB power conversion stage.")
        _click(
            context,
            "create-block-form.action.create",
            "Create Block",
            closes=True,
        )

    try:
        result = _run_form(root, context, create)
        assert result == (
            "USB Power Stage",
            "Isolated USB power conversion stage.",
        )
    finally:
        root.destroy()


def test_create_block_form_rejects_blank_name_and_recovers(monkeypatch: MonkeyPatch):
    root, context = _new_root(monkeypatch)
    errors = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )

    def recover(dialog: Toplevel) -> None:
        _assert_initial_form_state(context, dialog)
        name_entry = context.require_widget("create-block-form.name")
        name_entry.delete(0, "end")
        _click(context, "create-block-form.action.create", "Create Block")
        assert dialog.winfo_exists()
        assert dialog.winfo_viewable()
        assert errors == [("Create Functional Block", "Enter a block name.")]

        name_entry.insert(0, "Recovered Block")
        _click(
            context,
            "create-block-form.action.create",
            "Create Block",
            closes=True,
        )

    try:
        result = _run_form(root, context, recover)
        assert result == ("Recovered Block", "")
    finally:
        root.destroy()


def test_create_block_form_cancel_returns_none_without_creating_block(monkeypatch: MonkeyPatch):
    root, context = _new_root(monkeypatch)

    def cancel(dialog: Toplevel) -> None:
        _assert_initial_form_state(context, dialog)
        _click(
            context,
            "create-block-form.action.cancel",
            "Cancel",
            closes=True,
        )

    try:
        result = _run_form(root, context, cancel)
        assert result is None
    finally:
        root.destroy()


def test_create_block_form_escape_key_cancels(monkeypatch: MonkeyPatch):
    root, context = _new_root(monkeypatch)

    def cancel_with_escape(dialog: Toplevel) -> None:
        _assert_initial_form_state(context, dialog)
        dialog.event_generate("<Escape>")
        root.update_idletasks()
        root.update()
        assert not dialog.winfo_exists()

    try:
        result = _run_form(root, context, cancel_with_escape)
        assert result is None
    finally:
        root.destroy()
