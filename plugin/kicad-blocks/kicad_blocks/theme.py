"""Shared Tk styling and compact desktop widgets for KiCad Blocks."""

from __future__ import annotations

import itertools
import tkinter as tk
import weakref
from tkinter import ttk
from typing import Any

from .theme_tokens import (
    UI_FONT_SIZES,
    UI_SPACING,
    palette_for_theme,
    resolve_theme,
)


class AutoHideScrollbar(ttk.Scrollbar):
    """A quiet idle rail that reveals a usable thumb on hover or scrolling."""

    _instances = itertools.count(1)
    IDLE_WIDTH = 2
    ACTIVE_WIDTH = 10
    HIDE_DELAY_MS = 900

    def __init__(self, master=None, *, palette: dict[str, str], **kwargs):
        self._scroll_command = kwargs.pop("command", None)
        orient = str(kwargs.get("orient", "vertical")).casefold()
        orient_name = "Horizontal" if orient.startswith("h") else "Vertical"
        token = next(self._instances)
        self._base_style = f"{orient_name}.TScrollbar"
        self._idle_style = f"AutoHide{token}.Idle.{orient_name}.TScrollbar"
        self._active_style = f"AutoHide{token}.Active.{orient_name}.TScrollbar"
        self._style = ttk.Style(master)
        self._hide_after_id = None
        self._scrollable = False
        self._hovered = False
        self._last_fractions: tuple[float, float] | None = None
        self._configure_styles(palette)
        kwargs["style"] = self._idle_style
        kwargs["command"] = self._dispatch_scroll
        super().__init__(master, **kwargs)
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")

        registry_root = self._root()
        registry = getattr(registry_root, "_kicad_blocks_auto_hide_scrollbars", None)
        if registry is None:
            registry = weakref.WeakSet()
            registry_root._kicad_blocks_auto_hide_scrollbars = registry
        registry.add(self)

    @property
    def idle_style(self) -> str:
        return self._idle_style

    @property
    def active_style(self) -> str:
        return self._active_style

    @property
    def scrollable(self) -> bool:
        return self._scrollable

    def _configure_styles(self, palette: dict[str, str]) -> None:
        try:
            layout = self._style.layout(self._base_style)
        except tk.TclError:
            layout = []
        self._style.layout(self._idle_style, layout)
        self._style.layout(self._active_style, layout)
        self._style.configure(
            self._idle_style,
            background=palette["background"],
            troughcolor=palette["background"],
            bordercolor=palette["background"],
            lightcolor=palette["background"],
            darkcolor=palette["background"],
            relief="flat",
            borderwidth=0,
            width=self.IDLE_WIDTH,
        )
        self._style.map(
            self._idle_style,
            background=[("!disabled", palette["background"])],
        )
        self._style.configure(
            self._active_style,
            background=palette["scroll_thumb"],
            troughcolor=palette["scroll_trough"],
            bordercolor=palette["scroll_trough"],
            lightcolor=palette["scroll_thumb"],
            darkcolor=palette["scroll_thumb"],
            relief="flat",
            borderwidth=0,
            width=self.ACTIVE_WIDTH,
        )
        self._style.map(
            self._active_style,
            background=[
                ("pressed", palette["accent"]),
                ("active", palette["accent"]),
                ("!disabled", palette["scroll_thumb"]),
            ],
        )

    def set(self, first: str, last: str) -> None:
        super().set(first, last)
        try:
            fractions = (float(first), float(last))
        except (TypeError, ValueError):
            return
        self._scrollable = fractions[0] > 0.0 or fractions[1] < 1.0
        changed = fractions != self._last_fractions
        self._last_fractions = fractions
        if not self._scrollable:
            self._cancel_hide()
            self._set_idle_style()
        elif changed:
            self._reveal()

    def _dispatch_scroll(self, *args):
        self._reveal()
        if callable(self._scroll_command):
            return self._scroll_command(*args)
        if self._scroll_command:
            return self.tk.call(self._scroll_command, *args)
        return None

    def _on_enter(self, _event=None) -> None:
        self._hovered = True
        self._reveal()

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._schedule_hide()

    def _reveal(self) -> None:
        if not self._scrollable:
            return
        self._cancel_hide()
        try:
            self.configure(style=self._active_style)
            self._hide_after_id = self.after(self.HIDE_DELAY_MS, self._hide_if_idle)
        except tk.TclError:
            self._hide_after_id = None

    def _schedule_hide(self) -> None:
        self._cancel_hide()
        if self._scrollable and not self._hovered:
            try:
                self._hide_after_id = self.after(
                    self.HIDE_DELAY_MS, self._hide_if_idle
                )
            except tk.TclError:
                self._hide_after_id = None

    def _hide_if_idle(self) -> None:
        self._hide_after_id = None
        if not self._hovered:
            self._set_idle_style()

    def _set_idle_style(self) -> None:
        try:
            self.configure(style=self._idle_style)
        except tk.TclError:
            pass

    def _cancel_hide(self) -> None:
        if self._hide_after_id is None:
            return
        try:
            self.after_cancel(self._hide_after_id)
        except tk.TclError:
            pass
        self._hide_after_id = None

    def _on_destroy(self, event=None) -> None:
        if event is not None and getattr(event, "widget", self) is not self:
            return
        self._cancel_hide()


