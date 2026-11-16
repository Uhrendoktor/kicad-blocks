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
        except Exception as exc:
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
