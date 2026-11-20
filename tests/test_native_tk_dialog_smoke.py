"""Smoke-test real Tk message and simple dialogs under a display server.

The test does not stub messagebox/simpledialog. It observes the windows Tk
actually creates, verifies their parent and visible controls, then invokes a
real button so the modal function returns the expected Python result.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from typing import TypedDict

import pytest

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog
except ImportError:  # pragma: no cover - exercised by the skip guard below
    tk = None
    messagebox = None
    simpledialog = None

pytestmark = pytest.mark.skipif(
    tk is None or (sys.platform == "linux" and not os.environ.get("DISPLAY")),
    reason="Real Tk dialog smoke tests require Tk and a display (run under xvfb-run).",
)

_DIALOG_TIMEOUT_SECONDS = 5.0


class _DialogOutcome(TypedDict, total=False):
    """Captured properties and failures from one observed native Tk dialog."""

    path: str
    mapped: bool
    geometry: str
    transient_parent: str
    callback_error: str
    timeout: str


def _create_parent():
    root = tk.Tk()
    root.title("KiCad Blocks native dialog smoke host")
    root.geometry("360x180+20+20")
    root.resizable(False, False)
    root.update_idletasks()
    root.update()
    return root


def _children(root, widget_path: str) -> list[str]:
    return list(root.tk.splitlist(root.tk.call("winfo", "children", widget_path)))


def _walk_widgets(root, widget_path: str):
    for child in _children(root, widget_path):
        yield child
        yield from _walk_widgets(root, child)


def _find_dialog(root, title: str) -> str | None:
    for child in _children(root, root._w):
        try:
            if str(root.tk.call("wm", "title", child)) == title:
                return child
        except tk.TclError:
            continue
    return None


def _button_labels(root, dialog_path: str) -> dict[str, str]:
    buttons = {}
    for widget in _walk_widgets(root, dialog_path):
        try:
            widget_class = str(root.tk.call("winfo", "class", widget))
            if widget_class not in {"Button", "TButton"}:
                continue
            label = str(root.tk.call(widget, "cget", "-text")).strip()
            if label:
                buttons[label.casefold()] = widget
        except tk.TclError:
            continue
    return buttons


def _visible_text(root, dialog_path: str) -> list[str]:
    values = []
    for widget in _walk_widgets(root, dialog_path):
        try:
            value = str(root.tk.call(widget, "cget", "-text")).strip()
        except tk.TclError:
            continue
        if value:
            values.append(value)
    return values


def _dismiss_after_callback_error(root, dialog_path: str) -> None:
    """Avoid leaving a modal window open after recording a callback failure."""
    try:
        buttons = _button_labels(root, dialog_path)
        for label in ("ok", "cancel", "no", "close"):
            if label in buttons:
                root.tk.call(buttons[label], "invoke")
                return
        root.tk.call("destroy", dialog_path)
    except tk.TclError:
        pass


def _schedule_dialog_action(
    root,
    title: str,
    callback: Callable[[str], None],
) -> _DialogOutcome:
    """Observe a modal window once mapped, then interact with its real widgets."""
    outcome: _DialogOutcome = {}
    deadline = time.monotonic() + _DIALOG_TIMEOUT_SECONDS

    def poll() -> None:
        dialog_path = _find_dialog(root, title)
        if dialog_path is not None:
            outcome["path"] = dialog_path
            try:
                outcome["mapped"] = bool(int(root.tk.call("winfo", "ismapped", dialog_path)))
                outcome["geometry"] = str(root.tk.call("wm", "geometry", dialog_path))
                outcome["transient_parent"] = str(root.tk.call("wm", "transient", dialog_path))
                callback(dialog_path)
            except (tk.TclError, AssertionError, KeyError, TypeError, ValueError) as exc:
                outcome["callback_error"] = f"{type(exc).__name__}: {exc}"
                _dismiss_after_callback_error(root, dialog_path)
            return

        if time.monotonic() >= deadline:
            outcome["timeout"] = f"Tk did not map dialog {title!r} within {_DIALOG_TIMEOUT_SECONDS:g}s."
            # Destroying the root also terminates Tk's nested modal wait, so the
            # outer assertion reports a timeout rather than hanging the runner.
            try:
                root.destroy()
            except tk.TclError:
                pass
            return
        root.after(20, poll)

    root.after(20, poll)
    return outcome


def _assert_dialog_window(root, outcome: _DialogOutcome) -> str:
    assert "timeout" not in outcome, outcome.get("timeout")
    assert "callback_error" not in outcome, outcome.get("callback_error")
    dialog_path = outcome.get("path")
    assert isinstance(dialog_path, str) and dialog_path
    assert outcome.get("mapped") is True
    assert outcome.get("transient_parent") == root._w
    geometry = str(outcome.get("geometry", ""))
    dimensions = geometry.split("+", 1)[0].split("x", 1)
    assert len(dimensions) == 2 and all(int(value) > 0 for value in dimensions), geometry
    return dialog_path


def _invoke_button(root, dialog_path: str, label: str) -> None:
    buttons = _button_labels(root, dialog_path)
    button = buttons.get(label.casefold())
    assert button is not None, f"Dialog {dialog_path!r} did not render a {label!r} button; found {sorted(buttons)}."
    root.tk.call(button, "invoke")


def _destroy_parent(root) -> None:
    try:
        if root.winfo_exists():
            root.destroy()
    except tk.TclError:
        pass


def test_real_showerror_dialog_renders_message_parent_and_ok_button():
    root = _create_parent()
    title = "Native error dialog smoke"
    message = "The test operation failed and can be retried."

    outcome = _schedule_dialog_action(
        root,
        title,
        lambda dialog: (
            _assert_message_dialog(root, dialog, message, {"ok"}),
            _invoke_button(root, dialog, "OK"),
        ),
    )
    try:
        result = messagebox.showerror(title, message, parent=root)
        _assert_dialog_window(root, outcome)
        assert result == "ok"
    finally:
        _destroy_parent(root)


def test_real_askyesno_dialog_renders_both_choices_and_returns_yes():
    root = _create_parent()
    title = "Native confirmation dialog smoke"
    message = "Apply the selected layout?"

    outcome = _schedule_dialog_action(
        root,
        title,
        lambda dialog: (
            _assert_message_dialog(root, dialog, message, {"yes", "no"}),
            _invoke_button(root, dialog, "Yes"),
        ),
    )
    try:
        result = messagebox.askyesno(title, message, parent=root)
        _assert_dialog_window(root, outcome)
        assert result is True
    finally:
        _destroy_parent(root)


def test_real_askstring_dialog_renders_prompt_and_returns_entered_value():
    root = _create_parent()
    title = "Native text prompt smoke"
    prompt = "Enter a short block label."
    entered = "Power input stage"

    def enter_value(dialog: str) -> None:
        assert prompt in _visible_text(root, dialog)
        entries = [
            widget
            for widget in _walk_widgets(root, dialog)
            if str(root.tk.call("winfo", "class", widget)) in {"Entry", "TEntry"}
        ]
        assert entries, f"Dialog {dialog!r} did not render a text-entry control."
        root.tk.call(entries[0], "insert", 0, entered)
        _invoke_button(root, dialog, "OK")

    outcome = _schedule_dialog_action(root, title, enter_value)
    try:
        result = simpledialog.askstring(title, prompt, parent=root)
        _assert_dialog_window(root, outcome)
        assert result == entered
    finally:
        _destroy_parent(root)


def test_real_askstring_cancel_button_returns_none():
    root = _create_parent()
    title = "Native cancellable prompt smoke"
    prompt = "A value that will not be accepted."

    def cancel(dialog: str) -> None:
        assert prompt in _visible_text(root, dialog)
        _invoke_button(root, dialog, "Cancel")

    outcome = _schedule_dialog_action(root, title, cancel)
    try:
        result = simpledialog.askstring(title, prompt, parent=root)
        _assert_dialog_window(root, outcome)
        assert result is None
    finally:
        _destroy_parent(root)


def _assert_message_dialog(
    root,
    dialog_path: str,
    message: str,
    expected_buttons: set[str],
) -> None:
    assert message in _visible_text(root, dialog_path)
    buttons = _button_labels(root, dialog_path)
    assert expected_buttons.issubset(buttons), (
        f"Dialog {dialog_path!r} is missing buttons {sorted(expected_buttons - set(buttons))}; "
        f"found {sorted(buttons)}."
    )
