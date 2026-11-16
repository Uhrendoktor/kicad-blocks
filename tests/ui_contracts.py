"""Reusable execute -> observe -> assert contracts for real Tk widgets.

These helpers deliberately exercise Tcl/Tk widgets rather than replacing the
application with mocks. External KiCad IPC and filesystem boundaries should be
isolated in fixtures, while the widget command and resulting application state
remain real.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MenuEntry:
    """A discoverable Tk menu command and its numeric Tcl/Tk index."""

    menu: Any
    index: int


@dataclass
class UiTestContext:
    """Shared state available to component contracts during one test."""

    root: Any
    action_calls: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    widgets: dict[str, Any] = field(default_factory=dict)
    window_destroyed: bool = False

    def __post_init__(self) -> None:
        self.root.bind("<Destroy>", self._observe_root_destroy, add="+")

    def _observe_root_destroy(self, event: Any) -> None:
        if event.widget is self.root:
            self.window_destroyed = True

    def refresh_widgets(self) -> dict[str, Any]:
        self.widgets = discover_interactive_controls(self.root)
        return self.widgets

    def require_widget(self, contract_id: str) -> Any:
        if not self.widgets:
            self.refresh_widgets()
        try:
            return self.widgets[contract_id]
        except KeyError as exc:
            raise AssertionError(
                f"No Tk widget is registered as {contract_id!r}."
            ) from exc


class ComponentContract(ABC):
    """Common lifecycle implemented by every testable UI component."""

    @abstractmethod
    def execute(self, context: UiTestContext) -> Any:
        """Perform a real interaction and return its observable result."""

    @abstractmethod
    def assert_result(self, context: UiTestContext, observation: Any) -> None:
        """Assert the externally observable result of that interaction."""

    def run(self, context: UiTestContext) -> Any:
        observation = self.execute(context)
        self.assert_result(context, observation)
        return observation


@dataclass(frozen=True)
class WindowContract(ComponentContract):
    """Assert that the expected application window was created and shown."""

    title: str
    screen_id: str
    required_widget_ids: tuple[str, ...] = ()

    def execute(self, context: UiTestContext) -> dict[str, Any]:
        context.root.update_idletasks()
        context.root.update()
        widgets = context.refresh_widgets()
        return {
            "title": context.root.title(),
            "screen_id": getattr(context.root, "_kicad_ui_screen_id", None),
            "exists": bool(context.root.winfo_exists()),
            "visible": bool(context.root.winfo_viewable()),
            "width": context.root.winfo_width(),
            "height": context.root.winfo_height(),
            "widget_ids": set(widgets),
        }

    def assert_result(
        self, context: UiTestContext, observation: dict[str, Any]
    ) -> None:
        assert observation["title"] == self.title
        assert observation["screen_id"] == self.screen_id
        assert observation["exists"] is True
        assert observation["visible"] is True
        assert observation["width"] > 0
        assert observation["height"] > 0
        assert set(self.required_widget_ids) <= observation["widget_ids"]


@dataclass(frozen=True)
class ButtonContract(ComponentContract):
    """Invoke one real Tk button and assert its route and lifecycle effect."""

    contract_id: str
    expected_action: str | tuple[str, ...] | None
    destroys_window: bool = True
    expected_new_window_title: str | None = None
    expected_closed_window_title: str | None = None
    expected_label: str | None = None

    def execute(self, context: UiTestContext) -> dict[str, Any]:
        button = context.require_widget(self.contract_id)
        label = str(button.cget("text"))
        existing_toplevels = {
            child.winfo_id(): child.title()
            for child in context.root.winfo_children()
            if child.winfo_class() == "Toplevel"
        }
        result = button.invoke()
        root_exists = not context.window_destroyed
        if root_exists:
            context.root.update_idletasks()
            context.root.update()
            root_exists = not context.window_destroyed
        new_windows = []
        remaining_toplevels = {}
        if root_exists:
            remaining_toplevels = {
                child.winfo_id(): child.title()
                for child in context.root.winfo_children()
                if child.winfo_class() == "Toplevel"
            }
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

    def assert_result(
        self, context: UiTestContext, observation: dict[str, Any]
    ) -> None:
        if self.expected_action is None:
            expected = []
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
class MenuItemContract(ComponentContract):
    """Invoke a real Tk menu entry and assert the bound action ran."""

    menu_item_id: str
    expected_event: str

    def execute(self, context: UiTestContext) -> dict[str, Any]:
        entry = context.require_widget(self.menu_item_id)
        if not isinstance(entry, MenuEntry):
            raise TypeError(
                f"{self.menu_item_id!r} is not a discovered Tk menu entry."
            )
        result = entry.menu.invoke(entry.index)
        return {"invoke_result": result, "events": list(context.events)}

    def assert_result(
        self, context: UiTestContext, observation: dict[str, Any]
    ) -> None:
        assert observation["events"] == [self.expected_event]


@dataclass(frozen=True)
class EntryInputContract(ComponentContract):
    """Enter text into a real Tk Entry and assert its dependent result list."""

    contract_id: str
    value: str
    results_contract_id: str
    expected_result_count: int
    summary_contract_id: str
    expected_summary: str

    def execute(self, context: UiTestContext) -> dict[str, Any]:
        entry = context.require_widget(self.contract_id)
        entry.delete(0, "end")
        entry.insert(0, self.value)
        context.root.update_idletasks()
        context.root.update()
        results = context.require_widget(self.results_contract_id)
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

    def assert_result(
        self, context: UiTestContext, observation: dict[str, Any]
    ) -> None:
        assert observation["value"] == self.value
        assert observation["result_count"] == self.expected_result_count
        assert observation["summary"] == self.expected_summary


@dataclass(frozen=True)
class TreeSelectionContract(ComponentContract):
    """Select a real Treeview row and assert selection plus dependent details."""

    contract_id: str
    row_id: str
    summary_contract_id: str
    expected_summary: str
    expected_values: tuple[str, ...] | None = None

    def execute(self, context: UiTestContext) -> dict[str, Any]:
        tree = context.require_widget(self.contract_id)
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
        return {
            "selection": tuple(tree.selection()),
            "summary": summary_text,
            "values": tuple(tree.item(self.row_id, "values")),
        }

    def assert_result(
        self, context: UiTestContext, observation: dict[str, Any]
    ) -> None:
        assert observation["selection"] == (self.row_id,)
        assert observation["summary"] == self.expected_summary
        if self.expected_values is not None:
            assert observation["values"] == self.expected_values


@dataclass(frozen=True)
class ChoiceViewContract(ComponentContract):
    """Select every declared value in a real ttk Combobox."""

    contract_id: str
    value: str
    expected_event: str

    def execute(self, context: UiTestContext) -> dict[str, Any]:
        choice = context.require_widget(self.contract_id)
        choice.set(self.value)
        choice.event_generate("<<ComboboxSelected>>")
        context.root.update_idletasks()
        return {
            "value": choice.get(),
            "events": list(context.events),
        }

    def assert_result(
        self, context: UiTestContext, observation: dict[str, Any]
    ) -> None:
        assert observation["value"] == self.value
        assert observation["events"] == [self.expected_event]


@dataclass(frozen=True)
class NotebookViewContract(ComponentContract):
    """Visit every Notebook page and assert the expected controls are visible."""

    contract_id: str
    views: tuple[tuple[str, tuple[str, ...]], ...]

    def execute(self, context: UiTestContext) -> list[dict[str, Any]]:
        notebook = context.require_widget(self.contract_id)
        observations = []
        for label, control_ids in self.views:
            tab_id = next(
                (
                    tab
                    for tab in notebook.tabs()
                    if notebook.tab(tab, "text") == label
                ),
                None,
            )
            if tab_id is None:
                raise AssertionError(f"Notebook view {label!r} does not exist.")
            notebook.select(tab_id)
            context.root.update_idletasks()
            context.root.update()
            observations.append(
                {
                    "selected": notebook.tab(notebook.select(), "text"),
                    "mapped": {
                        control_id: bool(
                            context.require_widget(control_id).winfo_ismapped()
                        )
                        for control_id in control_ids
                    },
                }
            )
        return observations

    def assert_result(
        self, context: UiTestContext, observation: list[dict[str, Any]]
    ) -> None:
        assert len(observation) == len(self.views)
        for result, (label, control_ids) in zip(
            observation, self.views, strict=True
        ):
            assert result["selected"] == label
            assert set(result["mapped"]) == set(control_ids)
            assert all(result["mapped"].values())


@dataclass(frozen=True)
class PaneGroupContract(ComponentContract):
    """Assert a real paned window contains visible, non-zero child panes."""

    contract_id: str
    orientation: str
    pane_ids: tuple[str, ...]

    def execute(self, context: UiTestContext) -> dict[str, Any]:
        group = context.require_widget(self.contract_id)
        context.root.update_idletasks()
        context.root.update()
        panes = {
            getattr(pane, "_kicad_ui_contract_id", None): pane
            for pane in group.winfo_children()
        }
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

    def assert_result(
        self, context: UiTestContext, observation: dict[str, Any]
    ) -> None:
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


def _walk_widgets(root: Any):
    yield root
    children = root.winfo_children()
    for child in children:
        yield from _walk_widgets(child)


def discover_interactive_controls(root: Any) -> dict[str, Any]:
    """Return all tagged action/view controls, including registered menu items.

    The discovery list is intentionally limited to controls with an interaction
    contract. Static labels and decorative frames are not actionable controls.
    """
    discovered: dict[str, Any] = {}
    for widget in _walk_widgets(root):
        widget_class = widget.winfo_class()
        contract_id = getattr(widget, "_kicad_ui_contract_id", None)
        if contract_id:
            if contract_id in discovered:
                raise AssertionError(f"Duplicate UI contract ID: {contract_id}")
            discovered[contract_id] = widget

        if widget_class == "Menu":
            menu_items = getattr(widget, "_kicad_ui_menu_item_ids", {})
            for item_id, index in menu_items.items():
                if item_id in discovered:
                    raise AssertionError(f"Duplicate UI contract ID: {item_id}")
                discovered[item_id] = MenuEntry(widget, int(index))
    return discovered


def assert_control_coverage(root: Any, expected_ids: set[str]) -> dict[str, Any]:
    """Fail when an interactive widget is untagged or outside the declared map."""
    missing_tags = []
    for widget in _walk_widgets(root):
        widget_class = widget.winfo_class()
        if widget_class in INTERACTIVE_WIDGET_CLASSES:
            if not getattr(widget, "_kicad_ui_contract_id", None):
                missing_tags.append(widget_class)
        elif widget_class == "Menu":
            tagged_indexes = {
                int(index)
                for index in getattr(widget, "_kicad_ui_menu_item_ids", {}).values()
            }
            last_index = widget.index("end")
            if last_index is None:
                continue
            for index in range(int(last_index) + 1):
                if widget.type(index) in {"separator", "tearoff"}:
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