def _configure_classic_widgets(root: Any, palette: dict[str, str]) -> None:
    """Apply matching colors to classic Tk widgets inside themed windows."""
    try:
        stack = list(root.winfo_children())
    except (AttributeError, tk.TclError):
        return
    while stack:
        widget = stack.pop()
        try:
            widget_class = str(widget.winfo_class())
            if widget_class == "Text":
                widget.configure(
                    background=palette["field"],
                    foreground=palette["foreground"],
                    insertbackground=palette["foreground"],
                    selectbackground=palette["selected"],
                    selectforeground=palette["foreground"],
                    highlightbackground=palette["border"],
                    highlightcolor=palette["accent"],
                )
            elif widget_class == "Canvas":
                widget.configure(
                    background=palette["background"],
                    highlightbackground=palette["border"],
                    highlightcolor=palette["accent"],
                )
            elif widget_class in {"Entry", "Listbox"}:
                widget.configure(
                    background=palette["field"],
                    foreground=palette["foreground"],
                    insertbackground=palette["foreground"],
                    selectbackground=palette["selected"],
                    selectforeground=palette["foreground"],
                    highlightbackground=palette["border"],
                    highlightcolor=palette["accent"],
                )
            elif widget_class == "Treeview":
                widget.tag_configure("stripe-alt", background=palette["surface_alt"])
                widget.tag_configure("basic", foreground=palette["basic_foreground"])
                widget.tag_configure("extended", foreground=palette["muted"])
                widget.tag_configure("unknown", foreground=palette["muted"])
            stack.extend(widget.winfo_children())
        except (AttributeError, tk.TclError):
            continue


