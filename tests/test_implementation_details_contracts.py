"""Real Tk interaction contracts for the Implementation details dialog."""

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
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import BlockRecord, create_block
from ui_contracts import (
    ButtonContract,
    MonkeyPatch,
    Misc,
    Tk,
    Toplevel,
    TkCallbackOption,
    UiTestContext,
    assert_control_coverage,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="Real Tk interaction contracts require an X display (run under xvfb-run).",
)

WINDOW_TITLE = "Implementation details"
BASIC_CONTROLS = {
    "implementation-details.window",
    "implementation-details.variant",
    "implementation-details.source-label",
    "implementation-details.source",
    "implementation-details.copy",
    "implementation-details.action.browse",
    "implementation-details.action.cancel",
    "implementation-details.action.use",
}
CREATE_CONTROLS = BASIC_CONTROLS | {"implementation-details.mode"}
MODE_FILE = "Choose a schematic file"
MODE_BLANK = "Create a new empty schematic"
MODE_COPY = "Copy an existing implementation"


def _fixture(tmp_path: Path) -> tuple[BlockRecord, Path, Path, Path]:
    library = tmp_path / "library"
    sources = tmp_path / "sources"
    sources.mkdir()
    first = sources / "implementation_3v3.kicad_sch"
    second = sources / "implementation_1v8.kicad_sch"
    for source in (first, second):
        source.write_text(
            '(kicad_sch (version 20250114) (generator "contract-test"))\n',
            encoding="utf-8",
        )
    record = create_block(
        library,
        "USB power block",
        parse_pins("VIN:input, GND:passive, VOUT:output"),
        variant_files=[
            {"value": "3V3", "path": first},
            {"value": "1V8", "path": second},
        ],
    )
    default_file = Path(record.path) / plugin_actions.resolve_variant_implementation(
        record.manifest, ""
    )
    return record, first, second, default_file


def _new_root(monkeypatch: MonkeyPatch) -> tuple[Tk, UiTestContext]:
    plugin_actions.load_tkinter()
    def no_action_icon(
        _parent: Misc, _name: str, _stroke_color: str | None = None
    ) -> None:
        return None

    monkeypatch.setattr(plugin_actions, "_create_action_icon", no_action_icon)
    root = plugin_actions.tk.Tk()
    root.title("Implementation details contract host")
    root.geometry("900x640")
    root.update()
    return root, UiTestContext(root)


def _find_dialog(root: Tk) -> Toplevel:
    return cast(
        Toplevel,
        next(
            child
            for child in root.winfo_children()
            if child.winfo_class() == "Toplevel" and child.title() == WINDOW_TITLE
        ),
    )


def _find_dialog_exists(root: Tk) -> bool:
    return any(
        child.winfo_class() == "Toplevel" and child.title() == WINDOW_TITLE
        for child in root.winfo_children()
    )


def _run_form(
    root: Tk,
    context: UiTestContext,
    action: Callable[[Toplevel], None],
    *,
    value: str = "",
    source: str = "",
    record: BlockRecord | None = None,
    allow_create: bool = False,
) -> tuple[str, str] | None:
    callback_errors: list[Exception] = []

    def drive() -> None:
        dialog: Toplevel | None = None
        try:
            dialog = _find_dialog(root)
            assert dialog.winfo_viewable()
            assert getattr(dialog, "_kicad_ui_screen_id", None) == "implementation-details"
            context.refresh_widgets()
            expected = CREATE_CONTROLS if allow_create else BASIC_CONTROLS
            assert_control_coverage(dialog, expected)
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
    result = plugin_actions.ask_implementation_details(
        root,
        value=value,
        source=source,
        record=record,
        allow_create=allow_create,
    )
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


def _assert_initial_state(context: UiTestContext, dialog: Toplevel, *, allow_create: bool) -> None:
    widgets = context.refresh_widgets()
    assert dialog.title() == WINDOW_TITLE
    assert dialog.winfo_width() >= 660
    assert dialog.winfo_height() >= 420
    selector = context.require_combobox("implementation-details.variant")
    source_label = widgets["implementation-details.source-label"]
    source = context.require_entry("implementation-details.source")
    browse = widgets["implementation-details.action.browse"]
    copy = widgets["implementation-details.copy"]
    cancel = context.require_button("implementation-details.action.cancel")
    use = context.require_button("implementation-details.action.use")

    assert selector.winfo_viewable()
    assert source_label.cget("text") == "Source schematic (.kicad_sch)"
    assert source.winfo_viewable()
    assert browse.winfo_viewable()
    assert not copy.winfo_viewable()
    assert cancel.winfo_viewable() and cancel.instate(["!disabled"])
    assert use.winfo_viewable() and use.instate(["!disabled"])
    if allow_create:
        mode = context.require_combobox("implementation-details.mode")
        assert mode.winfo_viewable()
        assert tuple(mode.cget("values")) == (MODE_FILE, MODE_BLANK, MODE_COPY)
        assert mode.get() == MODE_FILE
    else:
        assert "implementation-details.mode" not in widgets


