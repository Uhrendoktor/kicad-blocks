"""Reusable, keyboard-accessible flat tab control for KiCad Blocks dialogs."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import ttk
from typing import TYPE_CHECKING, TypeAlias

from .theme import UI_SPACING

if TYPE_CHECKING:
    from tkinter import TkOption
else:
    TkOption: TypeAlias = (
        str | int | float | bool | bytes | type(None) | tuple | list | dict | Sequence | Callable
    )


class FlatTabControl(ttk.Frame):
    """Flat text tabs with a small active underline and Notebook-compatible API.

    Pages remain direct children of the tab host so callers can continue to use
    tabs(), tab(), select(), and nametowidget() just as they did with ttk.Notebook.
    """

    def __init__(
        self, master: tk.Misc, *, contract_id: str | None = None, **kwargs: TkOption
    ) -> None:
        super().__init__(master, style="TabHost.TFrame", **kwargs)
        self._kicad_ui_contract_id = contract_id
        self._tabbar = ttk.Frame(self, style="TabBar.TFrame")
        self._tabbar.grid(row=0, column=0, sticky="ew", pady=(0, UI_SPACING["xs"]))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._pages: list[tk.Misc] = []
        self._captions: list[str] = []
        self._labels: list[ttk.Label] = []
        self._indicators: list[ttk.Frame] = []
        self._selected: tk.Misc | None = None

    @property
    def selected_tab(self) -> tk.Misc | None:
        return self._selected

    @property
    def tab_labels(self) -> tuple[ttk.Label, ...]:
        """Expose focusable tab labels for accessibility and interaction tests."""
        return tuple(self._labels)

    def add(self, page: tk.Misc, *, text: str) -> None:
        if page.master is not self:
            raise ValueError("Each tab page must be a direct child of FlatTabControl.")
        index = len(self._pages)
        item = ttk.Frame(self._tabbar, style="TabItem.TFrame")
        item.grid(row=0, column=index, sticky="ew")
        self._tabbar.columnconfigure(index, weight=1, uniform="ui-tabs")
        label = ttk.Label(
            item,
            text=text,
            style="Tab.TLabel",
            anchor="center",
            takefocus=True,
            cursor="hand2",
        )
        label.grid(row=0, column=0, sticky="ew")
        indicator = ttk.Frame(item, style="TabIndicatorInactive.TFrame", height=2)
        indicator.grid(row=1, column=0, sticky="ew")
        indicator.grid_propagate(False)
        item.columnconfigure(0, weight=1)

        page.grid(row=1, column=0, sticky="nsew")
        page.grid_remove()
        self._pages.append(page)
        self._captions.append(str(text))
        self._labels.append(label)
        self._indicators.append(indicator)

        label.bind("<Button-1>", lambda _event, selected=page: self.select(selected))
        label.bind("<Return>", lambda _event, selected=page: self._activate(selected))
        label.bind("<space>", lambda _event, selected=page: self._activate(selected))
        label.bind("<Left>", lambda _event, selected=index: self._move(selected, -1))
        label.bind("<Right>", lambda _event, selected=index: self._move(selected, 1))
        label.bind("<Home>", lambda _event: self._move(0, 0))
        label.bind("<End>", lambda _event: self._move(len(self._pages) - 1, 0))

        if self._selected is None:
            self.select(page)

    def tabs(self) -> list[str]:
        """Return page paths using the ttk.Notebook-compatible representation."""
        return [str(page) for page in self._pages]

    def index(self, page: tk.Misc | str | int) -> int:
        """Return a page index using the common ttk.Notebook forms."""
        if page == "current":
            page = self._selected
        if isinstance(page, int):
            return page
        return self._pages.index(self._resolve_page(page))

    def nametowidget(self, name: str) -> tk.Misc:
        """Resolve full Tk page paths the same way callers expect from Notebook."""
        for page in self._pages:
            if str(page) == str(name):
                return page
        return super().nametowidget(name)

    def tab(
        self, page: tk.Misc | str, option: str | None = None
    ) -> str | dict[str, str] | None:
        """Return a page caption, or a small Notebook-compatible option mapping."""
        selected = self._resolve_page(page)
        index = self._pages.index(selected)
        if option == "text":
            return self._captions[index]
        values = {"text": self._captions[index], "state": "normal"}
        return values if option is None else values.get(option)

    def select(self, page: tk.Misc | str | None = None) -> str:
        """Select a page, or return the selected page path when called without one."""
        if page is None:
            return str(self._selected) if self._selected is not None else ""
        selected = self._resolve_page(page)
        previous = self._selected
        self._selected = selected
        for candidate, label, indicator in zip(
            self._pages, self._labels, self._indicators, strict=True
        ):
            active = candidate is selected
            if active:
                candidate.grid()
                candidate.tkraise()
                label.configure(style="ActiveTab.TLabel")
                indicator.configure(style="TabIndicator.TFrame")
            else:
                candidate.grid_remove()
                label.configure(style="Tab.TLabel")
                indicator.configure(style="TabIndicatorInactive.TFrame")
        if previous is not selected:
            self.event_generate("<<NotebookTabChanged>>", when="tail")
        return str(selected)

    def _resolve_page(self, page: tk.Misc | str) -> tk.Misc:
        selected = next(
            (
                candidate for candidate in self._pages
                if candidate is page or str(candidate) == str(page)
            ),
            None,
        )
        if selected is None:
            raise ValueError(f"{page!r} is not a page in this tab control.")
        return selected

    def _activate(self, page: tk.Misc) -> str:
        self.select(page)
        return "break"

    def _move(self, index: int, offset: int) -> str:
        if not self._pages:
            return "break"
        if offset == 0:
            target = min(max(index, 0), len(self._pages) - 1)
        else:
            target = (index + offset) % len(self._pages)
        self._labels[target].focus_set()
        self.select(self._pages[target])
        return "break"