def apply_tk_theme(root: Any, theme: str | bool | None = None) -> dict[str, str]:
    """Apply the shared flat slate system to ttk and classic Tk widgets."""
    selected = resolve_theme(theme)
    palette = palette_for_theme(selected)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=palette["background"], highlightthickness=0)
    options = {
        "*Background": palette["background"],
        "*Foreground": palette["foreground"],
        "*Text.background": palette["field"],
        "*Text.foreground": palette["foreground"],
        "*Text.insertBackground": palette["foreground"],
        "*Entry.background": palette["field"],
        "*Entry.foreground": palette["foreground"],
        "*Entry.insertBackground": palette["foreground"],
        "*Listbox.background": palette["field"],
        "*Listbox.foreground": palette["foreground"],
        "*Canvas.background": palette["background"],
        "*selectBackground": palette["selected"],
        "*selectForeground": palette["foreground"],
        "*highlightBackground": palette["border"],
        "*highlightColor": palette["accent"],
        "*troughColor": palette["scroll_trough"],
    }
    for option, value in options.items():
        root.option_add(option, value)

    style.configure("TFrame", background=palette["background"])
    style.configure("TabHost.TFrame", background=palette["background"])
    style.configure(
        "TLabel",
        background=palette["background"],
        foreground=palette["foreground"],
        font=("TkDefaultFont", UI_FONT_SIZES["body"]),
    )
    style.configure(
        "Title.TLabel",
        background=palette["background"],
        foreground=palette["foreground"],
        font=("TkDefaultFont", UI_FONT_SIZES["title"], "bold"),
    )
    section_font = ("TkDefaultFont", UI_FONT_SIZES["card_title"], "bold")
    style.configure(
        "Section.TLabel",
        background=palette["background"],
        foreground=palette["muted"],
        font=("TkDefaultFont", UI_FONT_SIZES["section"], "bold"),
    )
    style.configure(
        "SectionTitleSurface.TLabel",
        background=palette["surface"],
        foreground=palette["foreground"],
        font=section_font,
    )
    style.configure(
        "Muted.TLabel",
        background=palette["background"],
        foreground=palette["muted"],
        font=("TkDefaultFont", UI_FONT_SIZES["caption"]),
    )
    style.configure("Card.TFrame", background=palette["surface"], relief="flat", borderwidth=0)
    style.configure("Card.TLabel", background=palette["surface"], foreground=palette["foreground"])
    style.configure("CardMeta.TLabel", background=palette["surface"], foreground=palette["muted"])
    style.configure("ProductPreview.TFrame", background=palette["surface"], relief="flat", borderwidth=0)
    style.configure("ProductImage.TFrame", background=palette["surface_alt"], relief="flat", borderwidth=0)
    style.configure("ProductImage.TLabel", background=palette["surface_alt"], foreground=palette["muted"])
    style.configure("ProductTitle.TLabel", background=palette["surface"], foreground=palette["foreground"], font=("TkDefaultFont", UI_FONT_SIZES["card_title"], "bold"))
    style.configure("ProductMeta.TLabel", background=palette["surface"], foreground=palette["muted"], font=("TkDefaultFont", UI_FONT_SIZES["caption"]))
    style.configure("ProductKey.TLabel", background=palette["surface"], foreground=palette["muted"], font=("TkDefaultFont", UI_FONT_SIZES["caption"], "bold"))
    style.configure("ProductMetric.TLabel", background=palette["surface"], foreground=palette["foreground"], font=("TkDefaultFont", UI_FONT_SIZES["metric"], "bold"))
    style.configure(
        "ValidationError.TLabel",
        background=palette["surface"],
        foreground=palette["error"],
    )
    style.configure(
        "ValidationWarning.TLabel",
        background=palette["surface"],
        foreground=palette["warning"],
    )
    style.configure(
        "ValidationInfo.TLabel",
        background=palette["surface"],
        foreground=palette["info"],
    )

    style.configure(
        "TLabelframe",
        background=palette["background"],
        foreground=palette["foreground"],
        bordercolor=palette["background"],
        lightcolor=palette["background"],
        darkcolor=palette["background"],
        relief="flat",
        borderwidth=0,
    )
    style.configure(
        "TLabelframe.Label",
        background=palette["background"],
        foreground=palette["muted"],
        padding=(0, 0),
        font=section_font,
    )

    style.configure(
        "TButton",
        background=palette["surface_alt"],
        foreground=palette["foreground"],
        bordercolor=palette["border"],
        focuscolor=palette["accent"],
        focusthickness=1,
        padding=(10, 6),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[
            ("disabled", palette["surface"]),
            ("pressed", palette["selected"]),
            ("active", palette["hover"]),
        ],
        foreground=[
            ("disabled", palette["muted"]),
            ("!disabled", palette["foreground"]),
        ],
        bordercolor=[
            ("focus", palette["accent"]),
            ("active", palette["accent"]),
            ("!focus", palette["border"]),
        ],
    )
    style.configure(
        "Primary.TButton",
        background=palette["accent"],
        foreground=palette["accent_foreground"],
        bordercolor=palette["accent"],
        focuscolor=palette["accent"],
        focusthickness=1,
        padding=(12, 7),
        relief="flat",
        font=("TkDefaultFont", UI_FONT_SIZES["body"], "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", palette["surface_alt"]),
            ("pressed", palette["selected"]),
            ("active", palette["accent"]),
        ],
        foreground=[
            ("disabled", palette["muted"]),
            ("!disabled", palette["accent_foreground"]),
        ],
        bordercolor=[("focus", palette["accent"]), ("!focus", palette["accent"])],
    )
    style.configure(
        "TEntry",
        fieldbackground=palette["field"],
        foreground=palette["foreground"],
        insertcolor=palette["foreground"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        padding=(7, 5),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", palette["accent"])],
        lightcolor=[("focus", palette["accent"])],
        darkcolor=[("focus", palette["accent"])],
    )
    style.configure(
        "TCombobox",
        fieldbackground=palette["field"],
        background=palette["surface_alt"],
        foreground=palette["foreground"],
        arrowcolor=palette["foreground"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        padding=(7, 5),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", palette["field"])],
        selectbackground=[("readonly", palette["selected"])],
        selectforeground=[("readonly", palette["foreground"])],
        bordercolor=[("focus", palette["accent"])],
    )
    style.configure(
        "Treeview",
        background=palette["surface"],
        fieldbackground=palette["surface"],
        foreground=palette["foreground"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        rowheight=29,
        relief="flat",
        borderwidth=0,
        padding=(2, 1),
    )
    style.map(
        "Treeview",
        background=[("selected", palette["selected"])],
        foreground=[("selected", palette["foreground"])],
    )
    style.configure(
        "Treeview.Heading",
        background=palette["surface_alt"],
        foreground=palette["muted"],
        bordercolor=palette["border"],
        lightcolor=palette["surface_alt"],
        darkcolor=palette["border"],
        relief="flat",
        padding=(8, 8),
        font=("TkDefaultFont", UI_FONT_SIZES["body"], "bold"),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", palette["hover"])],
        foreground=[("active", palette["foreground"])],
    )

    style.configure("TabBar.TFrame", background=palette["surface"], relief="flat", borderwidth=0)
    style.configure("TabItem.TFrame", background=palette["surface"], relief="flat", borderwidth=0)
    style.configure("TabIndicator.TFrame", background=palette["accent"], relief="flat", borderwidth=0)
    style.configure("TabIndicatorInactive.TFrame", background=palette["surface"], relief="flat", borderwidth=0)
    style.configure(
        "Tab.TLabel",
        background=palette["surface"],
        foreground=palette["muted"],
        padding=(UI_SPACING["md"], UI_SPACING["sm"]),
        font=("TkDefaultFont", UI_FONT_SIZES["body"]),
    )
    style.map(
        "Tab.TLabel",
        background=[("active", palette["hover"]), ("!disabled", palette["surface"])],
        foreground=[("focus", palette["accent"]), ("active", palette["foreground"]), ("!disabled", palette["muted"])],
    )
    style.configure(
        "ActiveTab.TLabel",
        background=palette["surface"],
        foreground=palette["accent"],
        padding=(UI_SPACING["md"], UI_SPACING["sm"]),
        font=("TkDefaultFont", UI_FONT_SIZES["body"], "bold"),
    )
    style.map(
        "ActiveTab.TLabel",
        background=[("active", palette["hover"]), ("!disabled", palette["surface"])],
        foreground=[("!disabled", palette["accent"])],
    )

    scrollbar_options = {
        "background": palette["scroll_thumb"],
        "troughcolor": palette["scroll_trough"],
        "bordercolor": palette["scroll_trough"],
        "lightcolor": palette["scroll_thumb"],
        "darkcolor": palette["scroll_thumb"],
        "relief": "flat",
        "borderwidth": 0,
        "width": 12,
    }
    style.configure("TScrollbar", **scrollbar_options)
    for scrollbar_style in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(scrollbar_style, **scrollbar_options)
        style.map(
            scrollbar_style,
            background=[
                ("active", palette["accent"]),
                ("pressed", palette["accent"]),
                ("!disabled", palette["scroll_thumb"]),
            ],
        )
    for scrollbar_style, layout in (
        (
            "Vertical.TScrollbar",
            [("Vertical.Scrollbar.trough", {
                "sticky": "ns",
                "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
            })],
        ),
        (
            "Horizontal.TScrollbar",
            [("Horizontal.Scrollbar.trough", {
                "sticky": "we",
                "children": [("Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
            })],
        ),
    ):
        try:
            style.layout(scrollbar_style, layout)
        except tk.TclError:
            pass

    style.configure("TSeparator", background=palette["border"])
    style.configure("TCheckbutton", background=palette["background"], foreground=palette["foreground"])
    style.map("TCheckbutton", background=[("active", palette["background"])], foreground=[("disabled", palette["muted"])])

    root._kicad_blocks_theme = selected
    root._kicad_blocks_palette = palette
    _configure_classic_widgets(root, palette)

    registry_root = root._root() if hasattr(root, "_root") else root
    for scrollbar in tuple(getattr(registry_root, "_kicad_blocks_auto_hide_scrollbars", ())):
        try:
            scrollbar._configure_styles(palette)
        except tk.TclError:
            continue
    return palette
