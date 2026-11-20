"""Real Tk interaction contracts for library and PCB layout managers."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TypedDict, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from _pytest.monkeypatch import MonkeyPatch
    from kicad_blocks.manifest_types import BlockLayoutEditInput, BlockLayoutInput, BlockVariantInput

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions  # noqa: E402
from kicad_blocks import repository  # noqa: E402
from kicad_blocks.model import parse_pins  # noqa: E402
from kicad_blocks.repository import BlockRecord, create_block, read_manifest  # noqa: E402
from ui_contracts import (  # noqa: E402
    ButtonContract,
    Misc,
    Tk,
    Toplevel,
    UiTestContext,
    assert_control_coverage,
    TkCallbackOption,
    TkOption,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="Real Tk interaction contracts require an X display (run under xvfb-run).",
)

LIBRARY_CONTROLS = {
    "manage-libraries.window",
    "manage-libraries.rows",
    "manage-libraries.status",
    "manage-libraries.action.add",
    "manage-libraries.action.create",
    "manage-libraries.action.change",
    "manage-libraries.action.unregister",
    "manage-libraries.action.default",
    "manage-libraries.action.close",
}
PCB_CONTROLS = {
    "pcb-layout-manager.window",
    "pcb-layout-manager.rows",
    "pcb-layout-manager.status",
    "pcb-layout-manager.implementation",
    "pcb-layout-manager.action.add",
    "pcb-layout-manager.action.remove",
    "pcb-layout-manager.action.default",
    "pcb-layout-manager.action.assign",
    "pcb-layout-manager.action.close",
}


type _RecordedMessage = tuple[str, str, Toplevel | None]
type _RecordedPrompt = tuple[str, str, Toplevel | None]
type _ChooserOptions = dict[str, TkCallbackOption]


class _LayoutDetails(TypedDict):
    name: str
    source: str
    linked_implementation: str


def _record_message(calls: list[_RecordedMessage]) -> Callable[..., None]:
    def show(title: str, message: str, **kwargs: TkCallbackOption) -> None:
        parent = cast(Toplevel | None, kwargs.get("parent"))
        calls.append((title, str(message), parent))

    return show


def _record_question(
    calls: list[tuple[str, str]],
    answer: bool,
) -> Callable[..., bool]:
    def ask(title: str, message: str, **_kwargs: TkCallbackOption) -> bool:
        calls.append((title, message))
        return answer

    return ask


def _directory_dialog_result(path: Path) -> Callable[..., str]:
    def choose(**_kwargs: TkCallbackOption) -> str:
        return str(path)

    return choose



def _record_question_string(
    calls: list[_RecordedPrompt],
    answer: str | None,
) -> Callable[..., str | None]:
    def ask(title: str, prompt: str, **kwargs: TkCallbackOption) -> str | None:
        calls.append((title, prompt, cast(Toplevel | None, kwargs.get("parent"))))
        return answer

    return ask


def _directory_picker_result(path: Path) -> Callable[..., str]:
    def choose(
        _parent: Misc,
        *,
        title: str,
        initial_path: str | Path | None = None,
    ) -> str:
        return str(path)

    return choose
def _layout_details_result(
    name: str,
    source: Path,
    linked_implementation: str,
) -> Callable[[Misc, BlockRecord], _LayoutDetails]:
    def choose(_parent: Misc, _record: BlockRecord) -> _LayoutDetails:
        return {
            "name": name,
            "source": str(source),
            "linked_implementation": linked_implementation,
        }

    return choose
def _raise_path_error(message: str) -> Callable[[Path], NoReturn]:
    def fail(_path: Path) -> NoReturn:
        raise OSError(message)

    return fail
def _return_path_name(value: str) -> Callable[..., str | None]:
    def ask(_title: str, _prompt: str, **_kwargs: TkCallbackOption) -> str | None:
        return value

    return ask

def _parent_title(parent: Toplevel | None) -> str:
    assert parent is not None
    return parent.title()


def _new_root(monkeypatch: MonkeyPatch) -> Tk:
    plugin_actions.load_tkinter()

    def no_icon(_parent: Misc, _name: str, _stroke_color: str | None = None) -> None:
        return None

    monkeypatch.setattr(plugin_actions, "_create_action_icon", no_icon)
    root = plugin_actions.tk.Tk()
    root.title("UI contract host")
    root.geometry("1280x800")
    root.update()
    return root


def _find_dialog(root: Tk, title: str) -> Toplevel:
    return next(
        cast(Toplevel, child)
        for child in root.winfo_children()
        if child.winfo_class() == "Toplevel" and cast(Toplevel, child).title() == title
    )


@dataclass(frozen=True)
class _ModalInvocation[T]:
    root: Tk
    context: UiTestContext
    title: str
    expected_ids: set[str]
    action: Callable[[Toplevel], None]
    invoke: Callable[[], T]


def _run_modal[T](spec: _ModalInvocation[T]) -> T:
    callback_errors: list[Exception] = []

    def drive() -> None:
        dialog: Toplevel | None = None
        try:
            dialog = _find_dialog(spec.root, spec.title)
            assert dialog.winfo_viewable()
            spec.context.refresh_widgets()
            assert_control_coverage(dialog, spec.expected_ids)
            spec.action(dialog)
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

    spec.root.after(100, drive)
    result = spec.invoke()
    if callback_errors:
        raise callback_errors[0]
    return result


def _click(
    context: UiTestContext,
    contract_id: str,
    label: str,
    *,
    closes: bool = False,
    title: str | None = None,
) -> None:
    ButtonContract(
        contract_id=contract_id,
        expected_action=None,
        destroys_window=False,
        expected_label=label,
        expected_closed_window_title=title if closes else None,
    ).run(context)


def _select_path(context: UiTestContext, tree_id: str, path: Path, *, path_column: str) -> str:
    tree = context.require_treeview(tree_id)
    wanted = str(path.expanduser().resolve())
    row_id = next(iid for iid in tree.get_children() if str(tree.set(iid, path_column)) == wanted)
    tree.selection_set(row_id)
    tree.focus(row_id)
    tree.event_generate("<<TreeviewSelect>>")
    context.root.update_idletasks()
    context.root.update()
    return row_id


def _library_fixture(monkeypatch: MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(repository, "settings_file", lambda: settings)
    monkeypatch.setenv("KICAD_BLOCKS_LIBRARY", "")

    first = tmp_path / "library-one"
    second = tmp_path / "library-two"
    first.mkdir()
    second.mkdir()
    (first / "keep-me.txt").write_text("preserve library data", encoding="utf-8")
    repository.add_library_root(first, make_default=True)
    repository.add_library_root(second)
    return settings, first, second


def _run_library_manager(
    root: Tk, context: UiTestContext, action: Callable[[Toplevel], None]
) -> bool:
    return _run_modal(
        _ModalInvocation(
            root=root,
            context=context,
            title="Manage Libraries",
            expected_ids=LIBRARY_CONTROLS,
            action=action,
            invoke=lambda: plugin_actions.show_library_roots_dialog(root),
        )
    )


def test_manage_libraries_add_existing_registers_and_refreshes_the_real_tree(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify adding an existing library refreshes the manager."""
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    new_library = tmp_path / "third-library"
    new_library.mkdir()
    chooser_calls: list[_ChooserOptions] = []
    monkeypatch.setattr(
        plugin_actions.filedialog,
        "askdirectory",
        _directory_dialog_result(new_library),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add(_dialog: Toplevel) -> None:
        _click(context, "manage-libraries.action.add", "Add library")
        assert chooser_calls[0]["title"] == "Add existing block library folder"
        assert cast(Toplevel, chooser_calls[0]["parent"]).title() == "Manage Libraries"
        assert new_library.resolve() in repository.library_roots()
        _select_path(context, "manage-libraries.rows", new_library, path_column="path")
        status = context.require_widget("manage-libraries.status")
        variable = str(status.cget("textvariable"))
        assert "registered libraries" in str(status.getvar(variable))
        _click(
            context,
            "manage-libraries.action.close",
            "Close",
            closes=True,
            title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, add)
        assert first in repository.library_roots()
        assert second in repository.library_roots()
        assert new_library in repository.library_roots()
    finally:
        root.destroy()


def test_manage_libraries_create_makes_and_registers_new_folder(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify library creation persists and refreshes the manager."""
    _settings, _first, _second = _library_fixture(monkeypatch, tmp_path)
    destination = tmp_path / "created-libraries"
    destination.mkdir()
    monkeypatch.setattr(
        plugin_actions.filedialog, "askdirectory", _directory_dialog_result(destination)
    )
    ask_calls: list[_RecordedPrompt] = []
    monkeypatch.setattr(
        plugin_actions.simpledialog,
        "askstring",
        _record_question_string(ask_calls, "new-library"),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)
    created = destination / "new-library"

    def create(_dialog: Toplevel) -> None:
        _click(context, "manage-libraries.action.create", "Create library")
        assert ask_calls[0][0] == "Create block library"
        assert _parent_title(ask_calls[0][2]) == "Manage Libraries"
        assert created.is_dir()
        assert created.resolve() in repository.library_roots()
        _click(
            context,
            "manage-libraries.action.close",
            "Close",
            closes=True,
            title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, create)
    finally:
        root.destroy()


def test_manage_libraries_unregister_removes_registration_but_preserves_files(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Verify unregister preserves library files."""
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", _record_question([], True))
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def unregister(_dialog: Toplevel) -> None:
        _select_path(context, "manage-libraries.rows", second, path_column="path")
        button = context.require_button("manage-libraries.action.unregister")
        assert button.instate(["!disabled"])
        _click(context, "manage-libraries.action.unregister", "Unregister")
        assert second.resolve() not in repository.library_roots()
        assert (second / ".gitignore").is_file()
        assert (first / "keep-me.txt").read_text(encoding="utf-8") == "preserve library data"
        _click(
            context,
            "manage-libraries.action.close",
            "Close",
            closes=True,
            title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, unregister)
    finally:
        root.destroy()


def test_manage_libraries_confirmation_cancel_keeps_registration(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify cancel keeps the library registered."""
    _settings, _first, second = _library_fixture(monkeypatch, tmp_path)
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        _record_question(prompts, False),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def cancel(_dialog: Toplevel) -> None:
        _select_path(context, "manage-libraries.rows", second, path_column="path")
        _click(context, "manage-libraries.action.unregister", "Unregister")
        assert prompts
        assert prompts[0][0] == "Unregister library"
        assert second.resolve() in repository.library_roots()
        _click(
            context,
            "manage-libraries.action.close",
            "Close",
            closes=True,
            title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, cancel)
    finally:
        root.destroy()


def test_manage_libraries_set_default_persists_selected_library(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify selecting a default library persists the setting."""
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def set_default(_dialog: Toplevel) -> None:
        _select_path(context, "manage-libraries.rows", second, path_column="path")
        button = context.require_button("manage-libraries.action.default")
        assert button.instate(["!disabled"])
        _click(context, "manage-libraries.action.default", "Set default")
        assert repository.default_library_root() == second.resolve()
        tree = context.require_treeview("manage-libraries.rows")
        selected = tree.selection()[0]
        assert tree.set(selected, "role") == "Default"
        assert context.require_button("manage-libraries.action.default").instate(["disabled"])
        _click(
            context,
            "manage-libraries.action.close",
            "Close",
            closes=True,
            title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, set_default)
        assert repository.default_library_root() == second.resolve()
        assert first.resolve() in repository.library_roots()
    finally:
        root.destroy()


def test_manage_libraries_change_folder_is_transactional_and_keeps_old_data(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify changing a library folder preserves the old data."""
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    replacement = tmp_path / "replacement-library"
    replacement.mkdir()
    monkeypatch.setattr(
        plugin_actions.filedialog,
        "askdirectory",
        _directory_dialog_result(replacement),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def change(_dialog: Toplevel) -> None:
        _select_path(context, "manage-libraries.rows", first, path_column="path")
        _click(context, "manage-libraries.action.change", "Change folder")
        roots = repository.library_roots()
        assert first.resolve() not in roots
        assert replacement.resolve() in roots
        assert repository.default_library_root() == replacement.resolve()
        assert (first / "keep-me.txt").read_text(encoding="utf-8") == "preserve library data"
        assert second.resolve() in roots
        _click(
            context,
            "manage-libraries.action.close",
            "Close",
            closes=True,
            title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, change)
    finally:
        root.destroy()


def test_manage_libraries_environment_override_and_empty_selection_states(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify environment overrides disable selection actions."""
    _settings, first, _second = _library_fixture(monkeypatch, tmp_path)
    monkeypatch.setenv("KICAD_BLOCKS_LIBRARY", str(first))
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def inspect(_dialog: Toplevel) -> None:
        fixed_actions = (
            "manage-libraries.action.change",
            "manage-libraries.action.unregister",
            "manage-libraries.action.default",
        )
        for button_id in fixed_actions:
            assert context.require_button(button_id).instate(["disabled"])
        _select_path(context, "manage-libraries.rows", first, path_column="path")
        for button_id in fixed_actions:
            assert context.require_button(button_id).instate(["disabled"])
        for button_id in (
            "manage-libraries.action.add",
            "manage-libraries.action.create",
        ):
            assert context.require_button(button_id).instate(["!disabled"])
        _click(
            context,
            "manage-libraries.action.close",
            "Close",
            closes=True,
            title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, inspect)
    finally:
        root.destroy()


def _pcb_fixture(
    tmp_path: Path, *, layout_count: int = 2, with_variant: bool = True
) -> tuple[BlockRecord, list[Path]]:
    library = tmp_path / "pcb-library"
    library.mkdir()
    source_boards: list[Path] = []
    for index in range(layout_count):
        board = tmp_path / f"board-{index + 1}.kicad_pcb"
        board.write_text(
            '(kicad_pcb (version 20241229) (generator "contract-test"))\n',
            encoding="utf-8",
        )
        source_boards.append(board)
    variants: list[BlockVariantInput] = []
    if with_variant:
        variant_source = tmp_path / "boost.kicad_sch"
        variant_source.write_text(
            '(kicad_sch (version 20250114) (generator "contract-test"))\n',
            encoding="utf-8",
        )
        variants.append({"value": "Boost", "path": variant_source})
    layout_files: list[BlockLayoutInput] = [
        {"id": f"layout_{index + 1}", "name": f"Layout {index + 1}", "path": board}
        for index, board in enumerate(source_boards)
    ]
    record = create_block(
        library,
        "PCB Contract Block",
        parse_pins("VIN:input, GND:passive, VOUT:output"),
        variant_files=variants,
        layout_files=layout_files,
    )
    return record, source_boards


def _run_pcb_manager(
    root: Tk, context: UiTestContext, record: BlockRecord, action: Callable[[Toplevel], None]
) -> Path | None:
    return _run_modal(
        _ModalInvocation(
            root=root,
            context=context,
            title=f"PCB layouts — {record.name}",
            expected_ids=PCB_CONTROLS,
            action=action,
            invoke=lambda: plugin_actions.show_block_layout_editor(record, root),
        )
    )


def _select_pcb_row(context: UiTestContext, row_id: str) -> None:
    tree = context.require_treeview("pcb-layout-manager.rows")
    tree.selection_set(row_id)
    tree.focus(row_id)
    tree.event_generate("<<TreeviewSelect>>")
    context.root.update_idletasks()
    context.root.update()


def test_pcb_layout_manager_updates_default_and_variant_associations_then_removes(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Verify default and variant layout associations persist."""
    record, _sources = _pcb_fixture(tmp_path, layout_count=2)
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        _record_question(prompts, True),
    )
    original_manifest = read_manifest(record.path)
    original_layouts = original_manifest["layouts"]
    first_id = original_layouts[0]["id"]
    second = original_layouts[1]
    second_path = record.path / second["file"]
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def maintain(_dialog: Toplevel) -> None:
        _select_pcb_row(context, "1")
        _click(context, "pcb-layout-manager.action.default", "Set default")
        manifest = read_manifest(record.path)
        assert manifest["default_layout"] == second["id"]

        combo = context.require_combobox("pcb-layout-manager.implementation")
        combo.set("Boost")
        _click(context, "pcb-layout-manager.action.assign", "Assign")
        manifest = read_manifest(record.path)
        variant = next(item for item in manifest["variants"] if item["value"] == "Boost")
        assert variant.get("layout_id") == second["id"]
        selected_layout = next(item for item in manifest["layouts"] if item["id"] == second["id"])
        assert "Boost" in selected_layout.get("implementations", [])

        _click(context, "pcb-layout-manager.action.remove", "Remove")
        manifest = read_manifest(record.path)
        assert len(manifest["layouts"]) == 1
        assert manifest["default_layout"] == first_id
        assert not second_path.exists()
        assert manifest["variants"][0].get("layout_id") == first_id
        assert prompts
        assert prompts[0][0] == "Remove PCB layout"
        _click(
            context,
            "pcb-layout-manager.action.close",
            "Close",
            closes=True,
            title=f"PCB layouts — {record.name}",
        )

    try:
        _run_pcb_manager(root, context, record, maintain)
    finally:
        root.destroy()


def test_pcb_layout_manager_add_and_save_failure_can_be_retried(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify layout save failures can be retried."""
    record, _sources = _pcb_fixture(tmp_path, layout_count=1)
    new_board = tmp_path / "new-boost.kicad_pcb"
    new_board.write_text(
        '(kicad_pcb (version 20241229) (generator "contract-test"))\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plugin_actions,
        "_ask_layout_details",
        _layout_details_result("Boost layout", new_board, "Boost"),
    )
    original_save = plugin_actions.save_block_layouts
    attempts: list[str] = []
    errors: list[_RecordedMessage] = []

    def fail_once(
        record: BlockRecord,
        layouts: list[BlockLayoutEditInput],
        *,
        default_layout_id: str | None,
        variant_layouts: dict[str, str] | None = None,
        layout_implementations: dict[str, list[str]] | None = None,
    ) -> BlockRecord:
        attempts.append("save")
        if len(attempts) == 1:
            message = "simulated PCB layout write failure"
            raise OSError(message)
        return original_save(
            record,
            layouts,
            default_layout_id=default_layout_id,
            variant_layouts=variant_layouts,
            layout_implementations=layout_implementations,
        )

    monkeypatch.setattr(plugin_actions, "_save_block_layouts", fail_once)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        _record_message(errors),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add_and_retry(_dialog: Toplevel) -> None:
        _click(context, "pcb-layout-manager.action.add", "Add layout")
        tree = context.require_treeview("pcb-layout-manager.rows")
        assert tree.item("1", "values")[2] == "new-boost.kicad_pcb"
        status = context.require_widget("pcb-layout-manager.status")
        variable = str(status.cget("textvariable"))
        assert "Save failed; use Save & close to retry." in str(status.getvar(variable))
        assert errors
        assert errors[0][0] == "Save PCB layouts"
        assert len(read_manifest(record.path)["layouts"]) == 1
        _click(
            context,
            "pcb-layout-manager.action.save-close",
            "Save & close",
            closes=True,
            title=f"PCB layouts — {record.name}",
        )

    try:
        opened = _run_pcb_manager(root, context, record, add_and_retry)
        manifest = read_manifest(record.path)
        assert len(manifest["layouts"]) == 2
        added = next(item for item in manifest["layouts"] if item["name"] == "Boost layout")
        assert opened is not None
        assert "Boost" in added.get("implementations", [])
        assert added["file"] == opened.name
        assert opened.is_file()
        assert opened.read_bytes() == new_board.read_bytes()
        assert len(attempts) == 2
    finally:
        root.destroy()


def test_pcb_layout_manager_cancelled_remove_preserves_board_and_manifest(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify cancelling layout removal preserves files."""
    record, _sources = _pcb_fixture(tmp_path, layout_count=2)
    before = read_manifest(record.path)
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        _record_question(prompts, False),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def cancel(_dialog: Toplevel) -> None:
        _select_pcb_row(context, "1")
        second_file = record.path / before["layouts"][1]["file"]
        _click(context, "pcb-layout-manager.action.remove", "Remove")
        assert second_file.is_file()
        assert read_manifest(record.path)["layouts"] == before["layouts"]
        assert prompts
        assert prompts[0][0] == "Remove PCB layout"
        _click(
            context,
            "pcb-layout-manager.action.close",
            "Close",
            closes=True,
            title=f"PCB layouts — {record.name}",
        )

    try:
        _run_pcb_manager(root, context, record, cancel)
    finally:
        root.destroy()


def test_pcb_layout_manager_disables_selection_actions_when_empty(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify empty layouts disable selection actions."""
    record, _sources = _pcb_fixture(tmp_path, layout_count=0, with_variant=False)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def inspect(_dialog: Toplevel) -> None:
        assert context.require_button("pcb-layout-manager.action.remove").instate(["disabled"])
        assert context.require_button("pcb-layout-manager.action.default").instate(["disabled"])
        assert context.require_button("pcb-layout-manager.action.assign").instate(["disabled"])
        assert context.require_button("pcb-layout-manager.action.add").instate(["!disabled"])
        _click(
            context,
            "pcb-layout-manager.action.close",
            "Close",
            closes=True,
            title=f"PCB layouts — {record.name}",
        )

    try:
        _run_pcb_manager(root, context, record, inspect)
    finally:
        root.destroy()


def test_pcb_layout_manager_duplicate_name_reports_error_without_mutating_manifest(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Verify duplicate layout names do not mutate the manifest."""
    record, _sources = _pcb_fixture(tmp_path, layout_count=2)
    original = read_manifest(record.path)
    duplicate = original["layouts"][0]["name"]
    board = tmp_path / "duplicate-source.kicad_pcb"
    board.write_text("(kicad_pcb)\\n", encoding="utf-8")
    monkeypatch.setattr(
        plugin_actions,
        "_ask_layout_details",
        _layout_details_result(duplicate, board, "Default"),
    )
    errors: list[_RecordedMessage] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        _record_message(errors),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add_duplicate(_dialog: Toplevel) -> None:
        _click(context, "pcb-layout-manager.action.add", "Add layout")
        assert len(errors) == 1
        assert errors[0][0] == "Add PCB layout"
        assert f"layout named {duplicate!r} already exists" in errors[0][1]
        assert cast(Toplevel, errors[0][2]).title() == f"PCB layouts — {record.name}"
        assert read_manifest(record.path) == original
        _click(
            context,
            "pcb-layout-manager.action.close",
            "Close",
            closes=True,
            title=f"PCB layouts — {record.name}",
        )

    try:
        _run_pcb_manager(root, context, record, add_duplicate)
    finally:
        root.destroy()


def test_manage_libraries_add_registration_error_keeps_existing_roots(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify registration failure preserves existing roots."""
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    registered_before = set(repository.library_roots())
    assert {first.resolve(), second.resolve()} <= registered_before
    candidate = tmp_path / "candidate-library"
    candidate.mkdir()
    monkeypatch.setattr(plugin_actions, "_pick_directory", _directory_picker_result(candidate))

    def fail_add(_path: Path) -> None:
        message = "permission denied while registering library"
        raise OSError(message)

    monkeypatch.setattr(plugin_actions, "add_library_root", fail_add)
    errors: list[_RecordedMessage] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        _record_message(errors),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add(_dialog: Toplevel) -> None:
        _click(context, "manage-libraries.action.add", "Add library")
        assert len(errors) == 1
        assert errors[0][0] == "Add existing library"
        assert errors[0][1] == "permission denied while registering library"
        assert cast(Toplevel, errors[0][2]).title() == "Manage Libraries"
        assert set(repository.library_roots()) == registered_before
        assert candidate.resolve() not in repository.library_roots()
        _click(
            context, "manage-libraries.action.close", "Close", closes=True, title="Manage Libraries"
        )

    try:
        _run_library_manager(root, context, add)
    finally:
        root.destroy()


def test_manage_libraries_create_rejects_path_like_folder_name(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify path-like folder names are rejected."""
    _settings, _first, _second = _library_fixture(monkeypatch, tmp_path)
    destination = tmp_path / "created"
    destination.mkdir()
    monkeypatch.setattr(plugin_actions, "_pick_directory", _directory_picker_result(destination))
    monkeypatch.setattr(
        plugin_actions.simpledialog,
        "askstring",
        _return_path_name("../outside"),
    )
    errors: list[_RecordedMessage] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        _record_message(errors),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def create(_dialog: Toplevel) -> None:
        _click(context, "manage-libraries.action.create", "Create library")
        assert len(errors) == 1
        assert errors[0][0] == "Create block library"
        assert errors[0][1] == "Enter a single folder name without a path."
        assert _parent_title(errors[0][2]) == "Manage Libraries"
        assert not (tmp_path / "outside").exists()
        _click(
            context, "manage-libraries.action.close", "Close", closes=True, title="Manage Libraries"
        )

    try:
        _run_library_manager(root, context, create)
    finally:
        root.destroy()


def test_manage_libraries_create_filesystem_error_keeps_existing_folder(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify failed folder creation preserves existing data."""
    _settings, _first, _second = _library_fixture(monkeypatch, tmp_path)
    destination = tmp_path / "created"
    existing = destination / "already-there"
    destination.mkdir()
    existing.mkdir()
    marker = existing / "preserve.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(plugin_actions, "_pick_directory", _directory_picker_result(destination))
    monkeypatch.setattr(
        plugin_actions.simpledialog,
        "askstring",
        _return_path_name("already-there"),
    )
    errors: list[_RecordedMessage] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        _record_message(errors),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def create(_dialog: Toplevel) -> None:
        _click(context, "manage-libraries.action.create", "Create library")
        assert len(errors) == 1
        assert errors[0][0] == "Create block library"
        assert "exists" in errors[0][1].lower()
        assert _parent_title(errors[0][2]) == "Manage Libraries"
        assert marker.read_text(encoding="utf-8") == "keep"
        assert existing.resolve() not in repository.library_roots()
        _click(
            context, "manage-libraries.action.close", "Close", closes=True, title="Manage Libraries"
        )

    try:
        _run_library_manager(root, context, create)
    finally:
        root.destroy()


def test_manage_libraries_change_folder_rolls_back_after_old_root_removal_fails(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Verify failed folder changes restore the original root."""
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    monkeypatch.setattr(plugin_actions, "_pick_directory", _directory_picker_result(replacement))
    original_remove = plugin_actions.remove_library_root

    def fail_old_root(path: Path) -> list[Path]:
        resolved = Path(path).expanduser().resolve()
        if resolved == first.resolve():
            raise OSError("simulated old-root unregister failure")
        return original_remove(path)

    monkeypatch.setattr(plugin_actions, "remove_library_root", fail_old_root)
    errors: list[_RecordedMessage] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        _record_message(errors),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def change(_dialog: Toplevel) -> None:
        _select_path(context, "manage-libraries.rows", first, path_column="path")
        _click(context, "manage-libraries.action.change", "Change folder")
        assert len(errors) == 1
        assert errors[0][0] == "Change registered folder"
        assert errors[0][1] == "simulated old-root unregister failure"
        assert _parent_title(errors[0][2]) == "Manage Libraries"
        assert first.resolve() in repository.library_roots()
        assert second.resolve() in repository.library_roots()
        assert replacement.resolve() not in repository.library_roots()
        assert repository.default_library_root() == first.resolve()
        _click(
            context, "manage-libraries.action.close", "Close", closes=True, title="Manage Libraries"
        )

    try:
        _run_library_manager(root, context, change)
    finally:
        root.destroy()


def test_manage_libraries_unregister_error_keeps_root_registered(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify unregister failure keeps the root registered."""
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        _record_question([], True),
    )
    monkeypatch.setattr(
        plugin_actions,
        "remove_library_root",
        _raise_path_error("settings file is read-only"),
    )
    errors: list[_RecordedMessage] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        _record_message(errors),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def unregister(_dialog: Toplevel) -> None:
        _select_path(context, "manage-libraries.rows", second, path_column="path")
        _click(context, "manage-libraries.action.unregister", "Unregister")
        assert len(errors) == 1
        assert errors[0][0] == "Unregister library"
        assert errors[0][1] == "settings file is read-only"
        assert _parent_title(errors[0][2]) == "Manage Libraries"
        assert second.resolve() in repository.library_roots()
        assert first.resolve() in repository.library_roots()
        _click(
            context, "manage-libraries.action.close", "Close", closes=True, title="Manage Libraries"
        )

    try:
        _run_library_manager(root, context, unregister)
    finally:
        root.destroy()


def test_manage_libraries_set_default_error_preserves_previous_default(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify default-setting failure preserves the prior root."""
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        plugin_actions,
        "set_library_root",
        _raise_path_error("could not write default setting"),
    )
    errors: list[_RecordedMessage] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        _record_message(errors),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def set_default(_dialog: Toplevel) -> None:
        _select_path(context, "manage-libraries.rows", second, path_column="path")
        _click(context, "manage-libraries.action.default", "Set default")
        assert len(errors) == 1
        assert errors[0][0] == "Set default library"
        assert errors[0][1] == "could not write default setting"
        assert _parent_title(errors[0][2]) == "Manage Libraries"
        assert repository.default_library_root() == first.resolve()
        _click(
            context, "manage-libraries.action.close", "Close", closes=True, title="Manage Libraries"
        )

    try:
        _run_library_manager(root, context, set_default)
    finally:
        root.destroy()
