"""Execute and assert the production Task Hub through real Tk widgets."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypeVar, cast

from _pytest.monkeypatch import MonkeyPatch

if TYPE_CHECKING:
    from tkinter import Event as TkEvent
    from tkinter import Misc, Tk
    from tkinter.ttk import Button, Notebook
else:
    TkEvent = Misc = Tk = Button = Notebook = object

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "plugin" / "kicad-blocks"
TEST_SOURCE = ROOT / "tests"
sys.path.insert(0, str(PLUGIN_SOURCE))
sys.path.insert(0, str(TEST_SOURCE))

from kicad_blocks import plugin as plugin_actions
from ui_contracts import (
    ButtonContract,
    ChoiceViewContract,
    MenuItemContract,
    NotebookViewContract,
    PaneGroupContract,
    UiTestContext,
    WindowContract,
    assert_control_coverage,
)

CATALOG_PATH = ROOT / "tests" / "ui_contract_catalog.json"
TASK_HUB_SCREEN = next(
    screen
    for screen in json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["screens"]
    if screen["id"] == "task-hub"
)
TASK_HUB_ACTIONS = tuple(
    (action["action_id"], action["label"], view["label"])
    for view in TASK_HUB_SCREEN["views"]
    for action in view["actions"]
)
TASK_HUB_VIEWS = tuple(
    (
        view["label"],
        tuple(action["contract_id"] for action in view["actions"]),
    )
    for view in TASK_HUB_SCREEN["views"]
)
TASK_HUB_CONTROL_IDS = {control["contract_id"] for control in TASK_HUB_SCREEN["controls"]}
TASK_HUB_CONTROL_IDS.update(
    action["contract_id"] for view in TASK_HUB_SCREEN["views"] for action in view["actions"]
)
TASK_HUB_CONTROL_IDS.update(
    control["contract_id"] for control in TASK_HUB_SCREEN.get("footer_controls", [])
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="Real Tk interaction contracts require an X display (run under xvfb-run).",
)


_HookResult = TypeVar("_HookResult")


def _private_hook(name: str) -> Callable[..., _HookResult]:
    """Resolve a private plugin hook through a typed test boundary."""
    return cast(Callable[..., _HookResult], getattr(plugin_actions, name))


_load_tkinter = cast(Callable[[], None], _private_hook("_load_tkinter"))
_show_action_hub = cast(Callable[..., Notebook], _private_hook("_show_action_hub"))


def _new_hub(monkeypatch: MonkeyPatch) -> tuple[Tk, Notebook]:
    _load_tkinter()

    def no_icon(_parent: Misc, _name: str, _stroke_color: str | None = None) -> None:
        return None

    monkeypatch.setattr(plugin_actions, "_create_action_icon", no_icon)
    root = cast(Tk, plugin_actions.tk.Tk())
    notebook = _show_action_hub(root)
    root.update_idletasks()
    root.update()
    return root, notebook


def _button_contract_id(action_id: str) -> str:
    return f"task-hub.action.{action_id}"


def test_task_hub_window_is_registered_and_visible(monkeypatch: MonkeyPatch) -> None:
    root, _notebook = _new_hub(monkeypatch)
    try:
        WindowContract(
            title="KiCad Blocks — Tasks",
            screen_id="task-hub",
            required_widget_ids=tuple(sorted(TASK_HUB_CONTROL_IDS)),
        ).run(UiTestContext(root))
    finally:
        root.destroy()


def test_task_hub_declares_every_action_and_view_from_the_catalog():
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    screen = next(item for item in catalog["screens"] if item["id"] == "task-hub")

    catalog_actions = [
        (action["action_id"], action["label"], view["label"])
        for view in screen["views"]
        for action in view["actions"]
    ]
    assert len(TASK_HUB_ACTIONS) == 13
    assert len({action[0] for action in TASK_HUB_ACTIONS}) == 13
    assert catalog_actions == list(TASK_HUB_ACTIONS)
    assert [view["label"] for view in screen["views"]] == [view[0] for view in TASK_HUB_VIEWS]
    assert all(
        action["assertion"] == "asserted"
        and action["contract_id"] == _button_contract_id(action["action_id"])
        for view in screen["views"]
        for action in view["actions"]
    )


def test_task_hub_widget_inventory_has_no_untagged_interactive_controls(
    monkeypatch: MonkeyPatch,
) -> None:
    root, _notebook = _new_hub(monkeypatch)
    try:
        discovered = assert_control_coverage(root, TASK_HUB_CONTROL_IDS)
        assert set(discovered) == TASK_HUB_CONTROL_IDS
    finally:
        root.destroy()


def test_task_hub_exposes_all_three_functional_notebook_views(monkeypatch: MonkeyPatch) -> None:
    root, _notebook = _new_hub(monkeypatch)
    try:
        contract = NotebookViewContract(
            contract_id="task-hub.views",
            views=TASK_HUB_VIEWS,
        )
        observations = contract.run(UiTestContext(root))
        assert [item["selected"] for item in observations] == [
            "Use & build",
            "Edit & maintain",
            "Checks & PCB",
        ]
    finally:
        root.destroy()


@pytest.mark.parametrize(
    "action_id,expected_label,_view",
    TASK_HUB_ACTIONS,
    ids=[action_id for action_id, _, _ in TASK_HUB_ACTIONS],
)
def test_each_task_hub_button_dispatches_exactly_its_action_id(
    monkeypatch: MonkeyPatch, action_id: str, expected_label: str, _view: str
) -> None:
    root, _notebook = _new_hub(monkeypatch)
    context = UiTestContext(root)

    def record_action(selected: str, *, editor_window_id: str | None = None) -> None:
        del editor_window_id
        context.action_calls.append(selected)

    monkeypatch.setattr(plugin_actions, "run_action", record_action)
    try:
        ButtonContract(
            contract_id=_button_contract_id(action_id),
            expected_action=action_id,
            destroys_window=True,
            expected_label=expected_label,
        ).run(context)
    finally:
        try:
            root.destroy()
        except plugin_actions.tk.TclError:
            pass


def test_task_hub_close_button_destroys_window_without_dispatch(monkeypatch: MonkeyPatch) -> None:
    root, _notebook = _new_hub(monkeypatch)
    context = UiTestContext(root)

    def record_action(selected: str, *, editor_window_id: str | None = None) -> None:
        del editor_window_id
        context.action_calls.append(selected)

    monkeypatch.setattr(plugin_actions, "run_action", record_action)
    try:
        ButtonContract(
            contract_id="task-hub.close",
            expected_action=None,
            destroys_window=True,
        ).run(context)
        assert context.action_calls == []
    finally:
        try:
            root.destroy()
        except plugin_actions.tk.TclError:
            pass


def test_task_hub_actions_use_a_compact_two_column_grid(monkeypatch: MonkeyPatch) -> None:
    root, notebook = _new_hub(monkeypatch)
    try:
        assert root.winfo_width() >= 700
        assert root.winfo_height() >= 500
        for tab_id in notebook.tabs():
            tab = notebook.nametowidget(tab_id)
            buttons = [
                cast(Button, child)
                for cell in tab.winfo_children()
                for child in cell.winfo_children()
                if child.winfo_class() == "TButton"
                and str(getattr(child, "_kicad_ui_contract_id", "")).startswith("task-hub.action.")
            ]
            assert len(buttons) in {3, 5}
            positions = [cast(Misc, button.master).grid_info() for button in buttons]
            assert positions[0]["column"] == 0
            assert positions[1]["column"] == 1
            assert positions[0]["row"] == positions[1]["row"]
            if len(buttons) % 2:
                assert positions[-1]["columnspan"] == 2
    finally:
        root.destroy()


def test_choice_view_contract_invokes_selected_combobox_view() -> None:
    _load_tkinter()
    tk = plugin_actions.tk
    ttk = plugin_actions.ttk
    root = tk.Tk()
    try:
        root.geometry("480x180")
        choice = ttk.Combobox(root, values=("Facade", "Schematic"), state="readonly")
        choice._kicad_ui_contract_id = "preview.kind"
        choice.pack()
        context = UiTestContext(root)

        def on_preview_selected(_event: TkEvent[Misc]) -> str | None:
            context.events.append(f"preview:{choice.get()}")
            return None

        choice.bind(
            "<<ComboboxSelected>>",
            on_preview_selected,
        )
        root.update()

        ChoiceViewContract(
            contract_id="preview.kind",
            value="Schematic",
            expected_event="preview:Schematic",
        ).run(context)
    finally:
        root.destroy()


def test_menu_item_contract_invokes_real_tk_menu_command() -> None:
    _load_tkinter()
    tk = plugin_actions.tk
    root = tk.Tk()
    try:
        events: list[str] = []
        menu = tk.Menu(root, tearoff=False)

        def refresh() -> None:
            events.append("refresh")

        menu.add_command(label="Refresh", command=refresh)
        menu._kicad_ui_menu_item_ids = {"library.refresh": 0}
        root.configure(menu=menu)
        root.update()

        context = UiTestContext(root)
        context.events = events
        MenuItemContract(
            menu_item_id="library.refresh",
            expected_event="refresh",
        ).run(context)
        assert_control_coverage(root, {"library.refresh"})
    finally:
        root.destroy()


def test_pane_group_contract_asserts_required_visible_panes() -> None:
    _load_tkinter()
    tk = plugin_actions.tk
    ttk = plugin_actions.ttk
    root = tk.Tk()
    try:
        root.geometry("640x360")
        panes = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        panes._kicad_ui_contract_id = "library.split-view"
        panes.pack(fill="both", expand=True)
        for pane_id, label in (
            ("library.results", "Results"),
            ("library.preview", "Preview"),
        ):
            pane = ttk.Frame(panes)
            pane._kicad_ui_contract_id = pane_id
            ttk.Label(pane, text=label).pack(padx=12, pady=12)
            panes.add(pane, weight=1)
        root.update_idletasks()
        root.update()

        PaneGroupContract(
            contract_id="library.split-view",
            orientation="horizontal",
            pane_ids=("library.results", "library.preview"),
        ).run(UiTestContext(root))
    finally:
        root.destroy()


def test_control_coverage_fails_for_untagged_buttons() -> None:
    _load_tkinter()
    tk = plugin_actions.tk
    ttk = plugin_actions.ttk
    root = tk.Tk()
    try:
        ttk.Button(root, text="Missing ID", command=lambda: None).pack()
        root.update()
        with pytest.raises(AssertionError, match="untagged"):
            assert_control_coverage(root, set())
    finally:
        root.destroy()


def test_control_coverage_fails_for_untagged_menu_entries() -> None:
    _load_tkinter()
    tk = plugin_actions.tk
    root = tk.Tk()
    try:
        menu = tk.Menu(root, tearoff=False)
        menu.add_command(label="Unregistered", command=lambda: None)
        root.configure(menu=menu)
        root.update()
        with pytest.raises(AssertionError, match="Menu entry"):
            assert_control_coverage(root, set())
    finally:
        root.destroy()
