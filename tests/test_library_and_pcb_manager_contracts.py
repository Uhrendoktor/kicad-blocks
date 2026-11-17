"""Real Tk interaction contracts for library and PCB layout managers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks import repository
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import create_block, read_manifest
from ui_contracts import ButtonContract, UiTestContext, assert_control_coverage

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
    "pcb-layout-manager.action.save-close",
}


def _new_root(monkeypatch):
    plugin_actions._load_tkinter()
    monkeypatch.setattr(plugin_actions, "_create_action_icon", lambda *_args: None)
    root = plugin_actions.tk.Tk()
    root.title("UI contract host")
    root.geometry("1280x800")
    root.update()
    return root


def _find_dialog(root, title: str):
    return next(
        child
        for child in root.winfo_children()
        if child.winfo_class() == "Toplevel" and child.title() == title
    )


def _run_modal(root, context, title, expected_ids, action, invoke):
    callback_errors = []

    def drive():
        dialog = None
        try:
            dialog = _find_dialog(root, title)
            assert dialog.winfo_viewable()
            context.refresh_widgets()
            assert_control_coverage(dialog, expected_ids)
            action(dialog)
        except Exception as exc:
            callback_errors.append(exc)
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()

    root.after(100, drive)
    result = invoke()
    if callback_errors:
        raise callback_errors[0]
    return result


def _click(context, contract_id: str, label: str, *, closes=False, title=None):
    ButtonContract(
        contract_id=contract_id,
        expected_action=None,
        destroys_window=False,
        expected_label=label,
        expected_closed_window_title=title if closes else None,
    ).run(context)


def _select_path(context, tree_id: str, path: Path, *, path_column: str):
    tree = context.require_widget(tree_id)
    wanted = str(path.expanduser().resolve())
    row_id = next(
        iid
        for iid in tree.get_children()
        if str(tree.set(iid, path_column)) == wanted
    )
    tree.selection_set(row_id)
    tree.focus(row_id)
    tree.event_generate("<<TreeviewSelect>>")
    context.root.update_idletasks()
    context.root.update()
    return row_id


def _library_fixture(monkeypatch, tmp_path: Path):
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


def _run_library_manager(root, context, action):
    return _run_modal(
        root,
        context,
        "Manage Libraries",
        LIBRARY_CONTROLS,
        action,
        lambda: plugin_actions._show_library_roots_dialog(root),
    )


def test_manage_libraries_add_existing_registers_and_refreshes_the_real_tree(
    monkeypatch, tmp_path
):
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    new_library = tmp_path / "third-library"
    new_library.mkdir()
    chooser_calls = []
    monkeypatch.setattr(
        plugin_actions.filedialog,
        "askdirectory",
        lambda **kwargs: (chooser_calls.append(kwargs) or str(new_library)),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add(_dialog):
        _click(context, "manage-libraries.action.add", "Add library")
        assert chooser_calls[0]["title"] == "Add existing block library folder"
        assert chooser_calls[0]["parent"].title() == "Manage Libraries"
        assert new_library.resolve() in repository.library_roots()
        _select_path(context, "manage-libraries.rows", new_library, path_column="path")
        status = context.require_widget("manage-libraries.status")
        variable = str(status.cget("textvariable"))
        assert "registered libraries" in status.getvar(variable)
        _click(
            context, "manage-libraries.action.close", "Close",
            closes=True, title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, add)
        assert first in repository.library_roots()
        assert second in repository.library_roots()
        assert new_library in repository.library_roots()
    finally:
        root.destroy()


def test_manage_libraries_create_makes_and_registers_new_folder(monkeypatch, tmp_path):
    _settings, _first, _second = _library_fixture(monkeypatch, tmp_path)
    destination = tmp_path / "created-libraries"
    destination.mkdir()
    monkeypatch.setattr(
        plugin_actions.filedialog, "askdirectory", lambda **_kwargs: str(destination)
    )
    ask_calls = []
    monkeypatch.setattr(
        plugin_actions.simpledialog,
        "askstring",
        lambda title, prompt, **kwargs: (
            ask_calls.append((title, prompt, kwargs.get("parent"))) or "new-library"
        ),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)
    created = destination / "new-library"

    def create(_dialog):
        _click(context, "manage-libraries.action.create", "Create library")
        assert ask_calls[0][0] == "Create block library"
        assert ask_calls[0][2].title() == "Manage Libraries"
        assert created.is_dir()
        assert created.resolve() in repository.library_roots()
        _click(
            context, "manage-libraries.action.close", "Close",
            closes=True, title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, create)
    finally:
        root.destroy()


def test_manage_libraries_unregister_removes_registration_but_preserves_files(
    monkeypatch, tmp_path
):
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", lambda *_a, **_k: True)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def unregister(_dialog):
        _select_path(context, "manage-libraries.rows", second, path_column="path")
        button = context.require_widget("manage-libraries.action.unregister")
        assert button.instate(["!disabled"])
        _click(context, "manage-libraries.action.unregister", "Unregister")
        assert second.resolve() not in repository.library_roots()
        assert (second / ".gitignore").is_file()
        assert (first / "keep-me.txt").read_text(encoding="utf-8") == "preserve library data"
        _click(
            context, "manage-libraries.action.close", "Close",
            closes=True, title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, unregister)
    finally:
        root.destroy()


def test_manage_libraries_confirmation_cancel_keeps_registration(monkeypatch, tmp_path):
    _settings, _first, second = _library_fixture(monkeypatch, tmp_path)
    prompts = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        lambda title, message, **_kwargs: (prompts.append((title, message)) or False),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def cancel(_dialog):
        _select_path(context, "manage-libraries.rows", second, path_column="path")
        _click(context, "manage-libraries.action.unregister", "Unregister")
        assert prompts and prompts[0][0] == "Unregister library"
        assert second.resolve() in repository.library_roots()
        _click(
            context, "manage-libraries.action.close", "Close",
            closes=True, title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, cancel)
    finally:
        root.destroy()


def test_manage_libraries_set_default_persists_selected_library(monkeypatch, tmp_path):
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def set_default(_dialog):
        _select_path(context, "manage-libraries.rows", second, path_column="path")
        button = context.require_widget("manage-libraries.action.default")
        assert button.instate(["!disabled"])
        _click(context, "manage-libraries.action.default", "Set default")
        assert repository.default_library_root() == second.resolve()
        tree = context.require_widget("manage-libraries.rows")
        selected = tree.selection()[0]
        assert tree.set(selected, "role") == "Default"
        _click(
            context, "manage-libraries.action.close", "Close",
            closes=True, title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, set_default)
        assert repository.default_library_root() == second.resolve()
        assert first.resolve() in repository.library_roots()
    finally:
        root.destroy()


def test_manage_libraries_change_folder_is_transactional_and_keeps_old_data(
    monkeypatch, tmp_path
):
    _settings, first, second = _library_fixture(monkeypatch, tmp_path)
    replacement = tmp_path / "replacement-library"
    replacement.mkdir()
    monkeypatch.setattr(
        plugin_actions.filedialog,
        "askdirectory",
        lambda **kwargs: str(replacement),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def change(_dialog):
        _select_path(context, "manage-libraries.rows", first, path_column="path")
        _click(context, "manage-libraries.action.change", "Change folder")
        roots = repository.library_roots()
        assert first.resolve() not in roots
        assert replacement.resolve() in roots
        assert repository.default_library_root() == replacement.resolve()
        assert (first / "keep-me.txt").read_text(encoding="utf-8") == "preserve library data"
        assert second.resolve() in roots
        _click(
            context, "manage-libraries.action.close", "Close",
            closes=True, title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, change)
    finally:
        root.destroy()


def test_manage_libraries_environment_override_and_empty_selection_states(
    monkeypatch, tmp_path
):
    _settings, first, _second = _library_fixture(monkeypatch, tmp_path)
    monkeypatch.setenv("KICAD_BLOCKS_LIBRARY", str(first))
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def inspect(_dialog):
        for button_id in (
            "manage-libraries.action.change",
            "manage-libraries.action.unregister",
            "manage-libraries.action.default",
        ):
            assert context.require_widget(button_id).instate(["disabled"])
        _click(
            context, "manage-libraries.action.close", "Close",
            closes=True, title="Manage Libraries",
        )

    try:
        _run_library_manager(root, context, inspect)
    finally:
        root.destroy()


def _pcb_fixture(tmp_path: Path, *, layout_count=2, with_variant=True):
    library = tmp_path / "pcb-library"
    library.mkdir()
    source_boards = []
    for index in range(layout_count):
        board = tmp_path / f"board-{index + 1}.kicad_pcb"
        board.write_text(
            '(kicad_pcb (version 20241229) (generator "contract-test"))\n',
            encoding="utf-8",
        )
        source_boards.append(board)
    variants = []
    if with_variant:
        variant_source = tmp_path / "boost.kicad_sch"
        variant_source.write_text(
            '(kicad_sch (version 20250114) (generator "contract-test"))\n',
            encoding="utf-8",
        )
        variants.append({"value": "Boost", "path": variant_source})
    layout_files = [
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


def _run_pcb_manager(root, context, record, action):
    return _run_modal(
        root,
        context,
        f"PCB layouts — {record.name}",
        PCB_CONTROLS,
        action,
        lambda: plugin_actions._show_block_layout_editor(record, root),
    )


def _select_pcb_row(context, row_id: str):
    tree = context.require_widget("pcb-layout-manager.rows")
    tree.selection_set(row_id)
    tree.focus(row_id)
    tree.event_generate("<<TreeviewSelect>>")
    context.root.update_idletasks()
    context.root.update()


def test_pcb_layout_manager_updates_default_and_variant_associations_then_removes(
    monkeypatch, tmp_path
):
    record, _sources = _pcb_fixture(tmp_path, layout_count=2)
    prompts = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        lambda title, message, **_kwargs: (prompts.append((title, message)) or True),
    )
    original_manifest = read_manifest(record.path)
    original_layouts = original_manifest["layouts"]
    first_id = original_layouts[0]["id"]
    second = original_layouts[1]
    second_path = record.path / second["file"]
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def maintain(_dialog):
        _select_pcb_row(context, "1")
        _click(context, "pcb-layout-manager.action.default", "Set default")
        manifest = read_manifest(record.path)
        assert manifest["default_layout"] == second["id"]

        combo = context.require_widget("pcb-layout-manager.implementation")
        combo.set("Boost")
        _click(context, "pcb-layout-manager.action.assign", "Assign")
        manifest = read_manifest(record.path)
        variant = next(item for item in manifest["variants"] if item["value"] == "Boost")
        assert variant["layout_id"] == second["id"]
        selected_layout = next(item for item in manifest["layouts"] if item["id"] == second["id"])
        assert "Boost" in selected_layout["implementations"]

        _click(context, "pcb-layout-manager.action.remove", "Remove")
        manifest = read_manifest(record.path)
        assert len(manifest["layouts"]) == 1
        assert manifest["default_layout"] == first_id
        assert not second_path.exists()
        assert manifest["variants"][0]["layout_id"] == first_id
        assert prompts and prompts[0][0] == "Remove PCB layout"
        _click(
            context, "pcb-layout-manager.action.close", "Close",
            closes=True, title=f"PCB layouts — {record.name}",
        )

    try:
        _run_pcb_manager(root, context, record, maintain)
    finally:
        root.destroy()


def test_pcb_layout_manager_add_and_save_failure_can_be_retried(monkeypatch, tmp_path):
    record, _sources = _pcb_fixture(tmp_path, layout_count=1)
    new_board = tmp_path / "new-boost.kicad_pcb"
    new_board.write_text(
        '(kicad_pcb (version 20241229) (generator "contract-test"))\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plugin_actions,
        "_ask_layout_details",
        lambda *_args, **_kwargs: {
            "name": "Boost layout",
            "source": str(new_board),
            "linked_implementation": "Boost",
        },
    )
    original_save = plugin_actions._save_block_layouts
    attempts = []
    errors = []

    def fail_once(*args, **kwargs):
        attempts.append("save")
        if len(attempts) == 1:
            raise OSError("simulated PCB layout write failure")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(plugin_actions, "_save_block_layouts", fail_once)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def add_and_retry(_dialog):
        _click(context, "pcb-layout-manager.action.add", "Add layout")
        tree = context.require_widget("pcb-layout-manager.rows")
        assert tree.item("1", "values")[2] == "new-boost.kicad_pcb"
        status = context.require_widget("pcb-layout-manager.status")
        variable = str(status.cget("textvariable"))
        assert "Save failed; use Save & close to retry." in status.getvar(variable)
        assert errors and errors[0][0] == "Save PCB layouts"
        assert len(read_manifest(record.path)["layouts"]) == 1
        _click(
            context, "pcb-layout-manager.action.save-close", "Save & close",
            closes=True, title=f"PCB layouts — {record.name}",
        )

    try:
        opened = _run_pcb_manager(root, context, record, add_and_retry)
        manifest = read_manifest(record.path)
        assert len(manifest["layouts"]) == 2
        added = next(item for item in manifest["layouts"] if item["name"] == "Boost layout")
        assert "Boost" in added["implementations"]
        assert added["file"] == opened.name
        assert opened.is_file()
        assert opened.read_bytes() == new_board.read_bytes()
        assert len(attempts) == 2
    finally:
        root.destroy()


def test_pcb_layout_manager_cancelled_remove_preserves_board_and_manifest(
    monkeypatch, tmp_path
):
    record, _sources = _pcb_fixture(tmp_path, layout_count=2)
    before = json.loads(json.dumps(read_manifest(record.path)))
    prompts = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        lambda title, message, **_kwargs: (prompts.append((title, message)) or False),
    )
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def cancel(_dialog):
        _select_pcb_row(context, "1")
        second_file = record.path / before["layouts"][1]["file"]
        _click(context, "pcb-layout-manager.action.remove", "Remove")
        assert second_file.is_file()
        assert read_manifest(record.path)["layouts"] == before["layouts"]
        assert prompts and prompts[0][0] == "Remove PCB layout"
        _click(
            context, "pcb-layout-manager.action.close", "Close",
            closes=True, title=f"PCB layouts — {record.name}",
        )

    try:
        _run_pcb_manager(root, context, record, cancel)
    finally:
        root.destroy()


def test_pcb_layout_manager_disables_selection_actions_when_empty(
    monkeypatch, tmp_path
):
    record, _sources = _pcb_fixture(tmp_path, layout_count=0, with_variant=False)
    root = _new_root(monkeypatch)
    context = UiTestContext(root)

    def inspect(_dialog):
        assert context.require_widget("pcb-layout-manager.action.remove").instate(["disabled"])
        assert context.require_widget("pcb-layout-manager.action.default").instate(["disabled"])
        assert context.require_widget("pcb-layout-manager.action.assign").instate(["disabled"])
        assert context.require_widget("pcb-layout-manager.action.add").instate(["!disabled"])
        _click(
            context, "pcb-layout-manager.action.close", "Close",
            closes=True, title=f"PCB layouts — {record.name}",
        )

    try:
        _run_pcb_manager(root, context, record, inspect)
    finally:
        root.destroy()
