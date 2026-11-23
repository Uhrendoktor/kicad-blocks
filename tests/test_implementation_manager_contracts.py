"""Real Tk contracts for the implementation variants manager."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from _pytest.monkeypatch import MonkeyPatch

if TYPE_CHECKING:
    from tkinter import Misc, Tk, Toplevel
else:
    Misc = Tk = Toplevel = object

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.manifest_types import (
    BlockProfileInput,
    BlockVariantEditInput,
    BlockVariantInput,
)
from kicad_blocks.repository import BlockRecord
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import create_block, read_manifest
from ui_contracts import (
    Button,
    ButtonContract,
    Misc,
    TkCallbackOption,
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
    "implementations-manager.action.save-close",
    "implementations-manager.action.close",
}
STATUS_TEXT = "Empty KB_VARIANT uses the default implementation."


_load_tkinter = cast(Callable[[], None], getattr(plugin_actions, "_load_tkinter"))
_show_block_variant_editor = cast(
    Callable[..., Path | None], getattr(plugin_actions, "_show_block_variant_editor")
)
_save_block_variants = cast(Callable[..., None], getattr(plugin_actions, "_save_block_variants"))


def _new_manager_fixture(
    tmp_path: Path,
    variants: Sequence[str] = (),
    profiles: Sequence[BlockProfileInput] = (),
) -> tuple[BlockRecord, dict[str, Path]]:
    library_root = tmp_path / "library"
    library_root.mkdir()
    source_root = tmp_path / "sources"
    source_root.mkdir()
    variant_files: list[BlockVariantInput] = []
    sources: dict[str, Path] = {}
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
    _load_tkinter()
    return record, sources


def _new_root(monkeypatch: MonkeyPatch) -> Tk:
    def no_icon(_parent: Misc, _name: str, _stroke_color: str | None = None) -> None:
        return None

    monkeypatch.setattr(plugin_actions, "_create_action_icon", no_icon)
    root = plugin_actions.tk.Tk()
    root.title("UI contract host")
    root.geometry("1280x800")
    root.update()
    return root


def _manager_dialog(root: Tk) -> Toplevel:
    from tkinter import Toplevel as TkToplevel

    child = next(
        item
        for item in root.winfo_children()
        if item.winfo_class() == "Toplevel"
        and cast(TkToplevel, item).title().startswith("Implementations — ")
    )
    return cast(Toplevel, child)


def _run_manager(
    root: Tk,
    record: BlockRecord,
    context: UiTestContext,
    action: Callable[[Toplevel], None],
) -> Path | None:
    callback_errors: list[Exception] = []

    def drive() -> None:
        dialog: Toplevel | None = None
        try:
            dialog = _manager_dialog(root)
            assert getattr(dialog, "_kicad_ui_screen_id", None) == "implementations-manager"
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
                        cast(Toplevel, child).destroy()

    root.after(100, drive)
    result = _show_block_variant_editor(record, root)
    if callback_errors:
        raise callback_errors[0]
    return result


def _select_variant(context: UiTestContext, row_id: str, expected_values: tuple[str, ...]) -> None:
    TreeSelectionContract(
        contract_id="implementations-manager.rows",
        row_id=row_id,
        summary_contract_id="implementations-manager.status",
        expected_summary=STATUS_TEXT,
        expected_values=expected_values,
    ).run(context)


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
        expected_closed_window_title=("Implementations — USB power block" if closes else None),
    ).run(context)


def test_implementation_manager_window_and_all_buttons_are_discoverable(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def inspect_and_close(dialog: Toplevel) -> None:
        assert dialog.winfo_viewable()
        tree = context.require_treeview("implementations-manager.rows")
        assert tree.item("0", "values")[0] == "Default"
        assert tree.item("1", "values")[0] == "1V8"
        assert context.require_button("implementations-manager.action.edit").instate(["!disabled"])
        assert context.require_button("implementations-manager.action.remove").instate(["disabled"])
        _click(context, "implementations-manager.action.close", "Close", closes=True)

    try:
        result = _run_manager(root, record, context, inspect_and_close)
        assert result is None
    finally:
        root.destroy()


def test_set_as_default_promotes_selected_implementation_and_persists_manifest(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(
        tmp_path,
        variants=("1V8", "3V3"),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)
    manifest_path = record.path / "manifest.json"

    def promote(_dialog: Toplevel) -> None:
        tree = context.require_treeview("implementations-manager.rows")
        row_values = tuple(tree.item("2", "values"))
        _select_variant(context, "2", row_values)
        default_button = context.require_button("implementations-manager.action.default")
        assert default_button.cget("text") == "Set as default"
        _click(context, "implementations-manager.action.default", "Set as default")
        saved = read_manifest(record.path)
        assert saved.get("default_variant") == "3V3"
        assert tree.item("0", "values") == (
            "Default",
            saved["variants"][1]["file"],
            "Current default",
        )
        _click(
            context,
            "implementations-manager.action.close",
            "Close",
            closes=True,
        )

    try:
        _run_manager(root, record, context, promote)
        assert read_manifest(record.path).get("default_variant") == "3V3"
        assert manifest_path.is_file()
    finally:
        root.destroy()


def test_remove_alternative_updates_manifest_and_removes_unreferenced_file(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    variant_file = record.path / record.manifest["variants"][0]["file"]
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def confirm_remove(
        _title: str,
        _message: str,
        *,
        parent: Misc | None = None,
    ) -> bool:
        del parent
        return True

    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", confirm_remove)

    def remove(_dialog: Toplevel) -> None:
        tree = context.require_treeview("implementations-manager.rows")
        _select_variant(context, "1", tuple(tree.item("1", "values")))
        _click(context, "implementations-manager.action.remove", "Remove")
        assert tree.get_children() == ("0",)
        assert read_manifest(record.path)["variants"] == []
        assert not variant_file.exists()
        _click(
            context,
            "implementations-manager.action.close",
            "Close",
            closes=True,
        )

    try:
        _run_manager(root, record, context, remove)
    finally:
        root.destroy()


def test_remove_is_blocked_when_an_implementation_is_referenced_by_a_profile(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(
        tmp_path,
        variants=("1V8",),
        profiles=({"name": "Low voltage", "variant": "1V8"},),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)
    notices: list[tuple[str, str]] = []

    def show_info(
        title: str,
        message: str,
        *,
        parent: Misc | None = None,
    ) -> str:
        del parent
        notices.append((title, message))
        return "ok"

    monkeypatch.setattr(plugin_actions.messagebox, "showinfo", show_info)

    def guard(_dialog: Toplevel) -> None:
        tree = context.require_treeview("implementations-manager.rows")
        _select_variant(context, "1", tuple(tree.item("1", "values")))
        _click(context, "implementations-manager.action.remove", "Remove")
        status = context.require_label("implementations-manager.status")
        variable = str(status.cget("textvariable"))
        assert "Low voltage" in str(status.getvar(variable))
        assert len(read_manifest(record.path)["variants"]) == 1
        _click(context, "implementations-manager.action.close", "Close", closes=True)

    try:
        _run_manager(root, record, context, guard)
    finally:
        root.destroy()


def test_edit_variant_renames_selector_and_preserves_implementation_file(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    current_file = record.path / record.manifest["variants"][0]["file"]
    original_bytes = current_file.read_bytes()

    def ask_implementation_details(
        _parent: Misc,
        *,
        value: str = "",
        source: str = "",
        record: BlockRecord | None = None,
        allow_create: bool = False,
    ) -> tuple[str, str] | None:
        del value, source, record, allow_create
        return "1V8 low-noise", str(current_file)

    monkeypatch.setattr(plugin_actions, "_ask_implementation_details", ask_implementation_details)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def edit(_dialog: Toplevel) -> None:
        tree = context.require_treeview("implementations-manager.rows")
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
            "implementations-manager.action.close",
            "Close",
            closes=True,
        )

    try:
        _run_manager(root, record, context, edit)
    finally:
        root.destroy()


def test_add_variant_save_failure_can_be_retried_with_save_and_close(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(tmp_path)
    source = tmp_path / "new-3v3.kicad_sch"
    source.write_text(
        '(kicad_sch (version 20250114) (generator "retry-test"))\n',
        encoding="utf-8",
    )

    def ask_implementation_details(
        _parent: Misc,
        *,
        value: str = "",
        source: str = "",
        record: BlockRecord | None = None,
        allow_create: bool = False,
    ) -> tuple[str, str] | None:
        del value, source, record, allow_create
        return "3V3", str(source_path)

    source_path = source
    monkeypatch.setattr(plugin_actions, "_ask_implementation_details", ask_implementation_details)
    original_save = _save_block_variants
    attempts: list[str] = []
    errors: list[tuple[str, str]] = []

    def fail_once(
        *args: BlockRecord | list[BlockVariantInput] | str | None,
        **kwargs: str | None,
    ) -> None:
        attempts.append("attempt")
        if len(attempts) == 1:
            raise OSError("simulated write failure")
        original_save(record, variants, default_variant=default_variant)

    monkeypatch.setattr(plugin_actions, "_save_block_variants", fail_once)

    def show_error(
        title: str,
        message: str,
        *,
        parent: Misc | None = None,
    ) -> str:
        del parent
        errors.append((title, message))
        return "ok"

    monkeypatch.setattr(plugin_actions.messagebox, "showerror", show_error)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add_and_retry(_dialog: Toplevel) -> None:
        _click(context, "implementations-manager.action.add", "Add variant")
        status = context.require_widget("implementations-manager.status")
        status_variable = str(status.cget("textvariable"))
        assert "Automatic save failed." in str(status.getvar(status_variable))
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
        assert opened is not None
        assert opened == record.path / saved["variants"][0]["file"]
        assert opened.is_file()
    finally:
        root.destroy()


def test_implementation_manager_selection_controls_follow_row_state(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def assert_state(widget: Button, state: str) -> None:
        assert widget.instate([state])

    def inspect_states(_dialog: Toplevel) -> None:
        tree = context.require_treeview("implementations-manager.rows")
        edit = context.require_button("implementations-manager.action.edit")
        default = context.require_button("implementations-manager.action.default")
        remove = context.require_button("implementations-manager.action.remove")

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
        ):
            assert_state(context.require_button(contract_id), "!disabled")
        _click(context, "implementations-manager.action.close", "Close", closes=True)

    try:
        result = _run_manager(root, record, context, inspect_states)
        assert result is None
    finally:
        root.destroy()


def test_edit_default_row_returns_default_schematic_and_closes_manager(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    default_path = record.path / record.manifest["implementation_file"]
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def open_default(_dialog: Toplevel) -> None:
        assert context.require_treeview("implementations-manager.rows").selection() == ("0",)
        _click(
            context,
            "implementations-manager.action.edit",
            "Edit default",
            closes=True,
        )

    try:
        result = _run_manager(root, record, context, open_default)
        assert result == default_path
        assert result is not None
        assert result.is_file()
    finally:
        root.destroy()


def test_remove_confirmation_cancel_preserves_manifest_row_and_source_file(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    variant_file = record.path / record.manifest["variants"][0]["file"]
    original_manifest = read_manifest(record.path)
    original_bytes = variant_file.read_bytes()
    prompts: list[tuple[str, str]] = []

    def cancel_confirmation(title: str, message: str, **_kwargs: TkCallbackOption) -> bool:
        prompts.append((title, message))
        return False

    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        cancel_confirmation,
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def cancel_remove(_dialog: Toplevel) -> None:
        tree = context.require_treeview("implementations-manager.rows")
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
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(tmp_path)
    source = tmp_path / "pending-implementation.kicad_sch"
    source.write_text(
        '(kicad_sch (version 20250114) (generator "retry-on-close"))\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plugin_actions,
        "_ask_implementation_details",
        cast(Callable[..., tuple[str, str]], lambda *_args, **_kwargs: ("3V3", str(source))),
    )
    original_save = _save_block_variants
    attempts: list[str] = []
    errors: list[tuple[str, str]] = []
    confirmations: list[tuple[str, str]] = []
    decisions = iter([False, True])

    def fail_once(
        record: BlockRecord,
        variants: list[BlockVariantEditInput],
        *,
        default_variant: str | None = None,
    ) -> None:
        attempts.append("attempt")
        if len(attempts) == 1:
            raise OSError("simulated library write failure")
        original_save(*args, **kwargs)

    def confirm_discard(
        title: str,
        message: str,
        *,
        parent: Misc | None = None,
    ) -> bool:
        del parent
        confirmations.append((title, message))
        return next(decisions)

    monkeypatch.setattr(plugin_actions, "_save_block_variants", fail_once)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, str(message))),
    )
    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", confirm_discard)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add_then_confirm_discard(_dialog: Toplevel) -> None:
        _click(context, "implementations-manager.action.add", "Add variant")
        status = context.require_widget("implementations-manager.status")
        variable = str(status.cget("textvariable"))
        assert "Automatic save failed" in str(status.getvar(variable))
        assert attempts == ["attempt"]
        assert errors and errors[0][0] == "Save implementations"
        # No retains the pending edit; Yes confirms discarding it.
        _click(context, "implementations-manager.action.close", "Close")
        assert _manager_dialog(root).winfo_exists()
        _click(context, "implementations-manager.action.close", "Close", closes=True)

    try:
        opened = _run_manager(root, record, context, add_then_confirm_discard)
        saved = read_manifest(record.path)
        assert opened is None
        assert saved["variants"] == []
        assert attempts == ["attempt"]
        assert confirmations == [
            (
                "Discard implementation changes",
                "Discard the unsaved implementation changes?",
            ),
            (
                "Discard implementation changes",
                "Discard the unsaved implementation changes?",
            ),
        ]
    finally:
        root.destroy()


def test_add_button_routes_to_create_mode_details_and_persists_source(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, _sources = _new_manager_fixture(tmp_path)
    source_file = tmp_path / "add-dialog-source.kicad_sch"
    source_file.write_text(
        '(kicad_sch (version 20250114) (generator "nested-dialog-contract"))\n',
        encoding="utf-8",
    )
    requests: list[tuple[str, str, str, BlockRecord, bool]] = []

    def ask_details(
        parent: Misc,
        *,
        value: str = "",
        source: str = "",
        record: BlockRecord | None = None,
        allow_create: bool = False,
    ) -> tuple[str, str] | None:
        requests.append((parent.title(), value, source, record, allow_create))
        return "5V", str(source_file)

    monkeypatch.setattr(
        plugin_actions,
        "_ask_implementation_details",
        ask_details,
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add_from_details(_dialog: Toplevel) -> None:
        _click(context, "implementations-manager.action.add", "Add variant")
        assert requests == [
            (
                "Implementations — USB power block",
                "",
                "",
                record,
                True,
            )
        ]
        tree = context.require_treeview("implementations-manager.rows")
        assert tree.get_children() == ("0", "1")
        assert tree.item("1", "values")[0] == "5V"
        saved_variant = read_manifest(record.path)["variants"][0]
        library_source = Path(record.path) / saved_variant["file"]
        assert library_source.is_file()
        assert library_source.read_bytes() == source_file.read_bytes()
        _click(
            context,
            "implementations-manager.action.close",
            "Close",
            closes=True,
        )

    try:
        result = _run_manager(root, record, context, add_from_details)
        saved_variant = read_manifest(record.path)["variants"][0]
        assert result is not None
        assert result == Path(record.path) / saved_variant["file"]
        assert result.is_file()
    finally:
        root.destroy()


def test_add_variant_duplicate_value_reports_error_and_preserves_manifest(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, sources = _new_manager_fixture(tmp_path, variants=("1V8",))
    original = read_manifest(record.path)
    monkeypatch.setattr(
        plugin_actions,
        "_ask_implementation_details",
        cast(Callable[..., tuple[str, str]], lambda *_args, **_kwargs: ("1V8", str(sources["1V8"]))),
    )
    errors: list[tuple[str, str, Misc | None]] = []

    def record_error(title: str, message: str, **kwargs: TkCallbackOption) -> None:
        errors.append((title, str(message), cast(Misc | None, kwargs.get("parent"))))

    monkeypatch.setattr(plugin_actions.messagebox, "showerror", record_error)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add_duplicate(_dialog: Toplevel) -> None:
        _click(context, "implementations-manager.action.add", "Add variant")
        assert len(errors) == 1
        assert errors[0][0] == "Add implementation"
        assert errors[0][1] == "The value '1V8' already exists."
        assert errors[0][2] is _manager_dialog(root)
        assert read_manifest(record.path) == original
        _click(
            context,
            "implementations-manager.action.close",
            "Close",
            closes=True,
        )

    try:
        _run_manager(root, record, context, add_duplicate)
    finally:
        root.destroy()


def test_edit_variant_duplicate_value_reports_error_and_preserves_manifest(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    record, sources = _new_manager_fixture(tmp_path, variants=("1V8", "3V3"))
    original = read_manifest(record.path)
    monkeypatch.setattr(
        plugin_actions,
        "_ask_implementation_details",
        cast(Callable[..., tuple[str, str]], lambda *_args, **_kwargs: ("3V3", str(sources["3V3"]))),
    )
    errors = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append((title, str(message), kwargs.get("parent"))),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def edit_duplicate(_dialog: Toplevel) -> None:
        tree = context.require_treeview("implementations-manager.rows")
        _select_variant(context, "1", tuple(tree.item("1", "values")))
        _click(context, "implementations-manager.action.edit", "Edit implementation")
        assert len(errors) == 1
        assert errors[0][0] == "Edit implementation"
        assert errors[0][1] == "The value '3V3' already exists."
        assert errors[0][2] is _manager_dialog(root)
        assert read_manifest(record.path) == original
        _click(
            context,
            "implementations-manager.action.close",
            "Close",
            closes=True,
        )

    try:
        _run_manager(root, record, context, edit_duplicate)
    finally:
        root.destroy()