def _select_mode(root: Tk, context: UiTestContext, mode: str) -> None:
    combo = context.require_combobox("implementation-details.mode")
    combo.set(mode)
    combo.event_generate("<<ComboboxSelected>>")
    root.update_idletasks()
    root.update()


def test_existing_schematic_source_is_returned_and_non_create_mode_has_no_mode_selector(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "existing.kicad_sch"
    source.write_text("(kicad_sch (version 20250114))\n", encoding="utf-8")
    root, context = _new_root(monkeypatch)

    def accept_existing(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog, allow_create=False)
        assert context.require_combobox("implementation-details.variant").get() == "3V3"
        context.require_entry("implementation-details.source").delete(0, "end")
        context.require_entry("implementation-details.source").insert(0, str(source))
        _click(
            context,
            "implementation-details.action.use",
            "Use implementation",
            closes=True,
        )

    try:
        result = _run_form(
            root,
            context,
            accept_existing,
            value="3V3",
            source="",
            allow_create=False,
        )
        assert result == ("3V3", str(source.resolve()))
    finally:
        root.destroy()


def test_blank_selector_validation_keeps_form_open_and_recovers(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "existing.kicad_sch"
    source.write_text("(kicad_sch (version 20250114))\n", encoding="utf-8")
    root, context = _new_root(monkeypatch)
    errors: list[tuple[str, str]] = []
    def record_error(title: str, message: str, **_kwargs: TkCallbackOption) -> None:
        errors.append((title, str(message)))

    monkeypatch.setattr(plugin_actions.messagebox, "showerror", record_error)

    def recover(_dialog: Toplevel) -> None:
        _assert_initial_state(context, _dialog, allow_create=False)
        selector = context.require_combobox("implementation-details.variant")
        selector.delete(0, "end")
        _click(context, "implementation-details.action.use", "Use implementation")
        assert _find_dialog_exists(root)
        assert errors == [("Implementation details", "Enter an implementation selector.")]

        selector.insert(0, "VIN_1V8")
        context.require_entry("implementation-details.source").insert(0, str(source))
        _click(
            context,
            "implementation-details.action.use",
            "Use implementation",
            closes=True,
        )

    try:
        result = _run_form(root, context, recover, allow_create=False)
        assert result == ("VIN_1V8", str(source.resolve()))
    finally:
        root.destroy()


def test_browse_button_uses_kicad_schematic_picker_and_accepts_selected_file(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "selected.kicad_sch"
    source.write_text("(kicad_sch (version 20250114))\n", encoding="utf-8")
    calls: list[tuple[str, str, str, str | Path]] = []

    def choose_file(parent: Misc, *, title: str, suffix: str, initial_path: Path) -> str:
        calls.append((parent.title(), title, suffix, initial_path))
        return str(source)

    monkeypatch.setattr(plugin_actions, "_pick_kicad_file", choose_file)
    root, context = _new_root(monkeypatch)

    def browse_and_accept(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog, allow_create=False)
        _click(context, "implementation-details.action.browse", "Choose file…")
        assert calls == [
            ("Implementation details", "Choose implementation schematic", ".kicad_sch", "")
        ]
        assert context.require_entry("implementation-details.source").get() == str(source)
        _click(
            context,
            "implementation-details.action.use",
            "Use implementation",
            closes=True,
        )

    try:
        result = _run_form(root, context, browse_and_accept, value="3V3")
        assert result == ("3V3", str(source.resolve()))
    finally:
        root.destroy()


def test_invalid_source_extension_is_rejected_then_browse_recovers(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    invalid = tmp_path / "notes.txt"
    invalid.write_text("not a schematic\n", encoding="utf-8")
    source = tmp_path / "selected.kicad_sch"
    source.write_text("(kicad_sch (version 20250114))\n", encoding="utf-8")
    errors: list[tuple[str, str]] = []
    def record_error(title: str, message: str, **_kwargs: TkCallbackOption) -> None:
        errors.append((title, str(message)))

    monkeypatch.setattr(plugin_actions.messagebox, "showerror", record_error)
    def pick_file(
        _parent: Misc,
        *,
        title: str,
        suffix: str,
        initial_path: str | Path = "",
    ) -> str:
        del title, suffix, initial_path
        return str(source)

    monkeypatch.setattr(plugin_actions, "_pick_kicad_file", pick_file)
    root, context = _new_root(monkeypatch)

    def recover(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog, allow_create=False)
        context.require_entry("implementation-details.source").insert(0, str(invalid))
        _click(context, "implementation-details.action.use", "Use implementation")
        assert _find_dialog_exists(root)
        assert errors == [
            (
                "Implementation details",
                "Choose an existing KiCad .kicad_sch implementation.",
            )
        ]
        _click(context, "implementation-details.action.browse", "Choose file…")
        assert context.require_entry("implementation-details.source").get() == str(source)
        _click(
            context,
            "implementation-details.action.use",
            "Use implementation",
            closes=True,
        )

    try:
        result = _run_form(root, context, recover, value="2V5")
        assert result == ("2V5", str(source.resolve()))
    finally:
        root.destroy()


def test_create_blank_schematic_mode_returns_a_valid_temporary_source(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    record, _first, _second, _default = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)
    created: list[Path] = []
    create_source = cast(Callable[[], Path], getattr(plugin_actions, "_new_blank_schematic_source"))

    def capture_blank_source() -> Path:
        path = Path(create_source())
        created.append(path)
        return path

    monkeypatch.setattr(
        plugin_actions,
        "_new_blank_schematic_source",
        capture_blank_source,
    )

    def choose_blank(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog, allow_create=True)
        _select_mode(root, context, MODE_BLANK)
        assert (
            context.require_widget("implementation-details.source-label").cget("text")
            == "The new implementation starts as a blank KiCad schematic."
        )
        assert not context.require_entry("implementation-details.source").winfo_viewable()
        assert not context.require_widget("implementation-details.action.browse").winfo_viewable()
        _click(
            context,
            "implementation-details.action.use",
            "Use implementation",
            closes=True,
        )

    try:
        result = _run_form(
            root,
            context,
            choose_blank,
            value="2V5",
            record=record,
            allow_create=True,
        )
        assert result == ("2V5", str(created[0]))
        contents = created[0].read_text(encoding="utf-8")
        assert "(kicad_sch" in contents
        assert "(lib_symbols)" in contents
        assert created[0].suffix == ".kicad_sch"
    finally:
        for path in created:
            path.unlink(missing_ok=True)
        root.destroy()


def test_copy_existing_implementation_uses_selected_library_variant(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    record, first, second, _default = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)

    def choose_copy(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog, allow_create=True)
        _select_mode(root, context, MODE_COPY)
        assert (
            context.require_widget("implementation-details.source-label").cget("text")
            == "Copy this library implementation:"
        )
        assert not context.require_entry("implementation-details.source").winfo_viewable()
        assert not context.require_widget("implementation-details.action.browse").winfo_viewable()
        copy = context.require_combobox("implementation-details.copy")
        assert copy.winfo_viewable()
        assert tuple(copy.cget("values")) == ("Default", "3V3", "1V8")
        copy.set("1V8")
        _click(
            context,
            "implementation-details.action.use",
            "Use implementation",
            closes=True,
        )

    try:
        result = _run_form(
            root,
            context,
            choose_copy,
            value="1V8",
            record=record,
            allow_create=True,
        )
        selected_variant = next(
            item for item in record.manifest["variants"] if item["value"] == "1V8"
        )
        expected_library_source = Path(record.path) / selected_variant["file"]
        assert result == ("1V8", str(expected_library_source.resolve()))
        assert expected_library_source.is_file()
        assert first.is_file()
        assert second.is_file()
    finally:
        root.destroy()


def test_missing_copy_source_is_rejected_and_can_switch_to_existing_variant(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    record, _first, second, default = _fixture(tmp_path)
    default.unlink()
    root, context = _new_root(monkeypatch)
    errors: list[tuple[str, str]] = []
    def record_error(title: str, message: str, **_kwargs: TkCallbackOption) -> None:
        errors.append((title, str(message)))

    monkeypatch.setattr(plugin_actions.messagebox, "showerror", record_error)

    def recover_copy(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog, allow_create=True)
        _select_mode(root, context, MODE_COPY)
        copy = context.require_button("implementation-details.copy")
        assert copy.get() == "Default"
        _click(context, "implementation-details.action.use", "Use implementation")
        assert _find_dialog_exists(root)
        assert errors == [
            (
                "Implementation details",
                "Choose an existing library implementation to copy.",
            )
        ]

        copy.set("3V3")
        _click(
            context,
            "implementation-details.action.use",
            "Use implementation",
            closes=True,
        )

    try:
        result = _run_form(
            root,
            context,
            recover_copy,
            value="3V3",
            record=record,
            allow_create=True,
        )
        assert result == ("3V3", str(_first_path(record).resolve()))
        assert second.is_file()
    finally:
        root.destroy()


def _first_path(record: BlockRecord) -> Path:
    variants = record.manifest.get("variants", [])
    variant = next(item for item in variants if item["value"] == "3V3")
    return Path(record.path) / str(variant["file"])


def test_cancel_and_escape_leave_library_sources_unchanged(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    record, _first, _second, default = _fixture(tmp_path)
    root, context = _new_root(monkeypatch)

    def cancel(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog, allow_create=True)
        _click(
            context,
            "implementation-details.action.cancel",
            "Cancel",
            closes=True,
        )

    try:
        result = _run_form(root, context, cancel, value="1V8", record=record, allow_create=True)
        assert result is None
        assert default.is_file()
    finally:
        root.destroy()

    root, context = _new_root(monkeypatch)

    def escape(dialog: Toplevel) -> None:
        _assert_initial_state(context, dialog, allow_create=True)
        selector = context.require_combobox("implementation-details.variant")
        selector.focus_force()
        root.update()
        assert root.focus_get() is selector
        selector.event_generate("<Escape>", when="now")
        root.update_idletasks()
        root.update()
        assert not _find_dialog_exists(root)

    try:
        result = _run_form(root, context, escape, value="1V8", record=record, allow_create=True)
        assert result is None
        assert default.is_file()
    finally:
        root.destroy()
