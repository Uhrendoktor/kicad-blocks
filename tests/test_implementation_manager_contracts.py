"""Real Tk contracts for the implementation variants manager."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import create_block, read_manifest
from ui_contracts import (
    ButtonContract,
    TreeSelectionContract,
    UiTestContext,
    assert_control_coverage,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="Real Tk interaction contracts require an X display (run under xvfb-run).",
)

MANAGER_CONTROLS = {
    "implementations-manager.window",
    "implementations-manager.rows",
    "implementations-manager.status",
    "implementations-manager.action.add",
    "implementations-manager.action.edit",
    "implementations-manager.action.default",
    "implementations-manager.action.remove",
    "implementations-manager.action.close",
    "implementations-manager.action.save-close",
}
STATUS_TEXT = "Empty KB_VARIANT uses the default implementation."


def _new_manager_fixture(tmp_path: Path, variants=(), profiles=()):
    library_root = tmp_path / "library"
    library_root.mkdir()
    source_root = tmp_path / "sources"
    source_root.mkdir()
    variant_files = []
    sources = {}
    for value in variants:
        source = source_root / f"{value.casefold()}.kicad_sch"
        source.write_text(
            '(kicad_sch (version 20250114) (generator "contract-test"))\n',
            encoding="utf-8",
        )
        sources[value] = source
        variant_files.append({"value": value, "path": source})
    record = create_block(
        library_root,
        "USB power block",
        parse_pins("VIN:input, GND:passive, VOUT:output"),
        variant_files=variant_files,
        profiles=list(profiles),
    )
    plugin_actions._load_tkinter()
    return record, sources


def _new_root(monkeypatch):
    monkeypatch.setattr(
        plugin_actions,
        "_create_action_icon",
        lambda *_args: None,
    )
    root = plugin_actions.tk.Tk()
    root.title("UI contract host")
    root.geometry("1280x800")
    root.update()
    return root


def _manager_dialog(root):
    return next(
        child
        for child in root.winfo_children()
        if child.winfo_class() == "Toplevel"
        and child.title().startswith("Implementations — ")
    )


def _run_manager(root, record, context, action):
    callback_errors = []

    def drive():
        dialog = None
        try:
            dialog = _manager_dialog(root)
            assert dialog._kicad_ui_screen_id == "implementations-manager"
            assert dialog.title() == f"Implementations — {record.name}"
            context.refresh_widgets()
            assert_control_coverage(dialog, MANAGER_CONTROLS)
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
            # Tkinter reports callback exceptions to stderr and keeps a modal
            # wait_window() alive. Capture the exception in the test thread,
            # close the modal, and re-raise after wait_window returns so CI
            # fails with the useful assertion instead of timing out.
            callback_errors.append(exc)
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()
            elif not context.window_destroyed:
                for child in root.winfo_children():
                    if child.winfo_class() == "Toplevel":
                        child.destroy()

    root.after(100, drive)
    result = plugin_actions._show_block_variant_editor(record, root)
    if callback_errors:
        raise callback_errors[0]
    return result


def _select_variant(context, row_id: str, expected_values: tuple[str, ...]):
    TreeSelectionContract(
        contract_id="implementations-manager.rows",
        row_id=row_id,
        summary_contract_id="implementations-manager.status",
        expected_summary=STATUS_TEXT,
        expected_values=expected_values,
    ).run(context)


def _click(
    context,
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
        expected_closed_window_title=(
            "Implementations — USB power block" if closes else None
        ),
    ).run(context)


def test_implementation_manager_window_and_all_buttons_are_discoverable(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def inspect_and_close(dialog):
        assert dialog.winfo_viewable()
        widgets = context.widgets
        tree = widgets["implementations-manager.rows"]
        assert tree.item("0", "values")[0] == "Default"
        assert tree.item("1", "values")[0] == "1V8"
        assert widgets["implementations-manager.action.edit"].instate(["!disabled"])
        assert widgets["implementations-manager.action.remove"].instate(["disabled"])
        _click(context, "implementations-manager.action.close", "Close", closes=True)

    try:
        result = _run_manager(root, record, context, inspect_and_close)
        assert result is None
    finally:
        root.destroy()


def test_set_as_default_promotes_selected_implementation_and_persists_manifest(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(
        tmp_path,
        variants=("1V8", "3V3"),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)
    manifest_path = record.path / "manifest.json"

    def promote(_dialog):
        tree = context.require_widget("implementations-manager.rows")
        row_values = tuple(tree.item("2", "values"))
        _select_variant(context, "2", row_values)
        default_button = context.require_widget(
            "implementations-manager.action.default"
        )
        assert default_button.cget("text") == "Set as default"
        _click(context, "implementations-manager.action.default", "Set as default")
        saved = read_manifest(record.path)
        assert saved["default_variant"] == "3V3"
        assert tree.item("0", "values") == (
            "Default",
            saved["variants"][1]["file"],
            "Current default",
        )
        _click(
            context,
            "implementations-manager.action.save-close",
            "Save & close",
            closes=True,
        )

    try:
        _run_manager(root, record, context, promote)
        assert read_manifest(record.path)["default_variant"] == "3V3"
        assert manifest_path.is_file()
    finally:
        root.destroy()


def test_remove_alternative_updates_manifest_and_removes_unreferenced_file(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    variant_file = record.path / record.manifest["variants"][0]["file"]
    root = _new_root(monkeypatch)
    context = UiTestContext(root)
    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", lambda *_args, **_kwargs: True)

    def remove(_dialog):
        tree = context.require_widget("implementations-manager.rows")
        _select_variant(context, "1", tuple(tree.item("1", "values")))
        _click(context, "implementations-manager.action.remove", "Remove")
        assert tree.get_children() == ("0",)
        assert read_manifest(record.path)["variants"] == []
        assert not variant_file.exists()
        _click(
            context,
            "implementations-manager.action.save-close",
            "Save & close",
            closes=True,
        )

    try:
        _run_manager(root, record, context, remove)
    finally:
        root.destroy()


def test_remove_is_blocked_when_an_implementation_is_referenced_by_a_profile(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(
        tmp_path,
        variants=("1V8",),
        profiles=({"name": "Low voltage", "variant": "1V8"},),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)
    notices = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showinfo",
        lambda title, message, **_kwargs: notices.append((title, message)),
    )

    def guard(_dialog):
        tree = context.require_widget("implementations-manager.rows")
        _select_variant(context, "1", tuple(tree.item("1", "values")))
        _click(context, "implementations-manager.action.remove", "Remove")
        assert len(notices) == 1
        assert notices[0][0] == "Implementation is in use"
        assert "Low voltage" in notices[0][1]
        assert len(read_manifest(record.path)["variants"]) == 1
        _click(context, "implementations-manager.action.close", "Close", closes=True)

    try:
        _run_manager(root, record, context, guard)
    finally:
        root.destroy()


def test_edit_variant_renames_selector_and_preserves_implementation_file(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    current_file = record.path / record.manifest["variants"][0]["file"]
    original_bytes = current_file.read_bytes()
    monkeypatch.setattr(
        plugin_actions,
        "_ask_implementation_details",
        lambda *_args, **_kwargs: ("1V8 low-noise", str(current_file)),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def edit(_dialog):
        tree = context.require_widget("implementations-manager.rows")
        _select_variant(context, "1", tuple(tree.item("1", "values")))
        _click(
            context,
            "implementations-manager.action.edit",
            "Edit implementation",
        )
        saved = read_manifest(record.path)
        assert saved["variants"][0]["value"] == "1V8 low-noise"
        assert saved["variants"][0]["file"] == current_file.name
        assert current_file.read_bytes() == original_bytes
        _click(
            context,
            "implementations-manager.action.save-close",
            "Save & close",
            closes=True,
        )

    try:
        _run_manager(root, record, context, edit)
    finally:
        root.destroy()


def test_add_variant_save_failure_can_be_retried_with_save_and_close(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(tmp_path)
    source = tmp_path / "new-3v3.kicad_sch"
    source.write_text(
        '(kicad_sch (version 20250114) (generator "retry-test"))\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plugin_actions,
        "_ask_implementation_details",
        lambda *_args, **_kwargs: ("3V3", str(source)),
    )
    original_save = plugin_actions._save_block_variants
    attempts = []
    errors = []

    def fail_once(*args, **kwargs):
        attempts.append("attempt")
        if len(attempts) == 1:
            raise OSError("simulated write failure")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(plugin_actions, "_save_block_variants", fail_once)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add_and_retry(_dialog):
        _click(context, "implementations-manager.action.add", "Add variant")
        status = context.require_widget("implementations-manager.status")
        status_variable = str(status.cget("textvariable"))
        assert "Save failed; use Save & close to retry." in status.getvar(status_variable)
        assert len(errors) == 1
        _click(
            context,
            "implementations-manager.action.save-close",
            "Save & close",
            closes=True,
        )

    try:
        opened = _run_manager(root, record, context, add_and_retry)
        saved = read_manifest(record.path)
        assert [item["value"] for item in saved["variants"]] == ["3V3"]
        assert len(attempts) == 2
        assert opened == record.path / saved["variants"][0]["file"]
        assert opened.is_file()
    finally:
        root.destroy()


def test_implementation_manager_selection_controls_follow_row_state(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def assert_state(widget, state: str):
        assert widget.instate([state])

    def inspect_states(_dialog):
        widgets = context.refresh_widgets()
        tree = widgets["implementations-manager.rows"]
        edit = widgets["implementations-manager.action.edit"]
        default = widgets["implementations-manager.action.default"]
        remove = widgets["implementations-manager.action.remove"]

        # Initial selection is the Default row: edit is enabled, but promote
        # and remove are disabled.
        assert tree.selection() == ("0",)
        assert_state(edit, "!disabled")
        assert edit.cget("text") == "Edit default"
        assert_state(default, "disabled")
        assert default.cget("text") == "Default selected"
        assert_state(remove, "disabled")

        # No selected row disables each selection-dependent action.
        tree.selection_remove(*tree.selection())
        tree.event_generate("<<TreeviewSelect>>")
        root.update_idletasks()
        root.update()
        assert tree.selection() == ()
        for widget in (edit, default, remove):
            assert_state(widget, "disabled")
        assert default.cget("text") == "Set as default"

        # Selecting an alternative enables Edit, promotion, and removal.
        tree.selection_set("1")
        tree.event_generate("<<TreeviewSelect>>")
        root.update_idletasks()
        root.update()
        assert_state(edit, "!disabled")
        assert edit.cget("text") == "Edit implementation"
        assert_state(default, "!disabled")
        assert default.cget("text") == "Set as default"
        assert_state(remove, "!disabled")

        # Returning to Default restores its distinct state.
        tree.selection_set("0")
        tree.event_generate("<<TreeviewSelect>>")
        root.update_idletasks()
        root.update()
        assert_state(edit, "!disabled")
        assert_state(default, "disabled")
        assert_state(remove, "disabled")
        for contract_id in (
            "implementations-manager.action.add",
            "implementations-manager.action.close",
            "implementations-manager.action.save-close",
        ):
            assert_state(widgets[contract_id], "!disabled")
        _click(context, "implementations-manager.action.close", "Close", closes=True)

    try:
        result = _run_manager(root, record, context, inspect_states)
        assert result is None
    finally:
        root.destroy()


def test_edit_default_row_returns_default_schematic_and_closes_manager(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    default_path = record.path / record.manifest["implementation_file"]
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def open_default(_dialog):
        assert context.require_widget("implementations-manager.rows").selection() == ("0",)
        _click(
            context,
            "implementations-manager.action.edit",
            "Edit default",
            closes=True,
        )

    try:
        result = _run_manager(root, record, context, open_default)
        assert result == default_path
        assert result.is_file()
    finally:
        root.destroy()


def test_remove_confirmation_cancel_preserves_manifest_row_and_source_file(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    variant_file = record.path / record.manifest["variants"][0]["file"]
    original_manifest = read_manifest(record.path)
    original_bytes = variant_file.read_bytes()
    prompts = []

    def cancel_confirmation(title, message, **_kwargs):
        prompts.append((title, message))
        return False

    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        cancel_confirmation,
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def cancel_remove(_dialog):
        tree = context.require_widget("implementations-manager.rows")
        _select_variant(context, "1", tuple(tree.item("1", "values")))
        _click(context, "implementations-manager.action.remove", "Remove")
        assert prompts == [("Remove implementation", "Remove 1V8?")]
        assert tree.get_children() == ("0", "1")
        assert read_manifest(record.path) == original_manifest
        assert variant_file.read_bytes() == original_bytes
        _click(context, "implementations-manager.action.close", "Close", closes=True)

    try:
        _run_manager(root, record, context, cancel_remove)
    finally:
        root.destroy()


def test_close_confirmation_can_keep_pending_changes_or_discard_them(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(tmp_path)
    source = tmp_path / "pending-implementation.kicad_sch"
    source.write_text(
        '(kicad_sch (version 20250114) (generator "discard-contract"))\n',
        encoding="utf-8",
    )
    original_manifest = read_manifest(record.path)
    monkeypatch.setattr(
        plugin_actions,
        "_ask_implementation_details",
        lambda *_args, **_kwargs: ("3V3", str(source)),
    )
    save_attempts = []

    def fail_save(*_args, **_kwargs):
        save_attempts.append("attempt")
        raise OSError("simulated library write failure")

    monkeypatch.setattr(plugin_actions, "_save_block_variants", fail_save)
    errors = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, str(message))),
    )
    confirmations = []
    answers = iter((False, True))

    def confirm_discard(title, message, **_kwargs):
        confirmations.append((title, message))
        return next(answers)

    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", confirm_discard)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def stage_then_discard(_dialog):
        _click(context, "implementations-manager.action.add", "Add variant")
        status = context.require_widget("implementations-manager.status")
        variable = str(status.cget("textvariable"))
        assert "Save failed; use Save & close to retry." in status.getvar(variable)
        assert save_attempts == ["attempt"]
        assert len(errors) == 1
        assert read_manifest(record.path) == original_manifest

        _click(context, "implementations-manager.action.close", "Close")
        assert confirmations == [
            (
                "Discard implementation changes",
                "Discard the unsaved implementation changes?",
            )
        ]
        assert _manager_dialog(root).winfo_exists()
        assert read_manifest(record.path) == original_manifest

        _click(
            context,
            "implementations-manager.action.close",
            "Close",
            closes=True,
        )
        assert len(confirmations) == 2

    try:
        result = _run_manager(root, record, context, stage_then_discard)
        assert result is None
        assert read_manifest(record.path) == original_manifest
        assert not (Path(record.path) / "implementation_3v3.kicad_sch").exists()
    finally:
        root.destroy()


def test_add_button_opens_real_implementation_details_dialog_and_persists_source(
    monkeypatch, tmp_path
):
    record, _sources = _new_manager_fixture(tmp_path)
    source = tmp_path / "add-dialog-source.kicad_sch"
    source.write_text(
        '(kicad_sch (version 20250114) (generator "nested-dialog-contract"))\n',
        encoding="utf-8",
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)
    nested_callback_errors = []

    def complete_nested_details():
        nested = None
        try:
            nested = next(
                child
                for child in root.winfo_children()
                if child.winfo_class() == "Toplevel"
                and child.title() == "Implementation details"
            )
            assert nested._kicad_ui_screen_id == "implementation-details"
            selector = context.require_widget("implementation-details.variant")
            selector.delete(0, "end")
            selector.insert(0, "5V")
            source_entry = context.require_widget("implementation-details.source")
            source_entry.insert(0, str(source))
            ButtonContract(
                contract_id="implementation-details.action.use",
                expected_action=None,
                destroys_window=False,
                expected_label="Use implementation",
                expected_closed_window_title="Implementation details",
            ).run(context)
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
            nested_callback_errors.append(exc)
            for child in root.winfo_children():
                if child.winfo_class() == "Toplevel":
                    child.destroy()

    def add_from_nested_dialog(_dialog):
        root.after(100, complete_nested_details)
        _click(context, "implementations-manager.action.add", "Add variant")
        if nested_callback_errors:
            raise nested_callback_errors[0]
        tree = context.require_widget("implementations-manager.rows")
        assert tree.get_children() == ("0", "1")
        assert tree.item("1", "values")[0] == "5V"
        saved_variant = read_manifest(record.path)["variants"][0]
        library_source = Path(record.path) / saved_variant["file"]
        assert library_source.is_file()
        assert library_source.read_bytes() == source.read_bytes()
        _click(
            context,
            "implementations-manager.action.save-close",
            "Save & close",
            closes=True,
        )

    try:
        result = _run_manager(root, record, context, add_from_nested_dialog)
        saved_variant = read_manifest(record.path)["variants"][0]
        assert result == Path(record.path) / saved_variant["file"]
        assert result.is_file()
    finally:
        root.destroy()
