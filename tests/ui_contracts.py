"""Reusable execute -> observe -> assert contracts for real Tk widgets.

These helpers deliberately exercise Tcl/Tk widgets rather than replacing the
application with mocks. External KiCad IPC and filesystem boundaries should be
isolated in fixtures, while the widget command and resulting application state
remain real.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar, TypedDict, cast

if TYPE_CHECKING:
    import tkinter as tk
    from tkinter import Menu, Toplevel
    from tkinter.ttk import Button, Checkbutton, Combobox, Entry, Notebook, Panedwindow, Text, Treeview
else:
    Menu = Toplevel = Button = Checkbutton = Combobox = Entry = Notebook = Panedwindow = Text = Treeview = object


class MenuEntry:
    """A discoverable Tk menu command and its numeric Tcl/Tk index."""

    def __init__(self, menu: tk.Menu, index: int) -> None:
        self.menu = menu
        self.index = index


def _new_string_list() -> list[str]:
    return []


def _new_widget_map() -> dict[str, tk.Misc]:
    return {}


def _new_menu_entry_map() -> dict[str, MenuEntry]:
    return {}


class WindowObservation(TypedDict):
    title: str
    screen_id: str | None
    exists: bool
    visible: bool
    width: int
    height: int
    widget_ids: set[str]


class ButtonObservation(TypedDict):
    invoke_result: str | int | None
    label: str
    action_calls: list[str]
    root_exists: bool
    new_windows: list[str]
    closed_windows: list[str]


class MenuItemObservation(TypedDict):
    invoke_result: str | int | None
    events: list[str]


class EntryInputObservation(TypedDict):
    value: str
    result_count: int
    summary: str


class TreeSelectionObservation(TypedDict):
    selection: tuple[str, ...]
    summary: str
    values: tuple[str, ...]


class ChoiceViewObservation(TypedDict):
    value: str
    events: list[str]


class NotebookPageObservation(TypedDict):
    selected: str
    mapped: dict[str, bool]


class PaneObservation(TypedDict):
    present: bool
    width: int
    height: int


class PaneGroupObservation(TypedDict):
    orientation: str
    panes: dict[str, PaneObservation]


type TkOption = (
    str
    | int
    | float
    | bool
    | bytes
    | None
    | Sequence[str | int | float | bool | bytes | None]
    | Sequence[tuple[str, str]]
    | Callable[..., str | None]
)


ObservationT = TypeVar("ObservationT")


@dataclass
class UiTestContext:
    """Shared state available to component contracts during one test."""

    root: tk.Misc
    action_calls: list[str] = field(default_factory=_new_string_list)
    events: list[str] = field(default_factory=_new_string_list)
    widgets: dict[str, tk.Misc] = field(default_factory=_new_widget_map)
    menu_entries: dict[str, MenuEntry] = field(default_factory=_new_menu_entry_map)
    window_destroyed: bool = False

    def __post_init__(self) -> None:
        self.root.bind("<Destroy>", self._observe_root_destroy, add="+")

    def _observe_root_destroy(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is self.root:
            self.window_destroyed = True

    def refresh_widgets(self) -> dict[str, tk.Misc]:
        controls = discover_interactive_controls(self.root)
        self.widgets = {
            contract_id: widget
            for contract_id, widget in controls.items()
            if not isinstance(widget, MenuEntry)
        }
        self.menu_entries = {
            contract_id: widget
            for contract_id, widget in controls.items()
            if isinstance(widget, MenuEntry)
        }
        return self.widgets

    def require_widget(self, contract_id: str) -> tk.Misc:
        if not self.widgets and not self.menu_entries:
            self.refresh_widgets()
        try:
            return self.widgets[contract_id]
        except KeyError as exc:
            raise AssertionError(f"No Tk widget is registered as {contract_id!r}.") from exc

    def require_menu_entry(self, contract_id: str) -> MenuEntry:
        if not self.widgets and not self.menu_entries:
            self.refresh_widgets()
        try:
            return self.menu_entries[contract_id]
        except KeyError as exc:
            raise AssertionError(f"No Tk menu entry is registered as {contract_id!r}.") from exc

    def require_treeview(self, contract_id: str) -> Treeview:
        """Return one registered ttk Treeview with its row/selection API."""
        return cast(Treeview, self.require_widget(contract_id))

    def require_combobox(self, contract_id: str) -> Combobox:
        """Return one registered ttk Combobox with its choice API."""
        return cast(Combobox, self.require_widget(contract_id))

    def require_entry(self, contract_id: str) -> Entry:
        """Return one registered ttk Entry with its text-input API."""
        return cast(Entry, self.require_widget(contract_id))

    def require_button(self, contract_id: str) -> Button:
        """Return one registered ttk Button with its invocation/state API."""
        return cast(Button, self.require_widget(contract_id))

    def require_checkbutton(self, contract_id: str) -> Checkbutton:
        """Return one registered ttk Checkbutton with its state API."""
        return cast(Checkbutton, self.require_widget(contract_id))

    def require_canvas(self, contract_id: str) -> tk.Canvas:
        """Return one registered Tk Canvas with its drawing and scrolling API."""
        return cast(tk.Canvas, self.require_widget(contract_id))

    def require_text(self, contract_id: str) -> Text:
        """Return one registered Tk Text widget with its text-buffer API."""
        return cast(Text, self.require_widget(contract_id))


    def require_notebook(self, contract_id: str) -> Notebook:
        """Return one registered ttk Notebook with its page-selection API."""
        return cast(Notebook, self.require_widget(contract_id))

    def require_panedwindow(self, contract_id: str) -> Panedwindow:
        """Return one registered ttk Panedwindow with its child-pane API."""
        return cast(Panedwindow, self.require_widget(contract_id))


class ComponentContract(ABC, Generic[ObservationT]):
    """Common lifecycle implemented by every testable UI component."""

    @abstractmethod
    def execute(self, context: UiTestContext) -> ObservationT:
        """Perform a real interaction and return its observable result."""

    @abstractmethod
    def assert_result(self, context: UiTestContext, observation: ObservationT) -> None:
        """Assert the externally observable result of that interaction."""

    def run(self, context: UiTestContext) -> ObservationT:
        observation = self.execute(context)
        self.assert_result(context, observation)
        return observation


def _open_toplevels(root: tk.Misc) -> dict[int, str]:
    windows: dict[int, str] = {}
    for child in root.winfo_children():
        if child.winfo_class() == "Toplevel":
            window = cast(Toplevel, child)
            windows[window.winfo_id()] = window.title()
    return windows


@dataclass(frozen=True)
class WindowContract(ComponentContract[WindowObservation]):
    """Assert that the expected application window was created and shown."""

    title: str
    screen_id: str
    required_widget_ids: tuple[str, ...] = ()

    def execute(self, context: UiTestContext) -> WindowObservation:
        context.root.update_idletasks()
        context.root.update()
        widgets = context.refresh_widgets()
        return {
            "title": context.root.title(),
            "screen_id": cast(str | None, getattr(context.root, "_kicad_ui_screen_id", None)),
            "exists": bool(context.root.winfo_exists()),
            "visible": bool(context.root.winfo_viewable()),
            "width": context.root.winfo_width(),
            "height": context.root.winfo_height(),
            "widget_ids": set(widgets) | set(context.menu_entries),
        }

    def assert_result(self, context: UiTestContext, observation: WindowObservation) -> None:
        assert observation["title"] == self.title
        assert observation["screen_id"] == self.screen_id
        assert observation["exists"] is True
        assert observation["visible"] is True
        assert observation["width"] > 0
        assert observation["height"] > 0
        assert set(self.required_widget_ids) <= observation["widget_ids"]


@dataclass(frozen=True)
class ButtonContract(ComponentContract[ButtonObservation]):
    """Invoke one real Tk button and assert its route and lifecycle effect."""

    contract_id: str
    expected_action: str | tuple[str, ...] | None
    destroys_window: bool = True
    expected_new_window_title: str | None = None
    expected_closed_window_title: str | None = None
    expected_label: str | None = None

    def execute(self, context: UiTestContext) -> ButtonObservation:
        button = cast(Button, context.require_widget(self.contract_id))
        label = str(button.cget("text"))
        existing_toplevels = _open_toplevels(context.root)
        result = button.invoke()
        root_exists = not context.window_destroyed
        if root_exists:
            context.root.update_idletasks()
            context.root.update()
            root_exists = not context.window_destroyed
        remaining_toplevels = _open_toplevels(context.root) if root_exists else {}
        new_windows = [
            title
            for window_id, title in remaining_toplevels.items()
            if window_id not in existing_toplevels
        ]
        closed_windows = [
            title
            for window_id, title in existing_toplevels.items()
            if window_id not in remaining_toplevels
        ]
        return {
            "invoke_result": result,
            "label": label,
            "action_calls": list(context.action_calls),
            "root_exists": root_exists,
            "new_windows": new_windows,
            "closed_windows": closed_windows,
        }

    def assert_result(self, context: UiTestContext, observation: ButtonObservation) -> None:
        if self.expected_action is None:
            expected: list[str] = []
        elif isinstance(self.expected_action, tuple):
            expected = list(self.expected_action)
        else:
            expected = [self.expected_action]
        assert observation["action_calls"] == expected
        assert observation["root_exists"] is (not self.destroys_window)
        if self.expected_label is not None:
            assert observation["label"] == self.expected_label
        if self.expected_new_window_title is not None:
            assert self.expected_new_window_title in observation["new_windows"]
        if self.expected_closed_window_title is not None:
            assert self.expected_closed_window_title in observation["closed_windows"]


@dataclass(frozen=True)
class MenuItemContract(ComponentContract[MenuItemObservation]):
    """Invoke a real Tk menu entry and assert the bound action ran."""

    menu_item_id: str
    expected_event: str

    def execute(self, context: UiTestContext) -> MenuItemObservation:
        entry = context.require_menu_entry(self.menu_item_id)
        return {
            "invoke_result": entry.menu.invoke(entry.index),
            "events": list(context.events),
        }

    def assert_result(self, context: UiTestContext, observation: MenuItemObservation) -> None:
        assert observation["events"] == [self.expected_event]


@dataclass(frozen=True)
class EntryInputContract(ComponentContract[EntryInputObservation]):
    """Enter text into a real Tk Entry and assert its dependent result list."""

    contract_id: str
    value: str
    results_contract_id: str
    expected_result_count: int
    summary_contract_id: str
    expected_summary: str

    def execute(self, context: UiTestContext) -> EntryInputObservation:
        entry = cast(Entry, context.require_widget(self.contract_id))
        entry.delete(0, "end")
        entry.insert(0, self.value)
        context.root.update_idletasks()
        context.root.update()
        results = cast(Treeview, context.require_widget(self.results_contract_id))
        summary = context.require_widget(self.summary_contract_id)
        summary_text = str(summary.cget("text"))
        text_variable = str(summary.cget("textvariable") or "")
        if text_variable:
            summary_text = str(summary.getvar(text_variable))
        return {
            "value": entry.get(),
            "result_count": len(results.get_children()),
            "summary": summary_text,
        }

    def assert_result(self, context: UiTestContext, observation: EntryInputObservation) -> None:
        assert observation["value"] == self.value
        assert observation["result_count"] == self.expected_result_count
        assert observation["summary"] == self.expected_summary


@dataclass(frozen=True)
class TreeSelectionContract(ComponentContract[TreeSelectionObservation]):
    """Select a real Treeview row and assert selection plus dependent details."""

    contract_id: str
    row_id: str
    summary_contract_id: str
    expected_summary: str
    expected_values: tuple[str, ...] | None = None

    def execute(self, context: UiTestContext) -> TreeSelectionObservation:
        tree = cast(Treeview, context.require_widget(self.contract_id))
        tree.selection_set(self.row_id)
        tree.focus(self.row_id)
        tree.event_generate("<<TreeviewSelect>>")
        context.root.update_idletasks()
        context.root.update()
        summary = context.require_widget(self.summary_contract_id)
        summary_text = str(summary.cget("text"))
        text_variable = str(summary.cget("textvariable") or "")
        if text_variable:
            summary_text = str(summary.getvar(text_variable))
        row_values = tuple(tree.item(self.row_id, "values"))
        return {
            "selection": tuple(tree.selection()),
            "summary": summary_text,
            "values": row_values,
        }

    def assert_result(self, context: UiTestContext, observation: TreeSelectionObservation) -> None:
        assert observation["selection"] == (self.row_id,)
        assert observation["summary"] == self.expected_summary
        if self.expected_values is not None:
            assert observation["values"] == self.expected_values


@dataclass(frozen=True)
class ChoiceViewContract(ComponentContract[ChoiceViewObservation]):
    """Select every declared value in a real ttk Combobox."""

    contract_id: str
    value: str
    expected_event: str

    def execute(self, context: UiTestContext) -> ChoiceViewObservation:
        choice = cast(Combobox, context.require_widget(self.contract_id))
        choice.set(self.value)
        choice.event_generate("<<ComboboxSelected>>")
        context.root.update_idletasks()
        context.root.update()
        return {
            "value": choice.get(),
            "events": list(context.events),
        }

    def assert_result(self, context: UiTestContext, observation: ChoiceViewObservation) -> None:
        assert observation["value"] == self.value
        assert observation["events"] == [self.expected_event]


@dataclass(frozen=True)
class NotebookViewContract(ComponentContract[list[NotebookPageObservation]]):
    """Visit every Notebook page and assert the expected controls are visible."""

    contract_id: str
    views: tuple[tuple[str, tuple[str, ...]], ...]

    def execute(self, context: UiTestContext) -> list[NotebookPageObservation]:
        notebook = cast(Notebook, context.require_widget(self.contract_id))
        observations: list[NotebookPageObservation] = []
        for label, control_ids in self.views:
            tab_id = next(
                (tab for tab in notebook.tabs() if notebook.tab(tab, "text") == label),
                None,
            )
            if tab_id is None:
                raise AssertionError(f"Notebook view {label!r} does not exist.")
            notebook.select(tab_id)
            context.root.update_idletasks()
            context.root.update()
            mapped = {
                control_id: bool(context.require_widget(control_id).winfo_ismapped())
                for control_id in control_ids
            }
            observations.append(
                {
                    "selected": str(notebook.tab(notebook.select(), "text")),
                    "mapped": mapped,
                }
            )
        return observations

    def assert_result(
        self, context: UiTestContext, observation: list[NotebookPageObservation]
    ) -> None:
        assert len(observation) == len(self.views)
        for result, (label, control_ids) in zip(observation, self.views, strict=True):
            assert result["selected"] == label
            assert set(result["mapped"]) == set(control_ids)
            assert all(result["mapped"].values())


@dataclass(frozen=True)
class PaneGroupContract(ComponentContract[PaneGroupObservation]):
    """Assert a real paned window contains visible, non-zero child panes."""

    contract_id: str
    orientation: str
    pane_ids: tuple[str, ...]

    def execute(self, context: UiTestContext) -> PaneGroupObservation:
        group = cast(Panedwindow, context.require_widget(self.contract_id))
        context.root.update_idletasks()
        context.root.update()
        panes: dict[str, tk.Misc] = {}
        for pane in group.winfo_children():
            contract_id = cast(str | None, getattr(pane, "_kicad_ui_contract_id", None))
            if contract_id is not None:
                panes[contract_id] = pane
        return {
            "orientation": str(group.cget("orient")),
            "panes": {
                pane_id: {
                    "present": pane_id in panes,
                    "width": panes[pane_id].winfo_width() if pane_id in panes else 0,
                    "height": panes[pane_id].winfo_height() if pane_id in panes else 0,
                }
                for pane_id in self.pane_ids
            },
        }

    def assert_result(self, context: UiTestContext, observation: PaneGroupObservation) -> None:
        assert observation["orientation"] == self.orientation
        assert set(observation["panes"]) == set(self.pane_ids)
        for pane in observation["panes"].values():
            assert pane["present"] is True
            assert pane["width"] > 0
            assert pane["height"] > 0


INTERACTIVE_WIDGET_CLASSES = frozenset(
    {
        "Button",
        "TButton",
        "Checkbutton",
        "TCheckbutton",
        "Radiobutton",
        "TRadiobutton",
        "Menubutton",
        "TMenubutton",
        "Combobox",
        "TCombobox",
        "Spinbox",
        "TSpinbox",
        "Scale",
        "TScale",
        "Entry",
        "TEntry",
        "Treeview",
        "TTreeview",
        "Notebook",
        "TNotebook",
        "Panedwindow",
        "TPanedwindow",
    }
)


def _walk_widgets(root: tk.Misc) -> Iterator[tk.Misc]:
    yield root
    for child in root.winfo_children():
        yield child
        yield from _walk_widget_children(child)


def _walk_widget_children(widget: tk.Misc) -> Iterator[tk.Misc]:
    for child in widget.winfo_children():
        yield child
        yield from _walk_widget_children(child)


def discover_interactive_controls(root: tk.Misc) -> dict[str, tk.Misc | MenuEntry]:
    """Return all tagged action/view controls, including registered menu items.

    The discovery list is intentionally limited to controls with an interaction
    contract. Static labels and decorative frames are not actionable controls.
    """
    discovered: dict[str, tk.Misc | MenuEntry] = {}
    for widget in _walk_widgets(root):
        widget_class = widget.winfo_class()
        contract_id = cast(str | None, getattr(widget, "_kicad_ui_contract_id", None))
        if contract_id:
            if contract_id in discovered:
                raise AssertionError(f"Duplicate UI contract ID: {contract_id}")
            discovered[contract_id] = widget

        if widget_class == "Menu":
            menu = cast(Menu, widget)
            menu_items = cast(dict[str, int], getattr(menu, "_kicad_ui_menu_item_ids", {}))
            for item_id, index in menu_items.items():
                if item_id in discovered:
                    raise AssertionError(f"Duplicate UI contract ID: {item_id}")
                discovered[item_id] = MenuEntry(menu, int(index))
    return discovered


def assert_control_coverage(
    root: tk.Misc, expected_ids: set[str]
) -> dict[str, tk.Misc | MenuEntry]:
    """Fail when an interactive widget is untagged or outside the declared map."""
    missing_tags: list[str] = []
    for widget in _walk_widgets(root):
        widget_class = widget.winfo_class()
        contract_id = cast(str | None, getattr(widget, "_kicad_ui_contract_id", None))
        if widget_class in INTERACTIVE_WIDGET_CLASSES:
            if not contract_id:
                missing_tags.append(widget_class)
        elif widget_class == "Menu":
            menu = cast(Menu, widget)
            menu_items = cast(dict[str, int], getattr(menu, "_kicad_ui_menu_item_ids", {}))
            tagged_indexes = {int(index) for index in menu_items.values()}
            last_index = menu.index("end")
            if last_index is None:
                continue
            for index in range(int(last_index) + 1):
                if menu.type(index) in {"separator", "tearoff"}:
                    continue
                if index not in tagged_indexes:
                    missing_tags.append(f"Menu entry {index}")

    discovered = discover_interactive_controls(root)
    actual_ids = set(discovered)
    missing = sorted(expected_ids - actual_ids)
    unexpected = sorted(actual_ids - expected_ids)
    if missing_tags or missing or unexpected:
        raise AssertionError(
            "UI contract coverage mismatch: "
            f"untagged={missing_tags!r}; missing={missing!r}; "
            f"unexpected={unexpected!r}"
        )
    return discovered
