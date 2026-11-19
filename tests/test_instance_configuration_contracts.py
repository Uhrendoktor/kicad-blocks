"""Real Tk execute/result contracts for instance configuration."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parameter_field_name, parse_parameters, parse_pins
from kicad_blocks.repository import BlockRecord, create_block
from ui_contracts import (
    ButtonContract,
    Combobox,
    Entry,
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

WINDOW_TITLE = "Configure X1 — Configuration GUI Block"
INSTANCE_CONTROLS = {
    "instance-configuration.window",
    "instance-configuration.profile",
    "instance-configuration.variant",
    "instance-configuration.parameter.OUTPUT_VOLTAGE",
    "instance-configuration.layout",
    "instance-configuration.action.cancel",
    "instance-configuration.action.save",
}
PROFILE_3V3 = "Standard 3.3 V  [standard_3v3]"
PROFILE_1V8 = "Low voltage compact  [low_voltage_compact]"
LAYOUT_STANDARD = "Standard  [standard]"
LAYOUT_COMPACT = "Compact  [compact]"


def _fixture(
    tmp_path: Path,
) -> tuple[
    Path,
    Path,
    BlockRecord,
    plugin_actions.SchematicSymbol,
    plugin_actions.Schematic,
    SimpleNamespace,
]:
    library = tmp_path / "library"
    project = tmp_path / "project"
    project.mkdir()
    standard_board = tmp_path / "standard.kicad_pcb"
    compact_board = tmp_path / "compact.kicad_pcb"
    implementation_33 = tmp_path / "implementation_3v3.kicad_sch"
    implementation_18 = tmp_path / "implementation_1v8.kicad_sch"
    standard_board.write_text("(kicad_pcb)\n", encoding="utf-8")
    compact_board.write_text("(kicad_pcb)\n", encoding="utf-8")
    implementation_33.write_text(
        '(kicad_sch (version 20250114) (generator "contract-test"))\n',
        encoding="utf-8",
    )
    implementation_18.write_text(
        '(kicad_sch (version 20250114) (generator "contract-test"))\n',
        encoding="utf-8",
    )
    block = create_block(
        library,
        "Configuration GUI Block",
        parse_pins("VIN:input, GND:passive"),
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
                "name": "Standard 3.3 V",
                "variant": "3V3",
                "parameters": {"OUTPUT_VOLTAGE": "3.3"},
                "layout": "standard",
            },
            {
                "id": "low_voltage_compact",
                "name": "Low voltage compact",
                "variant": "1V8",
                "parameters": {"OUTPUT_VOLTAGE": "1.8"},
                "layout": "compact",
            },
        ],
    )
    fields = [
        SimpleNamespace(
            name="KB_PROFILE",
            text=SimpleNamespace(value=""),
        ),
        SimpleNamespace(
            name="KB_VARIANT",
            text=SimpleNamespace(value="3V3"),
        ),
        SimpleNamespace(
            name=parameter_field_name("OUTPUT_VOLTAGE"),
            text=SimpleNamespace(value="3.3"),
        ),
        SimpleNamespace(
            name="KB_LAYOUT",
            text=SimpleNamespace(value="standard"),
        ),
    ]
    instance = cast(
        plugin_actions.SchematicSymbol,
        SimpleNamespace(
            id="00000000-0000-0000-0000-000000000777",
            reference="X1",
            definition=SimpleNamespace(
                id=SimpleNamespace(
                    library=block.manifest["library_nickname"],
                    name=block.manifest["symbol_name"],
                )
            ),
            user_fields=fields,
        ),
    )
    commit = SimpleNamespace()
    schematic = cast(
        plugin_actions.Schematic,
        SimpleNamespace(
            get_selection=lambda: [instance],
            get_project=lambda: SimpleNamespace(expand_text_variables=lambda value: value),
            begin_commit=Mock(return_value=commit),
            push_commit=Mock(),
            drop_commit=Mock(),
            save=Mock(),
        ),
    )
    return library, project, block, instance, schematic, commit


def _new_root(monkeypatch: MonkeyPatch) -> tuple[Tk, UiTestContext]:
    plugin_actions.load_tkinter()
    monkeypatch.setattr(plugin_actions, "_create_action_icon", lambda *_args: None)
    root = plugin_actions.tk.Tk()
    root.title("Instance configuration contract host")
    root.geometry("900x900")
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
) -> None:
    ButtonContract(
        contract_id=contract_id,
        expected_action=None,
        destroys_window=False,
        expected_label=label,
        expected_closed_window_title=WINDOW_TITLE if closes else None,
    ).run(context)
    context.refresh_widgets()


def _run_dialog(
    root: Tk,
    context: UiTestContext,
    library: Path,
    project: Path,
    schematic: plugin_actions.Schematic,
    action: Callable[[Toplevel], None],
) -> None:
    callback_errors: list[Exception] = []

    def drive() -> None:
        dialog: Toplevel | None = None
        try:
            dialog = _find_dialog(root)
            assert dialog.winfo_viewable()
            assert dialog._kicad_ui_screen_id == "instance-configuration"
            context.refresh_widgets()
            assert_control_coverage(dialog, INSTANCE_CONTROLS)
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
    plugin_actions.show_selected_block_configuration(schematic, project, library, root)
    if callback_errors:
        raise callback_errors[0]


def _assert_initial_state(context: UiTestContext, dialog: Toplevel) -> None:
    widgets = context.refresh_widgets()
    assert dialog.title() == WINDOW_TITLE
    assert dialog.winfo_width() >= 580
    assert dialog.winfo_height() >= 500
    for contract_id in INSTANCE_CONTROLS - {"instance-configuration.window"}:
        widget = widgets[contract_id]
        assert widget.winfo_viewable()
    assert (
        context.require_combobox("instance-configuration.profile").get()
        == "Custom (manual settings)"
    )
    assert context.require_combobox("instance-configuration.variant").get() == "3V3"
    assert context.require_entry("instance-configuration.parameter.OUTPUT_VOLTAGE").get() == "3.3"
    assert context.require_combobox("instance-configuration.layout").get() == LAYOUT_STANDARD
    assert context.require_button("instance-configuration.action.cancel").instate(["!disabled"])
    assert context.require_button("instance-configuration.action.save").instate(["!disabled"])


def _install_update_spy(monkeypatch: MonkeyPatch) -> list[str]:
    updates: list[str] = []

    def capture_update(
        _schematic: plugin_actions.Schematic, target: plugin_actions.SchematicSymbol
    ) -> None:
        updates.append(target.reference)

    monkeypatch.setattr(plugin_actions, "_update_schematic_item_checked", capture_update)
    return updates


def test_profile_selection_updates_variant_parameter_and_layout_then_saves_one_ipc_edit(
    monkeypatch: MonkeyPatch, tmp_path
):
    library, project, _block, instance, schematic, commit = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    updates = _install_update_spy(monkeypatch)

    def select_profile_and_save(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog)
        profile = context.require_combobox("instance-configuration.profile")
        profile.set(PROFILE_1V8)
        profile.event_generate("<<ComboboxSelected>>")
        root.update_idletasks()
        root.update()

        assert context.require_combobox("instance-configuration.variant").get() == "1V8"
        assert (
            context.require_entry("instance-configuration.parameter.OUTPUT_VOLTAGE").get() == "1.8"
        )
        assert context.require_combobox("instance-configuration.layout").get() == LAYOUT_COMPACT

        _click(
            context,
            "instance-configuration.action.save",
            "Save instance",
            closes=True,
        )

    try:
        _run_dialog(root, context, library, project, schematic, select_profile_and_save)
        values = {field.name: field.text.value for field in instance.user_fields}
        assert values == {
            "KB_PROFILE": "low_voltage_compact",
            "KB_VARIANT": "1V8",
            "KB_PARAM_OUTPUT_VOLTAGE": "1.8",
            "KB_LAYOUT": "compact",
        }
        assert updates == ["X1"]
        schematic.begin_commit.assert_called_once_with()
        schematic.push_commit.assert_called_once_with(commit, "Configure KiCad block instance")
        schematic.save.assert_called_once_with()
        schematic.drop_commit.assert_not_called()
    finally:
        root.destroy()


def test_manual_parameter_edit_changes_profile_to_custom_and_saves(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    library, project, _block, instance, schematic, commit = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    updates = _install_update_spy(monkeypatch)

    def edit_and_save(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog)
        parameter = context.require_entry("instance-configuration.parameter.OUTPUT_VOLTAGE")
        parameter.delete(0, "end")
        parameter.insert(0, "2.5")
        root.update_idletasks()
        root.update()
        assert (
            context.require_combobox("instance-configuration.profile").get()
            == "Custom (manual settings)"
        )
        _click(
            context,
            "instance-configuration.action.save",
            "Save instance",
            closes=True,
        )

    try:
        _run_dialog(root, context, library, project, schematic, edit_and_save)
        values = {field.name: field.text.value for field in instance.user_fields}
        assert values["KB_PROFILE"] == ""
        assert values["KB_PARAM_OUTPUT_VOLTAGE"] == "2.5"
        assert values["KB_VARIANT"] == "3V3"
        assert values["KB_LAYOUT"] == "standard"
        assert updates == ["X1"]
        schematic.begin_commit.assert_called_once_with()
        schematic.push_commit.assert_called_once_with(commit, "Configure KiCad block instance")
        schematic.save.assert_called_once_with()
    finally:
        root.destroy()


def test_invalid_parameter_shows_error_without_ipc_edit_then_recovers(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    library, project, _block, instance, schematic, _commit = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    updates = _install_update_spy(monkeypatch)
    errors = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, str(message))),
    )

    def recover_from_invalid_parameter(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog)
        parameter = context.require_entry("instance-configuration.parameter.OUTPUT_VOLTAGE")
        parameter.delete(0, "end")
        parameter.insert(0, "not-a-number")
        _click(context, "instance-configuration.action.save", "Save instance")
        assert dialog.winfo_exists()
        assert errors and errors[0][0] == "Invalid Block Configuration"
        schematic.begin_commit.assert_not_called()
        assert updates == []

        parameter.delete(0, "end")
        parameter.insert(0, "1.9")
        _click(
            context,
            "instance-configuration.action.save",
            "Save instance",
            closes=True,
        )

    try:
        _run_dialog(root, context, library, project, schematic, recover_from_invalid_parameter)
        values = {field.name: field.text.value for field in instance.user_fields}
        assert values["KB_PARAM_OUTPUT_VOLTAGE"] == "1.9"
        schematic.begin_commit.assert_called_once_with()
        schematic.push_commit.assert_called_once()
        schematic.save.assert_called_once_with()
        assert updates == ["X1"]
    finally:
        root.destroy()


def test_cancel_and_escape_discard_pending_instance_changes(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    library, project, _block, instance, schematic, _commit = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    updates = _install_update_spy(monkeypatch)
    original_values = {field.name: field.text.value for field in instance.user_fields}

    def cancel_after_edit(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog)
        parameter = context.require_entry("instance-configuration.parameter.OUTPUT_VOLTAGE")
        parameter.delete(0, "end")
        parameter.insert(0, "4.2")
        _click(
            context,
            "instance-configuration.action.cancel",
            "Cancel",
            closes=True,
        )

    try:
        _run_dialog(root, context, library, project, schematic, cancel_after_edit)
        assert {field.name: field.text.value for field in instance.user_fields} == original_values
        assert updates == []
        schematic.begin_commit.assert_not_called()
        schematic.save.assert_not_called()
    finally:
        root.destroy()

    # Escape uses the same discard path without invoking either footer action.
    root, context = _new_root(monkeypatch)

    def escape(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog)
        target = context.require_combobox("instance-configuration.profile")
        target.focus_force()
        root.update()
        assert root.focus_get() is target
        target.event_generate("<Escape>", when="now")
        root.update_idletasks()
        root.update()
        assert not dialog.winfo_exists()

    try:
        _run_dialog(root, context, library, project, schematic, escape)
        assert {field.name: field.text.value for field in instance.user_fields} == original_values
        assert updates == []
        schematic.begin_commit.assert_not_called()
        schematic.save.assert_not_called()
    finally:
        root.destroy()


def test_failed_instance_update_rolls_back_field_values_and_keeps_form_open(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    library, project, _block, instance, schematic, commit = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    errors = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, str(message))),
    )
    original_values = {field.name: field.text.value for field in instance.user_fields}

    def fail_update(
        _schematic: plugin_actions.Schematic, _target: plugin_actions.SchematicSymbol
    ) -> None:
        raise OSError("simulated IPC update failure")

    monkeypatch.setattr(
        plugin_actions,
        "_update_schematic_item_checked",
        fail_update,
    )

    def retry_after_failure(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog)
        parameter = context.require_entry("instance-configuration.parameter.OUTPUT_VOLTAGE")
        parameter.delete(0, "end")
        parameter.insert(0, "2.2")
        _click(context, "instance-configuration.action.save", "Save instance")
        assert dialog.winfo_exists()
        assert errors and errors[0][0] == "Invalid Block Configuration"
        assert {field.name: field.text.value for field in instance.user_fields} == original_values
        schematic.begin_commit.assert_called_once_with()
        schematic.drop_commit.assert_called_once_with(commit)
        schematic.push_commit.assert_not_called()
        schematic.save.assert_not_called()
        _click(
            context,
            "instance-configuration.action.cancel",
            "Cancel",
            closes=True,
        )

    try:
        _run_dialog(root, context, library, project, schematic, retry_after_failure)
    finally:
        root.destroy()


def test_missing_required_configuration_field_fails_before_opening_dialog(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    library, project, _block, instance, schematic, _commit = _fixture(tmp_path)
    root, _context = _new_root(monkeypatch)
    instance.user_fields = [field for field in instance.user_fields if field.name != "KB_LAYOUT"]
    try:
        with pytest.raises(RuntimeError, match="KB_LAYOUT"):
            plugin_actions.show_selected_block_configuration(schematic, project, library, root)
        assert not any(
            child.winfo_class() == "Toplevel" and child.title() == WINDOW_TITLE
            for child in root.winfo_children()
        )
    finally:
        root.destroy()
