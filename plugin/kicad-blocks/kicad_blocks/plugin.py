"""KiCad IPC actions for opaque, library-backed schematic block symbols."""

from __future__ import annotations

import base64
import contextlib
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# The core block model and IPC helpers must remain importable in headless
# environments. Load Tkinter only when the interactive manager is invoked.
tk = None
ttk = None
filedialog = None
messagebox = None
simpledialog = None

from .auto_dependencies import synchronize_block_dependencies
from .extraction import selected_sheet_file, selected_sheet_instance_uuid
from .model import (
    LAYOUT_FIELD_NAME,
    PARAMETER_FIELD_PREFIX,
    PROFILE_FIELD_NAME,
    VARIANT_FIELD_NAME,
    parameter_field_name,
    resolve_parameter_values,
    resolve_profile_configuration,
    resolve_variant_implementation,
)
from .operations import (
    export_block_bom,
    find_kicad_cli,
    validate_block_library,
    validate_block_tree,
)
from .pcb_layout import BlockLayoutPlacement, merge_block_layout, merge_block_layouts
from .pcb_placement import placement_manifest_path, write_pcb_placement_manifest
from .repository import (
    LIBRARY_CATALOG_NAME,
    LIBRARY_NICKNAME,
    add_library_root,
    atomic_write,
    build_project_symbol_catalog,
    create_block,
    default_library_root,
    ensure_stable_block_identity,
    import_project_catalog_facade_edit,
    link_instance_to_local_copy,
    list_blocks,
    local_copy_for_instance,
    make_local_copy,
    publish_local_copy,
    refresh_library_block,
    register_project_symbol_library,
    remove_library_root,
    set_library_root,
)
from .repository import library_roots as configured_library_roots

LOG = logging.getLogger("kicad_blocks")

_KEEP_DEFAULT_VARIANT = object()


def _load_tkinter() -> None:
    """Load the optional desktop UI toolkit when the manager is run."""
    global tk, ttk, filedialog, messagebox, simpledialog
    if tk is not None:
        return

    import tkinter as tkinter_module
    from tkinter import filedialog as filedialog_module
    from tkinter import messagebox as messagebox_module
    from tkinter import simpledialog as simpledialog_module
    from tkinter import ttk as ttk_module

    tk = tkinter_module
    ttk = ttk_module
    filedialog = filedialog_module
    messagebox = messagebox_module
    simpledialog = simpledialog_module


def _system_prefers_dark_theme() -> bool | None:
    """Return an explicit theme override, or None to keep Tk’s native theme."""
    override = os.environ.get("KICAD_BLOCKS_THEME", "system").strip().casefold()
    if override in {"dark", "true", "1", "yes", "on"}:
        return True
    if override in {"light", "false", "0", "no", "off"}:
        return False
    # Tk does not expose a portable query for the host OS dark-mode preference.
    # Preserve the toolkit's native theme rather than probing OS-specific settings.
    return None


def _configure_tk_theme(root) -> None:
    """Apply a compact KiCad-compatible palette to themed and classic Tk widgets."""
    dark = _system_prefers_dark_theme()
    if dark is None:
        # Keep the toolkit/host native appearance for the system setting.
        root._kicad_blocks_palette = {}
        return
    colors = ({
        "background": "#202326", "surface": "#292d31", "surface_alt": "#33383d",
        "field": "#191c1f", "hover": "#3b4249", "selected": "#344b62",
        "foreground": "#e6e9ec", "muted": "#aab2ba", "border": "#48515a",
        "accent": "#78a9d4", "scroll_thumb": "#505a64", "scroll_trough": "#24282c",
    } if dark else {
        "background": "#e8eaed", "surface": "#f4f5f6", "surface_alt": "#e1e4e7",
        "field": "#ffffff", "hover": "#e8edf2", "selected": "#c9d8e6",
        "foreground": "#20252a", "muted": "#5f6871", "border": "#b8c0c8",
        "accent": "#4c789f", "scroll_thumb": "#c0c7ce", "scroll_trough": "#e4e7ea",
    })
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(background=colors["background"], highlightthickness=0)
    for option, value in {
        "*Background": colors["background"], "*Foreground": colors["foreground"],
        "*Text.background": colors["field"], "*Text.foreground": colors["foreground"],
        "*Text.insertBackground": colors["foreground"], "*Entry.background": colors["field"],
        "*Entry.foreground": colors["foreground"], "*Entry.insertBackground": colors["foreground"],
        "*Listbox.background": colors["field"], "*Listbox.foreground": colors["foreground"],
        "*Canvas.background": colors["background"], "*selectBackground": colors["selected"],
        "*selectForeground": colors["foreground"], "*highlightBackground": colors["border"],
        "*highlightColor": colors["accent"], "*troughColor": colors["scroll_trough"],
    }.items():
        root.option_add(option, value)
    style.configure("TFrame", background=colors["background"])
    style.configure("TPanedwindow", background=colors["background"])
    style.configure("TLabel", background=colors["background"], foreground=colors["foreground"])
    style.configure("TLabelframe", background=colors["background"], foreground=colors["foreground"],
                    bordercolor=colors["border"], lightcolor=colors["border"],
                    darkcolor=colors["border"], relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=colors["background"], foreground=colors["muted"], padding=(4, 0))
    style.configure(
        "TButton",
        background=colors["surface_alt"],
        foreground=colors["foreground"],
        bordercolor=colors["surface_alt"],
        lightcolor=colors["surface_alt"],
        darkcolor=colors["surface_alt"],
        borderwidth=0,
        focuscolor=colors["accent"],
        focusthickness=1,
        padding=(11, 7),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[
            ("disabled", colors["surface"]),
            ("pressed", colors["selected"]),
            ("active", colors["hover"]),
        ],
        foreground=[
            ("disabled", colors["muted"]),
            ("!disabled", colors["foreground"]),
        ],
        bordercolor=[("focus", colors["accent"]), ("!focus", colors["surface_alt"])],
        lightcolor=[("focus", colors["accent"]), ("!focus", colors["surface_alt"])],
        darkcolor=[("focus", colors["accent"]), ("!focus", colors["surface_alt"])],
    )
    style.configure("TEntry", fieldbackground=colors["field"], foreground=colors["foreground"],
                    insertcolor=colors["foreground"], bordercolor=colors["border"],
                    lightcolor=colors["border"], darkcolor=colors["border"], padding=(7, 5))
    style.map("TEntry", bordercolor=[("focus", colors["accent"])],
              lightcolor=[("focus", colors["accent"])], darkcolor=[("focus", colors["accent"])])
    style.configure("TCombobox", fieldbackground=colors["field"], background=colors["surface_alt"],
                    foreground=colors["foreground"], arrowcolor=colors["foreground"],
                    bordercolor=colors["border"], lightcolor=colors["border"],
                    darkcolor=colors["border"], padding=(7, 5))
    style.map("TCombobox", fieldbackground=[("readonly", colors["field"])],
              selectbackground=[("readonly", colors["selected"])],
              selectforeground=[("readonly", colors["foreground"])],
              bordercolor=[("focus", colors["accent"])])
    style.configure("Treeview", background=colors["surface"], fieldbackground=colors["surface"],
                    foreground=colors["foreground"], bordercolor=colors["border"],
                    lightcolor=colors["border"], darkcolor=colors["border"],
                    rowheight=36, relief="flat", borderwidth=0, padding=(4, 3))
    style.map("Treeview", background=[("selected", colors["selected"])],
              foreground=[("selected", colors["foreground"])])
    style.configure("Treeview.Heading", background=colors["surface_alt"], foreground=colors["muted"],
                    bordercolor=colors["border"], lightcolor=colors["surface_alt"],
                    darkcolor=colors["border"], relief="flat", padding=(8, 8),
                    font=("TkDefaultFont", 9, "bold"))
    style.map("Treeview.Heading", background=[("active", colors["hover"])],
              foreground=[("active", colors["foreground"])])
    style.configure(
        "TNotebook", background=colors["background"], borderwidth=0,
        bordercolor=colors["border"], lightcolor=colors["border"],
        darkcolor=colors["border"], tabmargins=(2, 4, 2, 0)
    )
    style.configure("TNotebook.Tab", background=colors["surface_alt"], foreground=colors["muted"],
                    bordercolor=colors["border"], padding=(13, 8))
    style.map("TNotebook.Tab", background=[("selected", colors["surface"]), ("active", colors["hover"])],
              foreground=[("selected", colors["foreground"]), ("active", colors["foreground"])])
    style.configure(
        "Vertical.TScrollbar",
        background=colors["scroll_thumb"],
        troughcolor=colors["scroll_trough"],
        bordercolor=colors["scroll_trough"],
        lightcolor=colors["scroll_thumb"],
        darkcolor=colors["scroll_thumb"],
        relief="flat",
        borderwidth=0,
        width=12,
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=colors["scroll_thumb"],
        troughcolor=colors["scroll_trough"],
        bordercolor=colors["scroll_trough"],
        lightcolor=colors["scroll_thumb"],
        darkcolor=colors["scroll_thumb"],
        relief="flat",
        borderwidth=0,
        width=12,
    )
    # Remove legacy arrow buttons; keep a single, low-contrast draggable thumb.
    style.layout(
        "Vertical.TScrollbar",
        [("Vertical.Scrollbar.trough", {
            "sticky": "ns",
            "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
        })],
    )
    style.layout(
        "Horizontal.TScrollbar",
        [("Horizontal.Scrollbar.trough", {
            "sticky": "we",
            "children": [("Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
        })],
    )
    style.configure(
        "TScrollbar",
        background=colors["scroll_thumb"],
        troughcolor=colors["scroll_trough"],
        bordercolor=colors["scroll_trough"],
        lightcolor=colors["scroll_thumb"],
        darkcolor=colors["scroll_thumb"],
        relief="flat",
        borderwidth=0,
        width=12,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", colors["accent"]), ("pressed", colors["accent"])],
    )
    style.map(
        "Horizontal.TScrollbar",
        background=[("active", colors["accent"]), ("pressed", colors["accent"])],
    )
    style.configure("TSeparator", background=colors["border"])
    root._kicad_blocks_palette = colors


def _set_window_geometry(window, width: int, height: int, min_width: int, min_height: int) -> None:
    """Size a completed dialog to its content and keep every action visible."""
    window.update_idletasks()
    sw = max(int(window.winfo_screenwidth()), 640)
    sh = max(int(window.winfo_screenheight()), 480)
    usable_width = max(640, sw - 80)
    usable_height = max(480, sh - 100)
    try:
        requested_width = int(window.winfo_reqwidth())
        requested_height = int(window.winfo_reqheight())
    except Exception:
        # Tests and non-GUI callers can provide lightweight window adapters
        # without Tk's measurement methods; actual Tk sizing failures should
        # also fall back to the explicit geometry instead of aborting startup.
        LOG.debug("Could not query requested Tk geometry; using configured size", exc_info=True)
        requested_width, requested_height = width, height
    w = min(max(width, requested_width), usable_width)
    h = min(max(height, requested_height), usable_height)
    window.geometry(f"{w}x{h}+{max(0, (sw-w)//2)}+{max(0, (sh-h)//2)}")
    window.minsize(min(min_width, w), min(min_height, h))
    master = getattr(window, "master", None)
    if master is not None:
        with contextlib.suppress(tk.TclError):
            window.transient(master)



def _attach_tk_window_to_editor(window, _editor_window_id: str | None = None) -> bool:
    """Associate a Tk dialog with its parent inside this plugin process.

    KiCad IPC does not expose a portable cross-process native window handle or
    transient-owner API. Tk's portable transient relationship is limited to
    windows managed by this Tk interpreter; do not mutate foreign window IDs.
    """
    master = getattr(window, "master", None)
    if master is None:
        return False
    try:
        window.transient(master)
        return True
    except (AttributeError, tk.TclError):
        LOG.debug("Could not associate the Tk dialog with its in-process parent", exc_info=True)
        return False

def _add_tooltip(widget, text: str) -> None:
    """Attach a small themed tooltip to a compact icon button."""
    tooltip = [None]

    def show(_event=None):
        if tooltip[0] is not None or not widget.winfo_exists():
            return
        palette = getattr(widget.winfo_toplevel(), "_kicad_blocks_palette", {})
        background = palette.get("surface_alt", "#e8eaed")
        foreground = palette.get("foreground", "#20252a")
        popup = tk.Toplevel(widget)
        popup.wm_overrideredirect(True)
        with contextlib.suppress(tk.TclError):
            popup.wm_attributes("-topmost", True)
        label = tk.Label(
            popup,
            text=text,
            background=background,
            foreground=foreground,
            relief="solid",
            borderwidth=1,
            padx=7,
            pady=4,
            font=("TkDefaultFont", 9),
        )
        label.pack()
        popup.update_idletasks()
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + 5
        popup.geometry(f"+{x}+{y}")
        tooltip[0] = popup

    def hide(_event=None):
        popup = tooltip[0]
        tooltip[0] = None
        if popup is not None:
            with contextlib.suppress(tk.TclError):
                popup.destroy()

    widget.bind("<Enter>", show, add="+")
    widget.bind("<FocusIn>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<FocusOut>", hide, add="+")
    widget.bind("<ButtonPress>", hide, add="+")


def _create_action_icon(parent, name: str):
    """Render one semantic 20px toolbar icon using the current KiCad palette."""
    drawings = {
        "place": '<rect x="4" y="4" width="12" height="12" rx="2"/><path d="M16 16l4 4M10 7v6M7 10h6"/>',
        "implementation": '<path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 11h6M9 15h6M9 19h4"/>',
        "facade": '<rect x="3" y="5" width="14" height="14" rx="2"/><path d="M7 9h6M7 13h4M14 17l6-6 2 2-6 6-3 1z"/>',
        "variants": '<path d="M4 7l8-4 8 4-8 4zM4 12l8 4 8-4M4 17l8 4 8-4"/>',
        "pcb": '<rect x="2" y="3" width="20" height="18" rx="2"/><path d="M7 8h3v3H7zM15 13h3v3h-3zM10 9h4v5h-4zM4 18h4M16 6h3"/>',
        "layouts": '<rect x="2" y="5" width="15" height="15" rx="2"/><path d="M7 2h13v14M5 10h9M5 14h6"/>',
        "refresh": '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 9A7 7 0 0 1 18 6l2 6M4 12l2 6a7 7 0 0 0 12.5-3"/>',
        "libraries": '<path d="M3 6h18M5 11h14M7 16h10M9 21h6"/><path d="M4 3h16v20H4z"/>',
        "add": '<circle cx="12" cy="12" r="9"/><path d="M12 7v10M7 12h10"/>',
        "create": '<path d="M3 7h7l2 2h9v11H3z"/><path d="M12 11v6M9 14h6"/>',
        "change": '<path d="M3 7h7l2 2h9v11H3z"/><path d="m14 16 5-5 2 2-5 5-3 1z"/>',
        "remove": '<path d="M3 7h18M8 7V4h8v3M6 7l1 14h10l1-14"/><path d="M10 11v6M14 11v6"/>',
        "default": '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z"/>',
        "close": '<path d="m6 6 12 12M18 6 6 18"/>',
        "configure": '<circle cx="12" cy="12" r="3"/><path d="M19 13.5a7.5 7.5 0 0 0 0-3l2-1.5-2-3.5-2.4 1a7.5 7.5 0 0 0-2.6-1.5L13.7 2h-4l-.4 3a7.5 7.5 0 0 0-2.6 1.5l-2.4-1-2 3.5 2 1.5a7.5 7.5 0 0 0 0 3l-2 1.5 2 3.5 2.4-1a7.5 7.5 0 0 0 2.6 1.5l.4 3h4l.4-3a7.5 7.5 0 0 0 2.6-1.5l2.4 1 2-3.5z"/>',
        "publish": '<path d="M12 3v12M7 8l5-5 5 5"/><path d="M4 15v5h16v-5"/>',
        "local": '<path d="M4 4h11l5 5v11H4z"/><path d="M15 4v5h5M8 14h8M8 17h5"/>',
        "sync": '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 9A7 7 0 0 1 18 6M18.5 15A7 7 0 0 1 6 18"/>',
        "validate": '<path d="m5 12 4 4L19 6"/><path d="M12 2a10 10 0 1 0 10 10"/>',
        "bom": '<path d="M6 3h12v18H6z"/><path d="M9 8h6M9 12h6M9 16h6"/>',
        "erc": '<path d="M12 2 20 5v6c0 5-3.5 8.5-8 11-4.5-2.5-8-6-8-11V5z"/><path d="m8 12 2.5 2.5L16 9"/>',
        "save": '<path d="M4 3h14l3 3v15H3V3z"/><path d="M7 3v6h10V3M7 21v-8h10v8"/>',
        "cancel": '<circle cx="12" cy="12" r="9"/><path d="m8 8 8 8M16 8l-8 8"/>',
        "open": '<path d="M3 12 12 3l9 9M12 3v18"/>',
        "select": '<path d="M4 3v18l5-5 3 6 3-1-3-6 7-1z"/>',
        "edit": '<path d="m4 16 11-11 4 4L8 20H4z"/><path d="m13 7 4 4"/>',
        "file": '<path d="M6 2h8l5 5v15H6z"/><path d="M14 2v6h5M9 13h7M9 17h7"/>',
    }
    path_data = drawings.get(name)
    if not path_data:
        return None
    palette = getattr(parent.winfo_toplevel(), "_kicad_blocks_palette", {})
    accent = palette.get("accent", "#4c789f")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
        f'<g fill="none" stroke="{accent}" stroke-width="1.7" stroke-linecap="round" '
        f'stroke-linejoin="round">{path_data}</g>'
        '</svg>'
    )
    try:
        import resvg_py

        png = resvg_py.svg_to_bytes(
            svg_string=svg, width=20, height=20
        )
        image = tk.PhotoImage(
            data=base64.b64encode(png).decode("ascii"), master=parent
        )
    except (ImportError, OSError, tk.TclError, ValueError):
        LOG.debug("Could not render toolbar icon %s", name, exc_info=True)
        return None
    owner = parent.winfo_toplevel()
    images = getattr(owner, "_kicad_blocks_action_icons", {})
    images[name] = image
    owner._kicad_blocks_action_icons = images
    return image


def _action_button(
    parent,
    text: str,
    command,
    icon: str,
    tooltip: str,
    **button_options,
):
    """Create a consistently padded, semantic icon-and-label action button."""
    image = _create_action_icon(parent, icon)
    options = {"text": text, "command": command, "padding": (9, 6)}
    options.update(button_options)
    if image is not None:
        options.update({"image": image, "compound": "left"})
    button = ttk.Button(parent, **options)
    _add_tooltip(button, tooltip)
    return button


def project_directory(schematic) -> Path:
    project = schematic.get_project()
    raw = Path(project.path).expanduser()
    if raw.is_file() or raw.suffix.lower() == ".kicad_pro":
        raw = raw.parent
    resolved = raw.resolve()
    if not resolved.is_dir():
        raise ValueError(
            f"The current project directory does not exist: {resolved}. "
            "Save the KiCad project before using this action."
        )
    return resolved


_LAST_LIBRARY_RELOAD_WARNING: str | None = None
_LAST_LIBRARY_RELOAD_OK = True


def _reload_symbol_library(kicad, nickname: str) -> bool:
    """Reload a project library using GUI-supported commands only."""
    global _LAST_LIBRARY_RELOAD_WARNING, _LAST_LIBRARY_RELOAD_OK
    from kipy.errors import LibraryCommandError

    _LAST_LIBRARY_RELOAD_WARNING = None
    _LAST_LIBRARY_RELOAD_OK = True
    try:
        # LT_SYMBOL=1; LTS_PROJECT=2. LoadAllLibraries is headless-only.
        kicad.reload_library(type=1, scope=2, nicknames=[nickname])
        statuses = kicad.wait_for_libraries(types=[1], scope=2, timeout_s=60.0)
        status_items = statuses if isinstance(statuses, (list, tuple)) else ()
        errors = [
            item.error_message for item in status_items
            if getattr(item, "nickname", None) == nickname
            and getattr(item, "error_message", None)
        ]
        if errors:
            raise RuntimeError("; ".join(str(error) for error in errors))
        return True
    except (LibraryCommandError, RuntimeError, TimeoutError) as exc:
        _LAST_LIBRARY_RELOAD_OK = False
        _LAST_LIBRARY_RELOAD_WARNING = (
            f"The block and project library table were saved, but Eeschema could not "
            f"reload {nickname!r}: {exc}. Save the project and restart Eeschema to "
            "load the updated block library."
        )
        LOG.warning(_LAST_LIBRARY_RELOAD_WARNING)
        return False




def _register_block(
    project_dir: Path, record, kicad, library_paths: list[Path] | None = None
):
    """Refresh the project's unified catalog from every registered block library."""
    record = ensure_stable_block_identity(record)
    roots = [Path(item).expanduser().resolve() for item in (
        library_paths if library_paths is not None else configured_library_roots()
    )]
    catalog = build_project_symbol_catalog(project_dir, roots)
    register_project_symbol_library(
        project_dir,
        LIBRARY_NICKNAME,
        catalog,
        description="KiCad Blocks — all registered functional-block libraries",
        replace_existing=True,
    )
    _reload_symbol_library(kicad, LIBRARY_NICKNAME)
    return record

def place_block(schematic, record, x_mm: float, y_mm: float):
    from kipy.geometry import Vector2

    symbol_id = f"{record.manifest['library_nickname']}:{record.manifest['symbol_name']}"
    instance = schematic.place_symbol_from_library(
        symbol_id, Vector2.from_xy_mm(x_mm, y_mm)
    )
    schematic.save()
    return instance


def _is_empty_selection_error(error: BaseException) -> bool:
    """Recognize KiCad IPC's empty-selection responses across supported builds."""
    message = str(error).casefold()
    return (
        "selection" in message
        and any(token in message for token in ("empty", "no item", "nothing", "none"))
    ) or any(
        token in message
        for token in (
            "no items selected",
            "no items are selected",
            "no item is selected",
            "nothing selected",
            "nothing is selected",
            "selection is not set",
        )
    )


def _safe_schematic_selection(schematic) -> list:
    """Return the current typed IPC selection, normalizing empty-selection errors."""
    if schematic is None or not hasattr(schematic, "get_selection"):
        return []
    try:
        return list(schematic.get_selection())
    except Exception as exc:
        if _is_empty_selection_error(exc):
            return []
        raise


def _selection_fragment(schematic) -> str:
    """Read the legacy serialized selection only when typed IPC cannot resolve it."""
    getter = getattr(schematic, "get_selection_as_string", None)
    if not callable(getter):
        return ""
    try:
        return str(getter() or "").strip()
    except Exception as exc:
        if _is_empty_selection_error(exc):
            return ""
        raise RuntimeError(
            "KiCad could not read the selected hierarchical sheet. "
            "Clear the selection and try again, or select exactly one sheet symbol."
        ) from exc


def _selected_symbols(schematic) -> list:
    selection = _safe_schematic_selection(schematic)
    symbols = [
        item for item in selection
        if hasattr(item, "definition") and hasattr(item.definition, "id")
    ]
    if not symbols:
        raise ValueError("Select one or more block symbol instances in the schematic.")
    return symbols


def _selected_symbol(schematic):
    symbols = _selected_symbols(schematic)
    if len(symbols) != 1:
        raise ValueError("Select exactly one block symbol instance in the schematic.")
    return symbols[0]


def _symbol_identifier(instance) -> tuple[str, str]:
    identifier = instance.definition.id
    return identifier.library, identifier.name


def _library_roots_for(value=None) -> list[Path]:
    if value is None:
        values = configured_library_roots()
    elif isinstance(value, (str, Path)):
        values = [Path(value)]
    else:
        values = [Path(item) for item in value]
    roots = []
    seen = set()
    for item in values:
        path = Path(item).expanduser().resolve()
        key = str(path)
        if key not in seen:
            roots.append(path)
            seen.add(key)
    return roots


def _list_library_records(library_paths=None) -> list:
    records = []
    for library_path in _library_roots_for(library_paths):
        records.extend(list_blocks(library_path))
    return sorted(
        records,
        key=lambda item: (item.name.casefold(), item.path.parent.name.casefold(), str(item.path)),
    )


def _library_record_for_instance(library_root: Path | list[Path], instance):
    library_nickname, symbol_name = _symbol_identifier(instance)
    matches = [
        record
        for record in _list_library_records(library_root)
        if record.manifest.get("library_nickname") == library_nickname
        and record.manifest.get("symbol_name") == symbol_name
    ]
    if not matches:
        raise FileNotFoundError(
            f"The selected symbol {library_nickname}:{symbol_name} was not found "
            "in any registered functional-block library."
        )
    identities = {
        (record.manifest.get("block_id"), record.manifest.get("revision"))
        for record in matches
    }
    if len(identities) > 1:
        locations = "\n".join(str(record.path) for record in matches)
        raise RuntimeError(
            f"The selected facade {library_nickname}:{symbol_name} is ambiguous because "
            "registered libraries contain different revisions of the same block ID:\n"
            f"{locations}\nRemove the stale or conflicting library registration."
        )
    return matches[0]


def make_selected_local(schematic, kicad, project_dir: Path, library_root: Path) -> Path:
    """Create an independent implementation copy bound to this symbol KIID."""
    instance = _selected_symbol(schematic)
    library_roots = _library_roots_for(library_root)
    source = _library_record_for_instance(library_roots, instance)
    source = synchronize_block_dependencies(
        source, library_roots, find_kicad_cli()
    )["block"]
    local = make_local_copy(source, project_dir)
    link_instance_to_local_copy(project_dir, str(instance.id), local)
    return local.path

def _parameter_values_for_instance(instance, block) -> dict[str, str]:
    """Read editable KB_PARAM_* user fields from one placed facade symbol."""
    parameters = block.manifest.get("parameters", [])
    overrides = {}
    for field in getattr(instance, "user_fields", ()):
        field_name = str(getattr(field, "name", ""))
        if not field_name.startswith(PARAMETER_FIELD_PREFIX):
            continue
        parameter_name = field_name[len(PARAMETER_FIELD_PREFIX):]
        field_text = getattr(field, "text", None)
        overrides[parameter_name] = getattr(field_text, "value", "")
    return resolve_parameter_values(parameters, overrides)


def _update_schematic_item_checked(schematic, instance) -> None:
    """Update one schematic item and inspect KiCad's per-item IPC status.

    The current kipy update_items() wrapper unconditionally unwraps the
    optional response Any. KiCad can return an ItemUpdateResult without that
    Any, so inspect the documented status fields directly instead.
    """
    from kipy.proto.common.commands import editor_commands_pb2
    from kipy.util import pack_any

    command = editor_commands_pb2.UpdateItems()
    command.header.document.CopyFrom(schematic.document)
    command.items.append(pack_any(instance.proto))
    response = schematic.client.send(
        command, editor_commands_pb2.UpdateItemsResponse
    )

    if int(response.status) != 1:
        raise RuntimeError(
            "KiCad rejected the block instance update request "
            f"(request status {int(response.status)})."
        )
    if len(response.updated_items) != 1:
        raise RuntimeError(
            "KiCad returned an unexpected number of item update results: "
            f"{len(response.updated_items)}."
        )

    item_result = response.updated_items[0]
    status = item_result.status
    if int(status.code) != 1:
        detail = str(getattr(status, "error_message", "") or "").strip()
        message = (
            f"KiCad rejected the block instance update (item status {int(status.code)})."
        )
        if detail:
            message += f" {detail}"
        raise RuntimeError(message)


def _write_instance_configuration(schematic, instance, values: dict[str, str]) -> None:
    """Write validated facade fields through KiCad IPC as one undoable edit."""
    fields = {
        str(getattr(field, "name", "")): field
        for field in getattr(instance, "user_fields", ())
    }
    missing = sorted(set(values) - set(fields))
    if missing:
        raise RuntimeError(
            f"Block instance {getattr(instance, 'reference', '<unknown>')} is missing "
            f"configuration field(s): {', '.join(missing)}. Reload the facade from "
            "the block library and replace the instance."
        )

    original_values = {
        name: str(getattr(getattr(fields[name], "text", None), "value", "") or "")
        for name in values
    }
    commit = schematic.begin_commit()
    try:
        for name, value in values.items():
            fields[name].text.value = str(value)
        _update_schematic_item_checked(schematic, instance)
        schematic.push_commit(commit, "Configure KiCad block instance")
        schematic.save()
    except Exception:
        with contextlib.suppress(Exception):
            schematic.drop_commit(commit)
        for name, value in original_values.items():
            with contextlib.suppress(Exception):
                fields[name].text.value = value
        raise


def _show_selected_block_configuration(
    schematic, project_dir: Path, library_root: Path, parent
) -> None:
    """Edit instance-local profile, variant, parameters and PCB layout."""
    _load_tkinter()
    instance = _selected_symbol(schematic)
    block = _block_record_for_instance(library_root, project_dir, instance)
    parameters = block.manifest.get("parameters", [])
    layouts = _layout_choices(block)
    variants = block.manifest.get("variants", [])
    profiles = block.manifest.get("profiles", [])
    current_values = _parameter_values_for_instance(instance, block)
    fields = {
        str(getattr(field, "name", "")): field
        for field in getattr(instance, "user_fields", ())
    }

    required = [parameter_field_name(item["name"]) for item in parameters]
    if variants:
        required.append(VARIANT_FIELD_NAME)
    if layouts:
        required.append(LAYOUT_FIELD_NAME)
    if profiles:
        required.append(PROFILE_FIELD_NAME)
    missing = sorted(set(required) - set(fields))
    if missing:
        raise RuntimeError(
            f"Block instance {instance.reference} is missing field(s): "
            + ", ".join(missing)
            + ". Reload the facade from the block library and replace the instance."
        )

    dialog = tk.Toplevel(parent)
    dialog.title(f"Configure {instance.reference} — {block.name}")
    dialog.transient(parent)

    outer = ttk.Frame(dialog, padding=18)
    outer.pack(fill="both", expand=True)
    ttk.Label(
        outer,
        text=f"{instance.reference}  ·  {block.name}",
    ).pack(anchor="w")
    content = ttk.Frame(outer)
    content.pack(fill="both", expand=True)

    profile_var = None
    profile_labels: dict[str, dict] = {}
    if profiles:
        section = ttk.LabelFrame(content, text="Configuration profile", padding=10)
        section.pack(fill="x", pady=(0, 10))
        profile_labels = {
            f"{item['name']}  [{item['id']}]": item
            for item in profiles
        }
        profile_labels["Custom (manual settings)"] = None
        current_profile_id = _field_value(instance, PROFILE_FIELD_NAME)
        current_profile_label = next(
            (
                label for label, item in profile_labels.items()
                if item is not None and str(item["id"]).casefold() == current_profile_id.casefold()
            ),
            "Custom (manual settings)",
        )
        profile_var = tk.StringVar(value=current_profile_label)
        profile_combo = ttk.Combobox(
            section,
            textvariable=profile_var,
            values=list(profile_labels),
            state="readonly",
        )
        profile_combo.pack(fill="x")
        _add_tooltip(profile_combo, "A profile fills the linked variant, parameters and PCB layout.")

    variant_var = None
    variant_combo = None
    if variants:
        section = ttk.LabelFrame(content, text="Implementation variant", padding=10)
        section.pack(fill="x", pady=(0, 10))
        variant_var = tk.StringVar(value=_field_value(instance, VARIANT_FIELD_NAME))
        variant_combo = ttk.Combobox(
            section,
            textvariable=variant_var,
            values=[str(item["value"]) for item in variants],
            state="normal",
        )
        variant_combo.pack(fill="x")
        _add_tooltip(variant_combo, "Choose a declared variant or enter a KiCad text-variable reference.")

    parameter_vars = {}
    if parameters:
        section = ttk.LabelFrame(content, text="Parameters", padding=10)
        section.pack(fill="both", expand=True, pady=(0, 10))
        section.columnconfigure(1, weight=1)
        for row, parameter in enumerate(parameters):
            name = parameter["name"]
            ttk.Label(
                section,
                text=f"{name}  ({parameter['type']})",
            ).grid(row=row * 2, column=0, sticky="w", padx=(0, 12), pady=(5, 0))
            variable = tk.StringVar(value=current_values[name])
            parameter_vars[name] = variable
            if parameter["type"] == "boolean":
                widget = ttk.Combobox(
                    section,
                    textvariable=variable,
                    values=("true", "false"),
                    state="readonly",
                )
            else:
                widget = ttk.Entry(section, textvariable=variable)
            widget.grid(row=row * 2, column=1, sticky="ew", pady=(5, 0))
            description = parameter.get("description") or f"Default: {parameter['default']}"
            _add_tooltip(widget, description)

    layout_var = None
    layout_ids = {}
    if layouts:
        section = ttk.LabelFrame(content, text="PCB layout", padding=10)
        section.pack(fill="x", pady=(0, 10))
        layout_ids = {
            f"{item.get('name', item.get('id'))}  [{item.get('id')}]": str(item.get("id") or "")
            for item in layouts
        }
        current_layout = _field_value(instance, LAYOUT_FIELD_NAME)
        selected_variant_value = variant_var.get() if variant_var is not None else ""
        linked_variant = next(
            (
                item for item in variants
                if str(item.get("value", "")).casefold()
                == selected_variant_value.casefold()
            ),
            None,
        )
        linked_layout_id = str(
            linked_variant.get("layout_id", "") if linked_variant else ""
        )
        if linked_layout_id and (
            not current_layout
            or current_layout.casefold()
            == str(block.manifest.get("default_layout") or "").casefold()
        ):
            current_layout = linked_layout_id
        current_label = next(
            (
                label for label, layout_id in layout_ids.items()
                if layout_id.casefold() == current_layout.casefold()
            ),
            None,
        )
        if current_layout and current_label is None:
            raise ValueError(
                f"Block {block.name!r} instance {instance.reference} selects unknown "
                f"layout {current_layout!r}. Refresh the block or select a valid layout."
            )
        layout_var = tk.StringVar(value=current_label or next(iter(layout_ids)))
        layout_combo = ttk.Combobox(
            section,
            textvariable=layout_var,
            values=list(layout_ids),
            state="readonly",
        )
        layout_combo.pack(fill="x")
        _add_tooltip(layout_combo, "PCB layout used when applying this block to a board.")

        def update_layout_for_variant(_event=None):
            if variant_var is None:
                return
            selected = next(
                (
                    item for item in variants
                    if str(item.get("value", "")).casefold()
                    == variant_var.get().strip().casefold()
                ),
                None,
            )
            linked_id = str(selected.get("layout_id", "") if selected else "")
            linked_label = next(
                (
                    label for label, layout_id in layout_ids.items()
                    if layout_id.casefold() == linked_id.casefold()
                ),
                None,
            )
            if linked_label is not None:
                layout_var.set(linked_label)

        if variant_combo is not None:
            variant_combo.bind("<<ComboboxSelected>>", update_layout_for_variant)
            variant_combo.bind("<KeyRelease>", update_layout_for_variant)

    profile_application_active = [False]

    def apply_profile(_event=None) -> None:
        if profile_var is None:
            return
        profile = profile_labels.get(profile_var.get())
        if profile is None:
            return
        resolved = resolve_profile_configuration(block.manifest, profile["id"])
        profile_application_active[0] = True
        try:
            if variant_var is not None:
                variant_var.set(resolved["variant"])
            for name, value in resolved["parameters"].items():
                if name in parameter_vars:
                    parameter_vars[name].set(value)
            if layout_var is not None:
                matching_label = next(
                    (
                        label for label, layout_id in layout_ids.items()
                        if layout_id.casefold() == resolved["layout"].casefold()
                    ),
                    None,
                )
                if matching_label is None:
                    raise ValueError(
                        f"Profile {profile['name']!r} references a PCB layout that is not available."
                    )
                layout_var.set(matching_label)
        finally:
            profile_application_active[0] = False

    def mark_profile_custom(*_args) -> None:
        if profile_var is None or profile_application_active[0]:
            return
        if profile_var.get() != "Custom (manual settings)":
            profile_var.set("Custom (manual settings)")

    if profile_var is not None:
        profile_combo.bind("<<ComboboxSelected>>", apply_profile)
        for linked_variable in (
            ([variant_var] if variant_var is not None else [])
            + list(parameter_vars.values())
            + ([layout_var] if layout_var is not None else [])
        ):
            linked_variable.trace_add("write", mark_profile_custom)

    footer = ttk.Frame(outer)
    footer.pack(fill="x", pady=(14, 0))

    def save_configuration() -> None:
        try:
            updates: dict[str, str] = {}
            resolved_variant = ""
            if variant_var is not None:
                raw_variant = variant_var.get().strip()
                resolved_variant = _resolve_variant_text(
                    raw_variant,
                    schematic.get_project(),
                    _sheet_variables_for_symbol(schematic, instance),
                )
                resolve_variant_implementation(block.manifest, resolved_variant)
                updates[VARIANT_FIELD_NAME] = raw_variant

            normalized_parameters = current_values
            if parameter_vars:
                normalized_parameters = resolve_parameter_values(
                    parameters,
                    {name: variable.get() for name, variable in parameter_vars.items()},
                )
                updates.update({
                    parameter_field_name(name): value
                    for name, value in normalized_parameters.items()
                })

            selected_layout_id = ""
            if layout_var is not None:
                selected_layout = layout_var.get()
                if selected_layout not in layout_ids:
                    raise ValueError("Select one of the declared PCB layout alternatives.")
                selected_layout_id = layout_ids[selected_layout]
                updates[LAYOUT_FIELD_NAME] = selected_layout_id

            if profile_var is not None:
                selected_profile = profile_labels.get(profile_var.get())
                selected_profile_id = ""
                if selected_profile is not None:
                    expected = resolve_profile_configuration(
                        block.manifest, selected_profile["id"]
                    )
                    current_matches = (
                        (variant_var is None or resolved_variant.casefold() == expected["variant"].casefold())
                        and normalized_parameters == expected["parameters"]
                        and (layout_var is None or selected_layout_id.casefold() == expected["layout"].casefold())
                    )
                    if current_matches:
                        selected_profile_id = str(selected_profile["id"])
                updates[PROFILE_FIELD_NAME] = selected_profile_id

            _write_instance_configuration(schematic, instance, updates)
        except Exception as exc:
            LOG.exception("Could not configure block instance %s", instance.reference)
            messagebox.showerror("Invalid Block Configuration", str(exc), parent=dialog)
            return

        messagebox.showinfo(
            "Block Instance Updated",
            f"Saved configuration for {instance.reference}.\n\n"
            "The schematic was updated through KiCad IPC as one undoable change. "
            "The shared library block was not modified.",
            parent=dialog,
        )
        dialog.destroy()

    _action_button(footer, "Cancel", dialog.destroy, "cancel", "Discard instance edits").pack(side="right", padx=(8, 0))
    _action_button(footer, "Save instance", save_configuration, "save", "Save configuration on the selected instance").pack(side="right")
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    _set_window_geometry(dialog, 700, 720, 580, 500)
    dialog.grab_set()
    dialog.wait_window()


def sync_selected_local(schematic, kicad, project_dir: Path) -> dict:
    """Synchronize the local implementation linked to the selected symbol KIID."""
    instance = _selected_symbol(schematic)
    local_record = local_copy_for_instance(project_dir, str(instance.id))
    if local_record is None:
        raise ValueError(
            "The selected block instance does not have a project-local copy. "
            "Use the Local action first."
        )
    library_roots = _library_roots_for()
    local_record = synchronize_block_dependencies(
        local_record, library_roots, find_kicad_cli()
    )["block"]
    result = publish_local_copy(local_record)
    _reload_symbol_library(kicad, LIBRARY_NICKNAME)
    return result

def _pick_block_for_placement(records, parent):
    """Choose, inspect and configure a functional block before native placement."""
    if not records:
        raise ValueError("No blocks were found in the registered functional-block libraries.")

    dialog = tk.Toplevel(parent)
    dialog.title("Place Functional Block")
    dialog.transient(parent)

    outer = ttk.Frame(dialog, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="Search functional blocks").pack(anchor="w")
    search = tk.StringVar()
    search_entry = ttk.Entry(outer, textvariable=search)
    search_entry.pack(fill="x", pady=(4, 8))

    panes = ttk.Panedwindow(outer, orient="horizontal")
    panes.pack(fill="both", expand=True)
    left = ttk.Frame(panes, padding=(0, 0, 8, 0))
    right = ttk.Frame(panes, padding=(8, 0, 0, 0))
    panes.add(left, weight=2)
    panes.add(right, weight=3)
    left.rowconfigure(0, weight=1)
    left.columnconfigure(0, weight=1)

    tree = ttk.Treeview(left, columns=("block", "library"), show="headings", selectmode="browse")
    tree.heading("block", text="Functional block")
    tree.heading("library", text="Library")
    tree.column("block", width=220, minwidth=160, stretch=True)
    tree.column("library", width=130, minwidth=100, stretch=False)
    scroll = ttk.Scrollbar(left, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
    tree.configure(yscrollcommand=scroll.set)
    palette = getattr(parent, "_kicad_blocks_palette", {})
    tree.tag_configure("even", background=palette.get("surface", "#f4f5f6"))
    tree.tag_configure("odd", background=palette.get("surface_alt", "#e8eaed"))
    tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    _bind_mousewheel(tree, lambda amount: tree.yview_scroll(amount, "units"))

    name = ttk.Label(right, text="Select a functional block")
    name.pack(anchor="w")
    description = ttk.Label(right, text="", wraplength=620, justify="left")
    description.pack(fill="x", anchor="w", pady=(4, 10))

    config = ttk.Frame(right)
    config.pack(fill="x", pady=(0, 8))
    config.columnconfigure(1, weight=1)
    ttk.Label(config, text="Implementation variant").grid(row=0, column=0, sticky="w", padx=(0, 10))
    variant_value = tk.StringVar()
    variant_combo = ttk.Combobox(config, textvariable=variant_value, state="readonly", width=32)
    variant_combo.grid(row=0, column=1, sticky="ew", pady=2)
    ttk.Label(config, text="Configuration profile").grid(row=1, column=0, sticky="w", padx=(0, 10))
    profile_value = tk.StringVar()
    profile_combo = ttk.Combobox(config, textvariable=profile_value, state="readonly", width=32)
    profile_combo.grid(row=1, column=1, sticky="ew", pady=2)

    preview_box = ttk.LabelFrame(right, text="Schematic Facade — rendered by KiCad", padding=6)
    preview_box.pack(fill="both", expand=True)
    preview = tk.Canvas(preview_box, height=310, highlightthickness=0, borderwidth=0)
    preview.pack(fill="both", expand=True)
    _bind_mousewheel(preview, lambda amount: preview.yview_scroll(amount, "units"))

    metadata = ttk.Label(right, text="", wraplength=620, justify="left")
    metadata.pack(fill="x", anchor="w", pady=(8, 0))

    records_by_id = {}
    visible_records = []
    selected_record = [None]
    chosen = [None]
    updating_config = [False]
    preview_size = [None]

    def preview_resized(event) -> None:
        # The chooser builds its widgets before the window is mapped. The first
        # preview render can therefore see a 1 px canvas and be permanently
        # clipped until the user changes the search or selection. Re-render only
        # when Tk reports a real canvas-size change, after layout has settled.
        size = (event.width, event.height)
        if size == preview_size[0]:
            return
        preview_size[0] = size
        record = selected_record[0]
        if record is not None:
            _draw_facade_preview(preview, record)

    preview.bind("<Configure>", preview_resized)

    def update_metadata(record):
        if record is None:
            name.configure(text="No matching functional blocks")
            description.configure(text="")
            metadata.configure(text="")
            preview.delete("all")
            variant_combo.configure(values=(), state="disabled")
            profile_combo.configure(values=(), state="disabled")
            return
        manifest = record.manifest
        name.configure(text=record.name)
        description.configure(text=str(manifest.get("description", "") or "No description provided."))
        variants = [str(item["value"]) for item in manifest.get("variants", [])]
        profiles = manifest.get("profiles", [])
        variant_combo.configure(values=variants or ("(single implementation)",), state="readonly" if variants else "disabled")
        profile_names = ["Custom"] + [
            str(item.get("name", item.get("id", ""))) for item in profiles
        ]
        profile_combo.configure(values=profile_names, state="readonly" if profiles else "disabled")
        variant_value.set(variants[0] if variants else "(single implementation)")
        profile_value.set("Custom" if profiles else "")
        pin_count = len(manifest.get("pins", []))
        parameter_count = len(manifest.get("parameters", []))
        layout_count = len(_layout_choices(record))
        metadata.configure(
            text=(
                f"Interface: {pin_count} pins    Parameters: {parameter_count}    "
                f"PCB layouts: {layout_count}    Version: {manifest.get('version', 1)}"
            )
        )
        _draw_facade_preview(preview, record)

    def render_results(*_args):
        nonlocal visible_records
        visible_records = _filter_library_records(records, search.get())
        tree.delete(*tree.get_children())
        records_by_id.clear()
        for index, record in enumerate(visible_records):
            iid = f"{record.manifest['block_id']}:{index}"
            records_by_id[iid] = record
            tree.insert(
                "", "end", iid=iid,
                values=(record.name, record.path.parent.name),
                tags=("even" if index % 2 == 0 else "odd",),
            )
        preferred = _preferred_library_selection(visible_records, selected_record[0])
        if preferred is None:
            selected_record[0] = None
            update_metadata(None)
            return
        iid = next(key for key, value in records_by_id.items() if value is preferred)
        tree.selection_set(iid)
        tree.focus(iid)
        selected_record[0] = preferred
        update_metadata(preferred)

    def selection_changed(_event=None):
        selection = tree.selection()
        record = records_by_id.get(selection[0]) if selection else None
        selected_record[0] = record
        update_metadata(record)

    def variant_changed(_event=None):
        if updating_config[0]:
            return
        if selected_record[0] is not None and profile_value.get() != "Custom":
            updating_config[0] = True
            profile_value.set("Custom" if selected_record[0].manifest.get("profiles") else "")
            updating_config[0] = False

    def profile_changed(_event=None):
        record = selected_record[0]
        if record is None or profile_value.get() in ("", "Custom"):
            return
        profile = next(
            (item for item in record.manifest.get("profiles", [])
             if str(item.get("name", item.get("id", ""))) == profile_value.get()),
            None,
        )
        if profile is not None:
            variant_value.set(str(profile.get("variant") or "(single implementation)"))

    def choose(_event=None):
        record = selected_record[0]
        if record is None:
            return "break"
        manifest = record.manifest
        profile = None
        if profile_value.get() not in ("", "Custom"):
            profile = next(
                (item for item in manifest.get("profiles", [])
                 if str(item.get("name", item.get("id", ""))) == profile_value.get()),
                None,
            )
        if profile is not None:
            resolved = resolve_profile_configuration(manifest, str(profile["id"]))
            selected_variant = resolved["variant"]
            values = dict(resolved["parameters"])
            layout = resolved["layout"]
            profile_id = str(profile["id"])
        else:
            selected_variant = "" if variant_value.get() == "(single implementation)" else variant_value.get()
            values = resolve_parameter_values(manifest.get("parameters", []))
            layout = str(manifest.get("default_layout") or "")
            profile_id = ""
        chosen[0] = {
            "record": record,
            "variant": selected_variant,
            "profile": profile_id,
            "parameters": values,
            "layout": layout,
        }
        dialog.destroy()
        return "break"

    search.trace_add("write", render_results)
    tree.bind("<<TreeviewSelect>>", selection_changed)
    tree.bind("<Double-1>", choose)
    search_entry.bind("<Return>", lambda _event: (tree.focus_set(), "break"))
    dialog.bind("<Return>", choose)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    variant_combo.bind("<<ComboboxSelected>>", variant_changed)
    profile_combo.bind("<<ComboboxSelected>>", profile_changed)

    buttons = ttk.Frame(outer)
    buttons.pack(fill="x", pady=(10, 0))
    _action_button(buttons, "Cancel", dialog.destroy, "cancel", "Cancel and close this dialog").pack(side="right")
    _action_button(buttons, "Place Block", choose, "place", "Start native symbol placement with this block").pack(side="right", padx=(0, 8))
    render_results()
    _set_window_geometry(dialog, 1200, 800, 980, 640)
    dialog.grab_set()
    search_entry.focus_set()
    dialog.wait_window()
    return chosen[0]


def _pick_record(records, parent, title: str):
    """Show a searchable modal picker for manager actions that need a library block."""
    if not records:
        raise ValueError("No blocks were found in the configured library.")
    if len(records) == 1:
        return records[0]

    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)

    outer = ttk.Frame(dialog, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="Search the block library").pack(anchor="w")
    search = tk.StringVar()
    search_entry = ttk.Entry(outer, textvariable=search)
    search_entry.pack(fill="x", pady=(4, 3))
    results_frame = ttk.Frame(outer)
    results_frame.pack(fill="both", expand=True)
    results_frame.columnconfigure(0, weight=1)
    results_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        results_frame,
        columns=("block", "library", "pins", "variants", "layouts"),
        show="headings",
        selectmode="browse",
    )
    tree.heading("block", text="Block")
    tree.heading("library", text="Library")
    tree.heading("pins", text="Pins")
    tree.heading("variants", text="Variants")
    tree.heading("layouts", text="Layouts")
    tree.column("block", width=260, minwidth=180, stretch=True)
    tree.column("library", width=135, minwidth=100, stretch=False)
    tree.column("pins", width=55, minwidth=45, stretch=False, anchor="center")
    tree.column("variants", width=75, minwidth=60, stretch=False, anchor="center")
    tree.column("layouts", width=65, minwidth=55, stretch=False, anchor="center")
    scrollbar = ttk.Scrollbar(results_frame, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
    tree.configure(yscrollcommand=scrollbar.set)
    palette = getattr(parent, "_kicad_blocks_palette", {})
    tree.tag_configure("even", background=palette.get("surface", "#f4f5f6"))
    tree.tag_configure("odd", background=palette.get("surface_alt", "#e8eaed"))
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    description = ttk.Label(
        outer,
        text="",
        wraplength=700,
        justify="left",
    )
    description.pack(fill="x", anchor="w", pady=(8, 0))
    palette = getattr(parent, "_kicad_blocks_palette", {})
    count_label = ttk.Label(
        outer,
        foreground=palette.get("muted", "#5f6871"),
        font=("TkDefaultFont", 8),
    )
    count_label.pack(anchor="w", pady=(3, 0))

    def short_description(record) -> str:
        value = " ".join(str(record.manifest.get("description", "") or "").split())
        return value[:177].rstrip() + "…" if len(value) > 180 else value

    records_by_id: dict[str, object] = {}
    selected_record = [None]
    chosen_record = [None]
    select_button = _action_button(
        outer, "Select block", lambda: None, "select",
        "Use the highlighted block", state="disabled",
    )
    select_button.pack(side="right", pady=(10, 0))
    _action_button(
        outer, "Cancel", dialog.destroy, "cancel", "Close without selecting a block",
    ).pack(side="right", padx=(0, 8), pady=(10, 0))

    def render_results(*_args):
        visible = _filter_library_records(records, search.get())
        preferred = _preferred_library_selection(visible, selected_record[0])
        children = tree.get_children()
        if children:
            tree.delete(*children)
        records_by_id.clear()
        for index, record in enumerate(visible):
            row_id = f"{record.manifest['block_id']}:{index}"
            records_by_id[row_id] = record
            tree.insert(
                "", "end", iid=row_id,
                values=(
                    record.name,
                    record.path.parent.name,
                    len(record.manifest.get("pins", [])),
                    len(record.manifest.get("variants", [])),
                    len(_layout_choices(record)),
                ),
                tags=("even" if index % 2 == 0 else "odd",),
            )
        count_label.configure(text=f"{len(visible)} of {len(records)} blocks")
        if preferred is None:
            selected_record[0] = None
            description.configure(text="No matching blocks.")
            select_button.configure(state="disabled")
            return
        preferred_id = next(
            key for key, value in records_by_id.items() if value is preferred
        )
        tree.selection_set(preferred_id)
        tree.focus(preferred_id)
        selected_record[0] = preferred
        description.configure(text=short_description(preferred))
        select_button.configure(state="normal")

    def selection_changed(_event=None):
        selection = tree.selection()
        record = records_by_id.get(selection[0]) if selection else None
        selected_record[0] = record
        if record is None:
            description.configure(text="")
            select_button.configure(state="disabled")
        else:
            description.configure(text=short_description(record))
            select_button.configure(state="normal")

    def choose_selected(_event=None):
        record = selected_record[0]
        if record is not None:
            chosen_record[0] = record
            dialog.destroy()
        return "break"

    def focus_results(_event=None):
        children = tree.get_children()
        if children:
            selection = tree.selection()
            target = selection[0] if selection else children[0]
            tree.selection_set(target)
            tree.focus(target)
            tree.focus_set()
        return "break"

    def choose_double_clicked(event):
        row_id = tree.identify_row(event.y)
        record = records_by_id.get(row_id) if row_id else None
        if record is None:
            return "break"
        tree.selection_set(row_id)
        tree.focus(row_id)
        selected_record[0] = record
        return choose_selected()

    _bind_mousewheel(tree, lambda amount: tree.yview_scroll(amount, "units"))
    tree.bind("<<TreeviewSelect>>", selection_changed)
    tree.bind("<Return>", choose_selected)
    tree.bind("<Double-1>", choose_double_clicked)
    search_entry.bind("<Down>", focus_results)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    select_button.configure(command=choose_selected)
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    search.trace_add("write", render_results)
    render_results()
    _set_window_geometry(dialog, 920, 640, 760, 480)
    dialog.grab_set()
    search_entry.focus_set()
    dialog.wait_window()
    return chosen_record[0]


def _layout_choices(block) -> list[dict]:
    """Return all stored PCB layouts while adapting legacy one-layout blocks."""
    layouts = list(block.manifest.get("layouts") or [])
    if layouts:
        return layouts
    legacy_file = block.manifest.get("layout_file")
    if legacy_file:
        return [{
            "id": None,
            "name": Path(str(legacy_file)).stem,
            "file": str(legacy_file),
            "project_file": block.manifest.get("layout_project_file", "layout.kicad_pro"),
        }]
    return []


def _choose_block_layout(
    block, parent, title: str = "Select PCB Layout", instance=None
) -> dict | None:
    """Resolve the instance's explicit or implementation-linked PCB layout."""
    layouts = _layout_choices(block)
    if not layouts:
        raise ValueError(f"Block {block.name!r} has no stored PCB layout.")

    selected_id = ""
    selected_variant = ""
    if instance is not None:
        for field in getattr(instance, "user_fields", ()):
            name = str(getattr(field, "name", ""))
            field_text = getattr(field, "text", None)
            value = str(getattr(field_text, "value", "") or "").strip()
            if name == LAYOUT_FIELD_NAME:
                selected_id = value
            elif name == VARIANT_FIELD_NAME:
                selected_variant = value
    if not selected_variant:
        selected_variant = str(block.manifest.get("default_variant") or "")
    if not selected_variant:
        selected_variant = str(block.manifest.get("default_variant") or "")
    variant = next(
        (
            item for item in block.manifest.get("variants", [])
            if str(item.get("value", "")).casefold() == selected_variant.casefold()
        ),
        None,
    )
    linked_layout = str(variant.get("layout_id", "") if variant else "").casefold()
    default_layout = str(block.manifest.get("default_layout") or "").casefold()
    if linked_layout and (
        not selected_id or selected_id.casefold() == default_layout
    ):
        selected_id = linked_layout

    if selected_id:
        for item in layouts:
            if selected_id.casefold() in {
                str(item.get("id", "")).casefold(),
                str(item.get("name", "")).casefold(),
            }:
                return item
        raise ValueError(
            f"Block {block.name!r} instance {getattr(instance, 'reference', '')} "
            f"selects unknown layout {selected_id!r}. Choose a valid layout in Configure PCB layout."
        )
    if len(layouts) == 1:
        return layouts[0]
    labels = [f"{item.get('name', item.get('id'))} [{item.get('id')}]" for item in layouts]
    selected = simpledialog.askstring(
        title,
        "Choose the PCB layout to apply:\n\n" + "\n".join(labels),
        initialvalue=labels[0],
        parent=parent,
    )
    if selected is None:
        return None
    for item, label in zip(layouts, labels):
        if selected.strip().casefold() in {
            label.casefold(),
            str(item.get("name", "")).casefold(),
            str(item.get("id", "")).casefold(),
        }:
            return item
    raise ValueError(f"No PCB layout {selected!r} is defined for block {block.name!r}.")


def _choose_library_root(parent) -> Path:
    default = default_library_root()
    default.mkdir(parents=True, exist_ok=True)
    selected = _pick_directory(
        parent,
        title="Select the shared KiCad block library folder",
        initial_path=default,
    )
    if not selected:
        return default
    return set_library_root(Path(selected))


def _parse_profile_parameter_overrides(text: str, parameters: list[dict]) -> dict[str, str]:
    """Parse typed NAME=VALUE overrides entered for one configuration profile."""
    declarations = {item["name"]: item for item in parameters}
    raw_values = {}
    for entry in str(text or "").split(","):
        token = entry.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError(f"Invalid profile parameter {token!r}; use NAME=VALUE.")
        name, value = (part.strip() for part in token.split("=", 1))
        if name not in declarations:
            raise ValueError(f"Unknown profile parameter {name!r}.")
        if name in raw_values:
            raise ValueError(f"Profile parameter {name!r} was entered more than once.")
        raw_values[name] = value
    normalized = resolve_parameter_values(parameters, raw_values)
    return {name: normalized[name] for name in raw_values}


def _collect_block_profiles(parent, parameters, variants, layouts) -> list[dict] | None:
    """Collect optional linked variant/parameter/layout presets during block creation."""
    if not (parameters or variants or layouts):
        return []
    if not messagebox.askyesno(
        "Configuration Profiles",
        "Add named presets that combine an implementation variant, parameter values "
        "and PCB layout? You can also configure each instance manually later.",
        parent=parent,
    ):
        return []

    profiles = []
    while True:
        name = simpledialog.askstring(
            "New Configuration Profile",
            "Profile name:",
            parent=parent,
        )
        if name is None:
            return None
        if not name.strip():
            messagebox.showwarning(
                "Configuration Profile",
                "Enter a profile name or choose No when asked to add profiles.",
                parent=parent,
            )
            return None

        description = simpledialog.askstring(
            "Profile Description",
            "Description (optional):",
            initialvalue="",
            parent=parent,
        )
        if description is None:
            return None

        variant = None
        if variants:
            choices = [str(item["value"]) for item in variants]
            variant = simpledialog.askstring(
                "Profile Implementation Variant",
                "Choose the exact implementation value:\n\n" + "\n".join(choices),
                initialvalue=choices[0],
                parent=parent,
            )
            if variant is None:
                return None
            matched_variant = next(
                (value for value in choices if value.casefold() == variant.strip().casefold()),
                None,
            )
            if matched_variant is None:
                raise ValueError(
                    f"Unknown profile variant {variant!r}. Choose one of: {', '.join(choices)}."
                )
            variant = matched_variant

        parameter_values = {}
        if parameters:
            parameter_text = simpledialog.askstring(
                "Profile Parameter Overrides",
                "Optional typed overrides, comma-separated as NAME=VALUE. "
                "Leave a parameter out to inherit the block default.\n\n"
                + ", ".join(f"{item['name']}={item['default']}" for item in parameters),
                initialvalue="",
                parent=parent,
            )
            if parameter_text is None:
                return None
            parameter_values = _parse_profile_parameter_overrides(
                parameter_text, parameters
            )

        layout_id = None
        if layouts:
            labels = {
                f"{item['name']}  [{item['id']}]": str(item["id"])
                for item in layouts
            }
            selected_layout = simpledialog.askstring(
                "Profile PCB Layout",
                "Choose a PCB layout:\n\n" + "\n".join(labels),
                initialvalue=next(iter(labels)),
                parent=parent,
            )
            if selected_layout is None:
                return None
            layout_aliases = {}
            for item in layouts:
                layout_aliases[item["name"].casefold()] = str(item["id"])
                layout_aliases[str(item["id"]).casefold()] = str(item["id"])
            for label, value in labels.items():
                layout_aliases[label.casefold()] = value
            layout_id = layout_aliases.get(selected_layout.strip().casefold())
            if layout_id is None:
                raise ValueError(
                    f"Unknown profile layout {selected_layout!r}. Choose one of: "
                    + ", ".join(labels)
                )

        profiles.append({
            "name": name.strip(),
            "description": description.strip(),
            "variant": variant,
            "parameters": parameter_values,
            "layout": layout_id,
        })
        if not messagebox.askyesno(
            "Configuration Profiles",
            f"Added profile {name.strip()!r}. Add another profile?",
            parent=parent,
        ):
            break
    return profiles


def _sheet_path_ids(value) -> tuple[str, ...]:
    """Normalize a KiCad SheetPath or a sequence of KIID values."""
    if value is None:
        return ()
    path = getattr(value, "path", value)
    try:
        items = list(path)
    except TypeError:
        return ()
    result = []
    for item in items:
        raw = getattr(item, "value", None) or getattr(item, "uuid", None) or item
        text_value = str(raw).strip().strip("{}")
        if text_value:
            result.append(text_value.casefold())
    return tuple(result)


def _walk_sheet_instances(instance):
    yield instance
    for child in getattr(instance, "children", ()) or ():
        yield from _walk_sheet_instances(child)


def _active_sheet_instance(schematic):
    """Resolve the currently displayed Eeschema sheet from its IPC sheet path."""
    hierarchy = schematic.get_hierarchy()
    root = getattr(hierarchy, "root", None)
    if root is None:
        raise ValueError("KiCad did not return a sheet hierarchy for the active schematic.")
    instances = list(_walk_sheet_instances(root))
    document = getattr(schematic, "document", None)
    document_path = _sheet_path_ids(getattr(document, "sheet_path", None))
    if document_path:
        for instance in instances:
            if _sheet_path_ids(getattr(instance, "path", None)) == document_path:
                return instance
        suffix_matches = [
            item for item in instances
            if _sheet_path_ids(getattr(item, "path", None))
            and _sheet_path_ids(getattr(item, "path", None))[-len(document_path):] == document_path
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]

    active_names = []
    for owner, attributes in (
        (schematic, ("name",)),
        (document, ("schematic_filename", "filename", "path")),
    ):
        for attribute in attributes:
            value = getattr(owner, attribute, None) if owner is not None else None
            if value:
                active_names.append(str(value).replace("\\", "/").casefold())
    for active_name in active_names:
        for instance in instances:
            filename = str(getattr(instance, "filename", "") or "").replace("\\", "/").casefold()
            name = str(getattr(instance, "name", "") or "").casefold()
            if filename and (
                active_name == filename
                or active_name.endswith("/" + filename)
                or Path(active_name).name == Path(filename).name
            ):
                return instance
            if name and Path(active_name).stem == name:
                return instance

    if len(instances) == 1:
        return root
    raise ValueError(
        "Could not identify the sheet currently displayed in Eeschema from the IPC sheet path. "
        "Select that sheet's symbol in its parent sheet and run Create Block from Sheet again."
    )


def _active_sheet_source(schematic, project_dir: Path) -> tuple[Path, str | None]:
    instance = _active_sheet_instance(schematic)
    filename = str(getattr(instance, "filename", "") or "").strip()
    if not filename:
        raise ValueError("KiCad did not provide a filename for the active hierarchical sheet.")
    source = Path(filename).expanduser()
    if not source.is_absolute():
        source = Path(project_dir) / source
    source = source.resolve()
    if not source.is_file() or source.suffix.casefold() != ".kicad_sch":
        raise FileNotFoundError(
            f"The currently displayed sheet does not resolve to a .kicad_sch file: {source}"
        )
    path_ids = _sheet_path_ids(getattr(instance, "path", None))
    sheet_uuid = path_ids[-1] if len(path_ids) > 1 else None
    return source, sheet_uuid


def _ask_create_block_details(parent, source_sheet: Path) -> tuple[str, str] | None:
    """Collect the block name and description in one creation dialog."""
    dialog = tk.Toplevel(parent)
    dialog.title("Create Functional Block")
    dialog.transient(parent)
    frame = ttk.Frame(dialog, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text="Create a reusable block from this sheet",
        font=("TkDefaultFont", 12, "bold"),
    ).pack(anchor="w")
    palette = getattr(parent, "_kicad_blocks_palette", {})
    ttk.Label(
        frame,
        text=f"Source sheet: {source_sheet.name}",
        foreground=palette.get("muted", ""),
    ).pack(anchor="w", pady=(4, 16))
    ttk.Label(frame, text="Block name").pack(anchor="w")
    name = tk.StringVar(
        value=source_sheet.stem.replace("_", " ").replace("-", " ").title()
    )
    name_entry = ttk.Entry(frame, textvariable=name)
    name_entry.pack(fill="x", pady=(4, 12))
    ttk.Label(frame, text="Description (optional)").pack(anchor="w")
    description = tk.Text(frame, height=5, wrap="word", relief="flat", borderwidth=1)
    description.pack(fill="both", expand=True, pady=(4, 14))
    description.configure(
        background=palette.get("field", "#ffffff"),
        foreground=palette.get("foreground", "#20252a"),
        insertbackground=palette.get("foreground", "#20252a"),
        highlightbackground=palette.get("border", "#b8c0c8"),
        highlightcolor=palette.get("accent", "#4c789f"),
    )
    result = [None]

    def accept():
        value = name.get().strip()
        if not value:
            messagebox.showerror("Create Functional Block", "Enter a block name.", parent=dialog)
            name_entry.focus_set()
            return
        result[0] = (value, description.get("1.0", "end-1c").strip())
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")
    _action_button(buttons, "Cancel", dialog.destroy, "cancel", "Cancel block creation").pack(side="right")
    _action_button(buttons, "Create Block", accept, "create", "Create the reusable block from this sheet").pack(side="right", padx=(0, 8))
    def cancel():
        dialog.destroy()

    dialog.bind("<Return>", lambda _event: accept())
    dialog.bind("<Escape>", lambda _event: cancel())
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    _set_window_geometry(dialog, 660, 390, 580, 340)
    dialog.grab_set()
    name_entry.focus_set()
    dialog.wait_window()
    return result[0]


def _selected_sheet_symbol_source(
    schematic, project_dir: Path, *, selection: list | None = None
) -> tuple[Path, str] | None:
    """Resolve one typed KiCad SheetSymbol using the active parent's directory."""
    if selection is None:
        selection = _safe_schematic_selection(schematic)
    if len(selection) != 1:
        return None
    item = selection[0]
    filename_field = getattr(item, "filename_field", None)
    if filename_field is None:
        return None
    filename_text = getattr(getattr(filename_field, "text", None), "value", "")
    filename = str(filename_text or "").strip()
    if not filename:
        raise ValueError("The selected hierarchical sheet has no Sheetfile property.")
    try:
        active_sheet, _active_uuid = _active_sheet_source(schematic, project_dir)
        base_directory = active_sheet.parent
    except (FileNotFoundError, ValueError, AttributeError):
        base_directory = Path(project_dir)
    source = Path(filename).expanduser()
    if not source.is_absolute():
        source = base_directory / source
    source = source.resolve()
    if source.suffix.casefold() != ".kicad_sch" or not source.is_file():
        raise FileNotFoundError(
            "The selected hierarchical sheet does not resolve to an existing "
            f".kicad_sch file relative to the active parent sheet: {source}"
        )
    raw_id = getattr(item, "id", None)
    source_uuid = (
        getattr(raw_id, "value", None)
        or getattr(raw_id, "uuid", None)
        or raw_id
    )
    if source_uuid is None:
        raise ValueError("The selected hierarchical sheet has no UUID.")
    source_uuid = str(source_uuid).strip().strip("{}")
    if not source_uuid:
        raise ValueError("The selected hierarchical sheet has no UUID.")
    return source, source_uuid


def _create_and_place(
    root, schematic, kicad, project_dir, parent,
    editor_window_id: str | None = None,
) -> None:
    """Create a block from one selected SheetSymbol or the currently open sheet."""
    selection = _safe_schematic_selection(schematic)
    if len(selection) > 1:
        raise ValueError(
            "Select exactly one hierarchical sheet symbol. Clear any other selected items and retry."
        )

    selected_sheet = (
        _selected_sheet_symbol_source(
            schematic, project_dir, selection=selection
        )
        if selection else None
    )
    if selected_sheet is not None:
        # Prefer the typed SheetSymbol returned by KiCad's IPC API. It avoids
        # depending on clipboard serialization and provides the instance UUID.
        source_sheet, source_sheet_uuid = selected_sheet
    else:
        fragment = _selection_fragment(schematic)
        if fragment:
            # Compatibility fallback for IPC versions/adapters which expose
            # the serialized selection but not a typed SheetSymbol object.
            try:
                active_sheet, _active_sheet_uuid = _active_sheet_source(
                    schematic, project_dir
                )
                sheet_base_directory = active_sheet.parent
            except (FileNotFoundError, ValueError, AttributeError):
                sheet_base_directory = Path(project_dir)
            try:
                source_sheet = selected_sheet_file(fragment, sheet_base_directory)
                source_sheet_uuid = selected_sheet_instance_uuid(fragment)
            except ValueError as exc:
                raise ValueError(
                    "Select exactly one hierarchical sheet symbol in the parent schematic, "
                    "or clear the selection to create from the currently open sheet. "
                    f"KiCad selection details: {exc}"
                ) from exc
        elif selection:
            raise ValueError(
                "The current selection is not a hierarchical sheet symbol. "
                "Select exactly one sheet symbol, or clear the selection to use the open sheet."
            )
        else:
            source_sheet, source_sheet_uuid = _active_sheet_source(
                schematic, project_dir
            )

    details = _ask_create_block_details(parent, source_sheet)
    if details is None:
        return
    name, description = details

    # Registration can fail after the block has been written (for example, if
    # the existing project's sym-lib-table is malformed). Snapshot the two
    # project-side registration files before creation so the operation can be
    # rolled back without leaving a block folder or dangling catalog entry.
    project_registration_files = (
        Path(project_dir) / ".kicad_blocks" / LIBRARY_CATALOG_NAME,
        Path(project_dir) / "sym-lib-table",
    )
    previous_registration_files = {
        path: path.read_bytes() if path.is_file() else None
        for path in project_registration_files
    }
    record = create_block(
        root,
        name,
        source_schematic=source_sheet,
        source_project_name=Path(project_dir).name,
        source_sheet_uuid=source_sheet_uuid,
        description=description,
    )
    try:
        library_roots = _library_roots_for(root)
        record = synchronize_block_dependencies(
            record, library_roots, find_kicad_cli()
        )["block"]
        record = _register_block(project_dir, record, kicad, library_roots)
    except Exception as registration_error:
        cleanup_errors = []
        block_path = Path(record.path).expanduser().resolve()
        library_path = Path(root).expanduser().resolve()
        if block_path.parent != library_path:
            cleanup_errors.append(
                RuntimeError(
                    f"Refusing to remove unexpected block path outside the selected library: {block_path}"
                )
            )
        else:
            try:
                shutil.rmtree(block_path)
            except OSError as cleanup_error:
                cleanup_errors.append(cleanup_error)
        for path, previous_content in previous_registration_files.items():
            try:
                if previous_content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, previous_content.decode("utf-8"))
            except (OSError, UnicodeError) as restore_error:
                cleanup_errors.append(restore_error)
        if cleanup_errors:
            details = "; ".join(str(error) for error in cleanup_errors)
            raise RuntimeError(
                "Project-library registration failed and rollback was incomplete: "
                f"{details}. Check the new block path and project library files manually."
            ) from registration_error
        raise
    pins_summary = "\\n".join(
        f"  • {pin['name']} — {pin['electrical_type']}"
        for pin in record.manifest.get("pins", [])
    )
    messagebox.showinfo(
        "Block Created",
        f"Created {name} from hierarchical sheet {source_sheet.name}.\\n\\n"
        f"Facade pins were generated from hierarchical labels and their shapes:\\n"
        f"{pins_summary}\\n\\n"
        f"Implementation: {record.implementation}\\n"
        f"Facade symbol: {record.symbol_library}\\n\\n"
        "The original project sheet is unchanged. Edit the facade in KiCad's "
        "normal Symbol Editor, then use Publish Library Block. When the "
        "implementation's hierarchical labels change, Publish synchronizes "
        "facade pin names and electrical types automatically."
        + (f"\\n\\nLibrary reload note: {_LAST_LIBRARY_RELOAD_WARNING}" if _LAST_LIBRARY_RELOAD_WARNING else ""),
        parent=parent,
    )



def _ensure_project_facade_catalog(
    project_dir: Path,
    record,
    kicad,
) -> Path:
    """Ensure the highlighted symbol exists in the already-registered KBlocks project catalog.

    Do not rebuild an existing catalog here: a user may have unsaved or
    unpublished edits to another facade in that same project library.
    """
    from .pcb_layout import parse_sexpr
    from .repository import _named_symbol_node, merge_symbol_catalog

    project_dir = Path(project_dir).expanduser().resolve()
    catalog = project_dir / ".kicad_blocks" / LIBRARY_CATALOG_NAME
    catalog.parent.mkdir(parents=True, exist_ok=True)
    symbol_name = str(record.manifest.get("symbol_name") or "")
    catalog_changed = False

    if not catalog.is_file():
        merge_symbol_catalog(catalog.parent, record.path / record.manifest["symbol_file"])
        catalog_changed = True
    else:
        tree = parse_sexpr(catalog.read_text(encoding="utf-8"))
        try:
            _named_symbol_node(tree, symbol_name, catalog)
        except ValueError:
            # Merge only the missing symbol. Never rebuild the whole project
            # catalog here, because that would discard edits to other symbols.
            merge_symbol_catalog(catalog.parent, record.path / record.manifest["symbol_file"])
            catalog_changed = True

    table_path = project_dir / "sym-lib-table"
    previous_table = table_path.read_bytes() if table_path.is_file() else None
    register_project_symbol_library(
        project_dir,
        LIBRARY_NICKNAME,
        catalog,
        description="KiCad Blocks — all registered functional-block libraries",
        replace_existing=True,
    )
    table_changed = (
        not table_path.is_file()
        or table_path.read_bytes() != previous_table
    )
    if (catalog_changed or table_changed) and not _reload_symbol_library(
        kicad, LIBRARY_NICKNAME
    ):
        raise RuntimeError(
            _LAST_LIBRARY_RELOAD_WARNING
            or "Eeschema could not reload the project KBlocks symbol catalog."
        )

    return catalog


def _edit_facade_symbol(
    record,
    kicad,
    editor_window_id: str | None = None,
    *,
    project_catalog: Path | None = None,
    close_browser=None,
) -> bool:
    """Validate the facade entry, then report the missing supported editor API.

    KiCad's documented IPC surface cannot open or select a specific symbol in
    Symbol Editor. KiCad.run_action is explicitly unstable, and the current
    Eeschema server has no handler for its RunAction protobuf. No desktop-specific
    input fallback or manual-only handoff is allowed.
    """
    del kicad, editor_window_id, close_browser
    symbol_name = str(record.manifest.get("symbol_name") or "").strip()
    catalog_path = (
        Path(project_catalog).expanduser().resolve()
        if project_catalog is not None
        else Path(record.symbol_library).expanduser().resolve()
    )
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The highlighted block has an invalid library symbol identifier.")
    if not catalog_path.is_file():
        raise FileNotFoundError(f"The project facade catalog is missing: {catalog_path}")

    from .pcb_layout import parse_sexpr
    from .repository import _named_symbol_node

    catalog = parse_sexpr(catalog_path.read_text(encoding="utf-8"))
    _named_symbol_node(catalog, symbol_name, catalog_path)
    lib_id = f"{LIBRARY_NICKNAME}:{symbol_name}"
    raise RuntimeError(
        "Edit Facade is not available through the supported KiCad IPC API yet. "
        f"The generated entry {lib_id} was validated, but KiCad exposes no supported "
        "IPC operation to open and select a library symbol in Symbol Editor. "
        "The unstable RunAction API is not used, and no manual-only or "
        "OS-specific input fallback is provided. This requires an upstream KiCad API."
    )


def _place_existing(root, schematic, kicad, project_dir, parent) -> None:
    """Open the searchable browser; placement is handed to KiCad's native cursor tool."""
    editor_window_id = getattr(parent, "_kicad_blocks_editor_window_id", None)
    browser = tk.Toplevel(parent)
    _attach_tk_window_to_editor(browser, editor_window_id)
    _show_library_browser(
        browser, schematic, kicad, project_dir,
        default_library_root(), editor_window_id,
    )



def _edit_block_implementation(root, schematic, kicad, project_dir, parent) -> None:
    """Compatibility helper for opening a library implementation from the manager."""
    selected = _safe_schematic_selection(schematic)
    if len(selected) == 1 and hasattr(selected[0], "definition"):
        record = _block_record_for_instance(root, project_dir, selected[0])
    else:
        record = _pick_record(list_blocks(root), parent, "Edit Block Implementation")
    if record is None:
        return None
    _register_implementation_catalog(record, _library_roots_for(root))
    process = _launch_block_asset(
        kicad,
        record.implementation,
        "implementation schematic",
        editor_window_id=getattr(parent, "_kicad_blocks_editor_window_id", None),
    )
    messagebox.showinfo(
        "Block Implementation",
        f"Opened {record.name} implementation in Eeschema.\n\n"
        "After editing a shared library block, run Publish. "
        "A project-local implementation remains independent until explicitly synchronized.",
        parent=parent,
    )
    return process




_SCHEMATIC_PAGE_SIZES_MM: dict[int, tuple[float, float]] = {
    # KiCad PageSize enum values; dimensions are default landscape sizes.
    1: (210.0, 148.0),       # A5
    2: (297.0, 210.0),       # A4
    3: (420.0, 297.0),       # A3
    4: (594.0, 420.0),       # A2
    5: (841.0, 594.0),       # A1
    6: (1189.0, 841.0),      # A0
    7: (279.4, 215.9),       # ANSI A
    8: (431.8, 279.4),       # ANSI B
    9: (558.8, 431.8),       # ANSI C
    10: (863.6, 558.8),      # ANSI D
    11: (1117.6, 863.6),     # ANSI E
    12: (812.8, 812.8),      # Gerber
    13: (279.4, 215.9),      # US Letter
    14: (355.6, 215.9),      # US Legal
    15: (431.8, 279.4),      # US Ledger
}
_SCHEMATIC_GRID_MM = 1.27


def _schematic_page_center_mm(schematic) -> tuple[float, float]:
    """Return the active sheet's page center, snapped to KiCad's 50 mil grid."""
    settings_getter = getattr(schematic, "get_page_settings", None)
    if not callable(settings_getter):
        raise TypeError("The active schematic does not expose page settings.")
    settings = settings_getter()
    page_size = int(getattr(settings, "page_size", 0) or 0)

    if page_size == 16:  # PS_USER
        custom = getattr(settings, "user_page_size", None)
        if custom is None:
            raise RuntimeError("The custom schematic page size is unavailable.")
        # KiCad IPC Vector2 coordinates are expressed in nanometres.
        width_mm = float(custom.x) / 1_000_000
        height_mm = float(custom.y) / 1_000_000
    else:
        try:
            width_mm, height_mm = _SCHEMATIC_PAGE_SIZES_MM[page_size]
        except KeyError as exc:
            raise RuntimeError(
                f"Unsupported KiCad schematic page-size enum: {page_size}."
            ) from exc
        if int(getattr(settings, "orientation", 0) or 0) == 2:  # PO_PORTRAIT
            width_mm, height_mm = height_mm, width_mm

    if (
        not math.isfinite(width_mm)
        or not math.isfinite(height_mm)
        or width_mm <= 0
        or height_mm <= 0
    ):
        raise RuntimeError("KiCad returned invalid schematic page dimensions.")

    def snap(value: float) -> float:
        return round(round(value / _SCHEMATIC_GRID_MM) * _SCHEMATIC_GRID_MM, 2)

    return snap(width_mm / 2), snap(height_mm / 2)


def _focus_schematic_items_in_editor(schematic, items):
    """Navigate to the owning sheet and center the supplied items in Eeschema."""
    if not items:
        return schematic
    sheet_path = getattr(items[0], "path", None)
    if sheet_path is None or not callable(getattr(schematic, "for_sheet", None)):
        raise RuntimeError("KiCad did not expose the new symbol's owning sheet path.")
    target_schematic = schematic.for_sheet(sheet_path)
    path_proto = getattr(sheet_path, "proto", None)
    if path_proto is None:
        raise RuntimeError(
            "The KiCad Python binding did not expose the sheet-path protobuf "
            "required by FocusOnItems."
        )

    from google.protobuf.empty_pb2 import Empty
    from kipy.proto.common.commands import editor_commands_pb2

    command = editor_commands_pb2.FocusOnItems()
    command.document.CopyFrom(target_schematic.document)
    command.document.sheet_path.CopyFrom(path_proto)
    for item in items:
        identifier = getattr(getattr(item, "id", None), "value", None)
        if not identifier:
            identifier = str(getattr(item, "id", "") or "")
        if not identifier:
            raise RuntimeError("KiCad did not return an instance ID for the placed symbol.")
        command.items.add().value = str(identifier)
    target_schematic.client.send(command, Empty)
    return target_schematic


def _start_native_symbol_placement(
    kicad,
    _editor_window_id: str | None,
    symbol_name: str,
):
    """Place a block at the actual sheet center, focus it, and select it.

    Page dimensions and orientation come from Eeschema's page settings. The
    coordinate is snapped to the schematic grid. FocusOnItems then brings the
    placed symbol into the viewport before it is selected.
    """
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The selected block has an invalid KiCad symbol name.")

    schematic = kicad.get_schematic()
    if schematic is None:
        raise RuntimeError("No active schematic was found in Eeschema.")

    from kipy.geometry import Vector2

    x_mm, y_mm = _schematic_page_center_mm(schematic)
    symbol_id = f"{LIBRARY_NICKNAME}:{symbol_name}"
    instance = schematic.place_symbol_from_library(
        symbol_id, Vector2.from_xy_mm(x_mm, y_mm)
    )
    target_schematic = _focus_schematic_items_in_editor(schematic, [instance])
    target_schematic.clear_selection()
    target_schematic.add_to_selection([instance])
    selected = _safe_schematic_selection(target_schematic)
    instance_id = str(getattr(getattr(instance, "id", None), "value", None) or instance.id)
    if not any(
        str(getattr(getattr(item, "id", None), "value", None) or item.id)
        == instance_id
        for item in selected
    ):
        raise RuntimeError("KiCad did not confirm selection of the newly placed block.")
    target_schematic.save()
    return instance


def _library_record_search_text(record) -> str:
    manifest = record.manifest
    parts = [
        str(manifest.get("name", "")),
        str(manifest.get("description", "")),
        str(manifest.get("slug", "")),
    ]
    parts.extend(
        f"{pin.get('name', '')} {pin.get('electrical_type', '')}"
        for pin in manifest.get("pins", [])
    )
    parts.extend(
        f"{parameter.get('name', '')} {parameter.get('type', '')}"
        for parameter in manifest.get("parameters", [])
    )
    parts.extend(
        str(variant.get("value", ""))
        for variant in manifest.get("variants", [])
    )
    parts.extend(str(layout.get("name", "")) for layout in _layout_choices(record))
    return " ".join(parts).casefold()


def _filter_library_records(records: list, query: str) -> list:
    """Filter blocks by name, description, pins, parameters, variants or layouts."""
    needle = str(query or "").strip().casefold()
    if not needle:
        return list(records)
    return [
        record for record in records
        if needle in _library_record_search_text(record)
    ]


def _preferred_library_selection(visible_records: list, selected_record):
    """Keep the current block selected while it remains visible after filtering."""
    if not visible_records:
        return None
    if selected_record is not None:
        selected_id = str(selected_record.manifest.get("block_id", ""))
        for record in visible_records:
            if str(record.manifest.get("block_id", "")) == selected_id:
                return record
    return visible_records[0]


def _library_record_for_tree_row(records_by_id: dict, row_id: str | None):
    """Return the block represented by a clicked result row, never a stale selection."""
    if not row_id:
        return None
    return records_by_id.get(str(row_id))


def _minimize_placement_windows(window) -> list:
    """Minimize the browser and its manager parent so Eeschema's canvas is visible."""
    minimized = []
    current = window
    while current is not None:
        state = None
        with contextlib.suppress(Exception):
            state = current.state()
        if state not in {"iconic", "withdrawn"}:
            try:
                current.iconify()
            except Exception:
                LOG.debug("Could not minimize a placement UI window", exc_info=True)
            else:
                minimized.append(current)
        current = getattr(current, "master", None)
    return minimized


def _restore_placement_windows(windows: list) -> None:
    """Restore windows minimized before a placement handoff failed."""
    for window in reversed(windows):
        with contextlib.suppress(Exception):
            window.deiconify()


def _clean_facade_preview_svg(svg_file: Path, record, output_dir: Path) -> Path:
    """Remove component-name and reference placeholder artwork from the SVG."""
    import xml.etree.ElementTree as ET

    root = ET.parse(svg_file).getroot()
    display_labels = {
        str(record.manifest.get("name", "")).strip(),
        str(record.manifest.get("symbol_name", "")).strip(),
    }
    reference_prefix = str(record.manifest.get("reference_prefix", "X") or "X").strip()
    if reference_prefix:
        display_labels.add(f"{reference_prefix}?")
    display_labels.discard("")

    def contained_labels(node) -> set[str]:
        labels = set()
        for child in node.iter():
            if child.tag.rsplit("}", 1)[-1] not in {"text", "desc"}:
                continue
            value = (child.text or "").strip()
            if value:
                labels.add(value)
        return labels

    # KiCad's SVG renderer groups text glyph paths with <text>/<desc> labels.
    # Remove the containing group instead of just its hidden text node.
    for parent_node in list(root.iter()):
        for child_node in list(parent_node):
            if contained_labels(child_node) & display_labels:
                parent_node.remove(child_node)

    # resvg requires a concrete intrinsic viewport. KiCad exports commonly
    # include a viewBox but may omit usable width/height attributes; CairoSVG
    # tolerated that ambiguity while resvg correctly rejects an undefined size.
    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox") or ""
    view_box_values = re.findall(
        r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", view_box
    )
    if len(view_box_values) == 4:
        try:
            view_width, view_height = float(view_box_values[2]), float(view_box_values[3])
        except ValueError:
            view_width = view_height = 0.0
        if view_width > 0 and view_height > 0:
            root.set("width", f"{view_width:g}px")
            root.set("height", f"{view_height:g}px")
    else:
        def valid_dimension(value: str | None) -> bool:
            if not value:
                return False
            match = re.fullmatch(
                r"\s*([+]?(?:\d+(?:\.\d*)?|\.\d+))(?:px|pt|pc|mm|cm|in)?\s*",
                value,
                flags=re.IGNORECASE,
            )
            return bool(match and float(match.group(1)) > 0)

        if not valid_dimension(root.attrib.get("width")) or not valid_dimension(
            root.attrib.get("height")
        ):
            root.set("width", "300px")
            root.set("height", "150px")

    cleaned_svg = output_dir / "facade-preview.svg"
    ET.ElementTree(root).write(
        cleaned_svg, encoding="utf-8", xml_declaration=True
    )
    return cleaned_svg


def _native_symbol_preview_photo(
    record,
    width: int = 680,
    max_height: int = 260,
    *,
    library_file: str | Path | None = None,
):
    """Render the exact catalog symbol used by the current preview/editor workflow."""
    symbol_name = str(record.manifest["symbol_name"])
    library_file = Path(
        library_file or record.symbol_library
    ).expanduser().resolve()
    if not library_file.is_file():
        raise FileNotFoundError(f"Facade symbol library is missing: {library_file}")

    cache_key = (
        str(library_file),
        library_file.stat().st_mtime_ns,
        symbol_name,
        int(width),
        int(max_height),
    )
    png_data = _NATIVE_PREVIEW_CACHE.get(cache_key)
    if png_data is None:
        cli = find_kicad_cli()
        with tempfile.TemporaryDirectory(prefix="kicad-block-preview-") as temporary:
            output_dir = Path(temporary)
            result = subprocess.run(
                [
                    cli, "sym", "export", "svg",
                    "--symbol", symbol_name,
                    "--output", str(output_dir),
                    str(library_file),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(
                    "KiCad could not render the facade symbol"
                    + (f": {detail}" if detail else ".")
                )
            svg_file = output_dir / f"{symbol_name}.svg"
            if not svg_file.is_file():
                candidates = list(output_dir.glob("*.svg"))
                if len(candidates) != 1:
                    raise RuntimeError(
                        "KiCad symbol export did not produce the expected SVG preview."
                    )
                svg_file = candidates[0]

            try:
                import resvg_py
            except ImportError as exc:
                raise RuntimeError(
                    "The native KiCad preview rasterizer is missing. Reinstall KiCad Blocks "
                    "so its resvg_py dependency is installed."
                ) from exc

            svg_file = _clean_facade_preview_svg(svg_file, record, output_dir)
            png_data = resvg_py.svg_to_bytes(
                svg_path=str(svg_file),
                width=int(width),
                height=int(max_height),
            )
            try:
                from io import BytesIO

                from PIL import Image

                image = Image.open(BytesIO(png_data)).convert("RGBA")
                bounds = image.getbbox()
                if bounds:
                    image = image.crop(bounds)
                scale = min(
                    1.0,
                    int(width) / max(1, image.width),
                    int(max_height) / max(1, image.height),
                )
                if scale < 1.0:
                    image = image.resize(
                        (
                            max(1, round(image.width * scale)),
                            max(1, round(image.height * scale)),
                        ),
                        Image.Resampling.LANCZOS,
                    )
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                png_data = buffer.getvalue()
            except (ImportError, OSError, ValueError):
                LOG.warning(
                    "Pillow is unavailable; facade preview could exceed its display area. "
                    "Install the declared Pillow dependency to enable automatic cropping."
                )
        _NATIVE_PREVIEW_CACHE[cache_key] = png_data

    # Tk images belong to a particular Tcl interpreter, so cache only PNG bytes.
    return tk.PhotoImage(data=base64.b64encode(png_data).decode("ascii"))

_NATIVE_PREVIEW_CACHE: dict[tuple, object] = {}


def _native_schematic_preview_pages(
    record,
    implementation: str = "",
    *,
    width: int = 900,
) -> list[tuple[str, bytes]]:
    """Render every sheet of the selected implementation with KiCad CLI."""
    implementation_file = resolve_variant_implementation(
        record.manifest, implementation
    )
    schematic_file = (Path(record.path) / implementation_file).resolve()
    if not schematic_file.is_file():
        raise FileNotFoundError(
            f"Implementation schematic is missing: {schematic_file}"
        )

    block_path = Path(record.path).resolve()
    schematic_assets = tuple(
        sorted(
            (
                path.relative_to(block_path).as_posix(),
                path.stat().st_mtime_ns,
                path.stat().st_size,
            )
            for path in block_path.rglob("*.kicad_sch")
            if path.is_file()
        )
    )
    cache_key = (str(schematic_file), schematic_assets, int(width))
    cached_pages = _SCHEMATIC_PREVIEW_CACHE.get(cache_key)
    if cached_pages is not None:
        return list(cached_pages)

    cli = find_kicad_cli()
    with tempfile.TemporaryDirectory(prefix="kicad-block-schematic-preview-") as temporary:
        output_dir = Path(temporary)
        result = subprocess.run(
            [
                cli,
                "sch",
                "export",
                "svg",
                "--no-background-color",
                "--output",
                str(output_dir),
                str(schematic_file),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                "KiCad could not render the implementation schematic"
                + (f": {detail}" if detail else ".")
            )

        svg_files = sorted(output_dir.glob("*.svg"), key=lambda path: path.name.casefold())
        root_svg = output_dir / f"{schematic_file.stem}.svg"
        if root_svg in svg_files:
            svg_files.remove(root_svg)
            svg_files.insert(0, root_svg)
        if not svg_files:
            raise RuntimeError(
                "KiCad schematic export did not produce any SVG preview pages."
            )

        try:
            import resvg_py
        except ImportError as exc:
            raise RuntimeError(
                "The native KiCad preview rasterizer is missing. Reinstall KiCad Blocks "
                "so its resvg_py dependency is installed."
            ) from exc

        pages: list[tuple[str, bytes]] = []
        for index, svg_file in enumerate(svg_files):
            if index == 0:
                label = "Root sheet"
            else:
                suffix = svg_file.stem
                prefix = f"{schematic_file.stem}-"
                if suffix.casefold().startswith(prefix.casefold()):
                    suffix = suffix[len(prefix):]
                suffix = re.sub(r"[-_]+", " ", suffix).strip()
                label = f"Sheet {index + 1}" + (f" · {suffix}" if suffix else "")
            png_data = resvg_py.svg_to_bytes(
                svg_path=str(svg_file),
                width=int(width),
                height=int(width),
            )
            pages.append((label, bytes(png_data)))

    # Keep the cache bounded: each entry contains rendered pages for one
    # implementation and size, not Tk objects tied to a particular window.
    if len(_SCHEMATIC_PREVIEW_CACHE) >= 24:
        _SCHEMATIC_PREVIEW_CACHE.pop(next(iter(_SCHEMATIC_PREVIEW_CACHE)))
    _SCHEMATIC_PREVIEW_CACHE[cache_key] = tuple(pages)
    return pages


_SCHEMATIC_PREVIEW_CACHE: dict[tuple, tuple[tuple[str, bytes], ...]] = {}


def _draw_schematic_preview(canvas, pages, page_label: str | None = None) -> None:
    """Display one KiCad-rendered schematic sheet in a scrollable canvas."""
    canvas.delete("all")
    selected = next(
        (item for item in pages if item[0] == page_label),
        pages[0] if pages else None,
    )
    if selected is None:
        return
    _label, png_data = selected
    photo = tk.PhotoImage(data=base64.b64encode(png_data).decode("ascii"))
    canvas._native_schematic_preview_photo = photo
    canvas.create_image(8, 8, image=photo, anchor="nw")
    canvas.configure(
        scrollregion=(0, 0, photo.width() + 16, photo.height() + 16)
    )
    canvas.xview_moveto(0)
    canvas.yview_moveto(0)


def _draw_facade_preview(
    canvas,
    record,
    *,
    library_file: str | Path | None = None,
) -> None:
    """Display a faithful KiCad-rendered facade preview; never approximate it."""
    canvas.delete("all")
    height = max(int(canvas.winfo_height()), 250)
    max_preview_height = max(160, min(280, height - 24))
    try:
        photo = _native_symbol_preview_photo(
            record,
            max_height=max_preview_height,
            library_file=library_file,
        )
    except Exception as exc:
        LOG.exception("Native facade preview failed for %s", record.name)
        style = ttk.Style(canvas)
        foreground = style.lookup("TLabel", "foreground") or canvas.cget("foreground")
        canvas.create_text(
            max(int(canvas.winfo_width()), 480) / 2,
            max(int(canvas.winfo_height()), 250) / 2,
            text=f"KiCad preview unavailable\\n{exc}",
            fill=foreground,
            justify="center",
            width=max(int(canvas.winfo_width()), 480) - 32,
        )
        canvas.configure(scrollregion=(0, 0, max(int(canvas.winfo_width()), 480), max(int(canvas.winfo_height()), 250)))
        return

    canvas._native_preview_photo = photo
    width = max(int(canvas.winfo_width()), 480)
    height = max(int(canvas.winfo_height()), 250)
    canvas.create_image(width / 2, height / 2, image=photo, anchor="center")
    canvas.configure(
        scrollregion=(0, 0, max(width, photo.width()), max(height, photo.height()))
    )


def _block_overview_text(record) -> str:
    """Summarize the block's interface and available circuit choices."""
    manifest = record.manifest
    pins = manifest.get("pins", [])
    parameters = manifest.get("parameters", [])
    variants = manifest.get("variants", [])
    layouts = _layout_choices(record)
    profiles = manifest.get("profiles", [])
    lines = [f"Connections · {len(pins)}"]
    if pins:
        lines.extend(
            f"  {pin.get('name', 'Unnamed')}  ·  {str(pin.get('electrical_type', 'passive')).replace('_', ' ').title()}"
            for pin in pins
        )
    if parameters:
        lines.extend(["", "Parameters"])
        lines.extend(
            f"  {item.get('name')}  ·  {item.get('type', 'text')}  ·  default: {item.get('default', '')}"
            for item in parameters
        )
    lines.extend(["", f"Circuit implementations · {len(variants) + 1}"])
    lines.append(f"  Default · {resolve_variant_implementation(manifest, '')}")
    lines.extend(
        f"  {item.get('value')}"
        + (f"  ·  PCB: {item['layout_id']}" if item.get("layout_id") else "")
        for item in variants
    )
    if layouts:
        lines.extend(["", f"PCB layouts · {len(layouts)}"])
        lines.extend(
            f"  {item.get('name', item.get('id'))}"
            + ("  ·  default" if str(item.get("id")) == str(manifest.get("default_layout")) else "")
            for item in layouts
        )
    if profiles:
        lines.extend(["", f"Profiles · {len(profiles)}"])
        lines.extend(
            f"  {item.get('name', item.get('id'))}"
            for item in profiles
        )
    return "\n".join(lines)


def _register_implementation_catalog(record, library_roots: list[Path]) -> Path:
    """Register every shared facade so implementation editors can use Add Symbol."""
    catalog = build_project_symbol_catalog(record.path, library_roots)
    return register_project_symbol_library(
        record.path,
        LIBRARY_NICKNAME,
        catalog,
        description="KiCad Blocks — registered child facades",
        replace_existing=True,
    )


def _launch_block_asset(
    kicad,
    path: Path,
    description: str,
    *,
    editor_window_id: str | None = None,
):
    """Open a stored KiCad asset with the executable resolved by KiCad."""
    del editor_window_id  # No portable cross-process transient-owner API is exposed by KiCad IPC.
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"The selected {description} is missing: {path}")
    executable_name = "pcbnew" if description == "PCB layout" else "eeschema"
    executable = kicad.get_kicad_binary_path(executable_name)
    if not executable:
        raise RuntimeError(f"KiCad did not provide an executable path for {description}.")
    return subprocess.Popen([executable, str(path)], close_fds=True)


def _save_block_variants(
    record, variants: list[dict], *, default_variant=_KEEP_DEFAULT_VARIANT
) -> None:
    """Publish implementation variants and the explicitly selected default."""
    import json
    import shutil

    from .model import slugify
    from .repository import (
        MANIFEST_NAME,
        _record,
        atomic_write,
        read_manifest,
        refresh_library_block,
    )

    block_path = Path(record.path).resolve()
    manifest = read_manifest(block_path)
    previous = list(manifest.get("variants", []))
    previous_files = {str(item["file"]) for item in previous}
    previous_layout_by_value = {
        str(item.get("value", "")).casefold(): str(item.get("layout_id", "")).casefold()
        for item in previous if item.get("layout_id")
    }
    previous_layout_by_file = {
        str(item.get("file", "")).casefold(): str(item.get("layout_id", "")).casefold()
        for item in previous if item.get("layout_id")
    }
    normalized = []
    retained_files = set()
    files_to_copy = []
    added_files = set()
    seen_values = set()
    seen_files = set()

    for item in variants:
        value = str(item.get("value", "")).strip()
        if not value or value.casefold() in seen_values:
            raise ValueError(f"Implementation values must be unique and non-empty: {value!r}")
        seen_values.add(value.casefold())

        if item.get("file"):
            filename = str(item["file"])
            source = None
        else:
            source = Path(item.get("source", "")).expanduser().resolve()
            if not source.is_file() or source.suffix.casefold() != ".kicad_sch":
                raise ValueError("Implementation variant must be an existing .kicad_sch file: " + str(source))
            stem = slugify(value)
            filename = f"implementation_{stem}.kicad_sch"
            suffix = 2
            while (
                ((block_path / filename).exists() and filename not in previous_files)
                or filename.casefold() in seen_files
            ):
                filename = f"implementation_{stem}_{suffix}.kicad_sch"
                suffix += 1

        if Path(filename).name != filename or Path(filename).suffix.casefold() != ".kicad_sch":
            raise ValueError(f"Unsafe implementation filename: {filename!r}")
        if filename.casefold() in seen_files:
            raise ValueError(f"Two implementation variants use the same file: {filename!r}")
        seen_files.add(filename.casefold())
        destination = block_path / filename
        if source is not None and source != destination.resolve():
            files_to_copy.append((source, destination))
            added_files.add(filename)
        retained_files.add(filename)
        descriptor = {"value": value, "file": filename}
        linked_layout = (
            item.get("layout_id")
            or previous_layout_by_file.get(filename.casefold())
            or previous_layout_by_value.get(value.casefold())
        )
        if linked_layout:
            descriptor["layout_id"] = str(linked_layout).casefold()
        normalized.append(descriptor)

    available_values = {item["value"].casefold() for item in normalized}
    dangling_profiles = [
        str(item.get("name", item.get("id", "Unnamed profile")))
        for item in manifest.get("profiles", [])
        if item.get("variant") and str(item["variant"]).casefold() not in available_values
    ]
    if dangling_profiles:
        raise ValueError(
            "These configuration profiles still use an implementation being removed or renamed: "
            + ", ".join(dangling_profiles)
            + ". Update those profiles before changing the implementation list."
        )

    new_value_by_file = {str(item["file"]).casefold(): str(item["value"]) for item in normalized}
    old_value_to_new = {}
    for old in previous:
        replacement_value = new_value_by_file.get(str(old.get("file", "")).casefold())
        old_value_to_new[str(old.get("value", "")).casefold()] = replacement_value

    if default_variant is _KEEP_DEFAULT_VARIANT:
        previous_default = str(manifest.get("default_variant") or "").casefold()
        selected_default = old_value_to_new.get(previous_default) if previous_default else None
    elif default_variant is None or str(default_variant).strip().casefold() in {"", "default", "base"}:
        selected_default = None
    else:
        selected_default = next(
            (item["value"] for item in normalized
             if item["value"].casefold() == str(default_variant).strip().casefold()),
            None,
        )
        if selected_default is None:
            raise ValueError(f"Default implementation {default_variant!r} is not in the saved variant list.")

    copied_paths = []
    try:
        for source, destination in files_to_copy:
            shutil.copy2(source, destination)
            copied_paths.append(destination)
    except OSError:
        for path in copied_paths:
            path.unlink(missing_ok=True)
        raise

    removed_files = previous_files - retained_files
    manifest["variants"] = normalized
    if selected_default:
        manifest["default_variant"] = selected_default
    else:
        manifest.pop("default_variant", None)

    # Keep per-layout implementation tags valid when selectors are renamed or removed.
    for layout in manifest.get("layouts", []):
        links = []
        for raw_link in layout.get("implementations", []):
            key = str(raw_link).casefold()
            if key == "default":
                link = "Default"
            elif key in old_value_to_new:
                link = old_value_to_new[key]
            else:
                link = next(
                    (item["value"] for item in normalized if item["value"].casefold() == key),
                    None,
                )
            if link and link not in links:
                links.append(link)
        if not links:
            links = ["Default"]
        layout["implementations"] = links

    manifest["files"] = [
        filename for filename in manifest.get("files", []) if filename not in removed_files
    ]
    for filename in sorted(added_files):
        if filename not in manifest["files"]:
            manifest["files"].append(filename)
    atomic_write(
        block_path / MANIFEST_NAME,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    updated = _record(block_path)
    result = refresh_library_block(updated)
    if not result.get("changed"):
        manifest = read_manifest(block_path)
        manifest["version"] = int(manifest.get("version", 1)) + 1
        atomic_write(
            block_path / MANIFEST_NAME,
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        )
    for filename in removed_files:
        (block_path / filename).unlink(missing_ok=True)


def _save_block_layouts(
    record,
    layouts: list[dict],
    *,
    default_layout_id: str | None,
    variant_layouts: dict[str, str] | None = None,
    layout_implementations: dict[str, list[str]] | None = None,
):
    """Copy PCB assets and atomically save non-empty implementation associations."""
    import json
    import shutil

    from .model import slugify
    from .repository import (
        MANIFEST_NAME,
        _record,
        atomic_write,
        read_manifest,
        refresh_library_block,
    )

    block_path = Path(record.path).resolve()
    manifest = read_manifest(block_path)
    previous = list(manifest.get("layouts", []))
    previous_files = {
        str(name)
        for item in previous
        for name in (item.get("file"), item.get("project_file"))
        if name
    }
    previous_files.update(
        str(name)
        for name in (manifest.get("layout_file"), manifest.get("layout_project_file"))
        if name
    )
    raw_default = str(default_layout_id or "").casefold()
    if not raw_default and layouts:
        first = layouts[0]
        raw_default = str(first.get("id") or slugify(str(first.get("name", "")))).casefold()
    variant_links = variant_layouts or {}
    implementation_names = {
        "default": "Default",
        **{
            str(item["value"]).casefold(): str(item["value"])
            for item in manifest.get("variants", [])
        },
    }
    requested_implementation_links = {
        str(key).casefold(): list(values)
        for key, values in (layout_implementations or {}).items()
    }

    normalized = []
    files_to_copy = []
    added_files = set()
    seen_ids = set()
    seen_names = set()
    seen_files = set()

    for item in layouts:
        name = str(item.get("name", "")).strip()
        layout_id = str(item.get("id") or slugify(name)).casefold()
        source_value = item.get("source")
        if not name or len(name) > 80 or not re.fullmatch(r"[a-z0-9_]+", layout_id):
            raise ValueError(
                "PCB layouts need a name and a lowercase ID using letters, numbers or underscores."
            )
        if layout_id in seen_ids or name.casefold() in seen_names:
            raise ValueError(f"Duplicate PCB layout name or ID: {name!r}.")
        seen_ids.add(layout_id)
        seen_names.add(name.casefold())

        if source_value:
            source = Path(source_value).expanduser().resolve()
            if not source.is_file() or source.suffix.casefold() != ".kicad_pcb":
                raise ValueError(f"PCB layout must be an existing .kicad_pcb file: {source}")
            filename = f"layout_{layout_id}.kicad_pcb"
            counter = 2
            while (
                ((block_path / filename).exists() and filename not in previous_files)
                or filename.casefold() in seen_files
            ):
                filename = f"layout_{layout_id}_{counter}.kicad_pcb"
                counter += 1
            destination = block_path / filename
            if source != destination.resolve():
                files_to_copy.append((source, destination))
                added_files.add(filename)
        else:
            filename = str(item.get("file", ""))
            if (
                Path(filename).name != filename
                or Path(filename).suffix.casefold() != ".kicad_pcb"
                or not (block_path / filename).is_file()
            ):
                raise ValueError(f"Stored PCB layout file is missing or unsafe: {filename!r}")

        if filename.casefold() in seen_files:
            raise ValueError(f"Two PCB layouts use the same file: {filename!r}")
        seen_files.add(filename.casefold())

        project_source_value = item.get("project_source")
        if project_source_value:
            project_source = Path(project_source_value).expanduser().resolve()
            if not project_source.is_file() or project_source.suffix.casefold() != ".kicad_pro":
                raise ValueError(
                    f"PCB project settings must be an existing .kicad_pro file: {project_source}"
                )
            project_filename = f"layout_{layout_id}.kicad_pro"
            project_destination = block_path / project_filename
            if project_source != project_destination.resolve():
                files_to_copy.append((project_source, project_destination))
                added_files.add(project_filename)
        else:
            project_filename = item.get("project_file")
            if project_filename and (
                Path(str(project_filename)).name != str(project_filename)
                or Path(str(project_filename)).suffix.casefold() != ".kicad_pro"
                or not (block_path / str(project_filename)).is_file()
            ):
                raise ValueError(
                    f"Stored project-settings file is missing or unsafe: {project_filename!r}"
                )

        raw_links = requested_implementation_links.get(
            layout_id, item.get("implementations")
        )
        if raw_links is None:
            raw_links = []
            if layout_id == raw_default:
                raw_links.append("Default")
            raw_links.extend(
                str(value)
                for value, linked in variant_links.items()
                if str(linked).casefold() == layout_id
            )
        normalized_links = []
        for raw_link in raw_links:
            key = str(raw_link).strip().casefold()
            label = implementation_names.get(key)
            if label is None:
                raise ValueError(
                    f"PCB layout {name!r} links to unknown implementation {raw_link!r}."
                )
            if label not in normalized_links:
                normalized_links.append(label)
        if not normalized_links:
            normalized_links = ["Default"]

        descriptor = {
            "id": layout_id,
            "name": name,
            "file": filename,
            "project_file": project_filename,
            "implementations": normalized_links,
        }
        if item.get("ports") is not None:
            descriptor["ports"] = dict(item["ports"])
        normalized.append(descriptor)

    available_ids = {item["id"] for item in normalized}
    if raw_default and raw_default not in available_ids:
        raise ValueError(f"Default PCB layout {raw_default!r} is not defined.")
    if normalized and not raw_default:
        raw_default = normalized[0]["id"]

    updated_variants = []
    for variant in manifest.get("variants", []):
        updated = dict(variant)
        if not normalized:
            updated.pop("layout_id", None)
            updated_variants.append(updated)
            continue
        linked_id = variant_links.get(str(variant["value"]))
        if linked_id is None:
            linked_id = variant_links.get(
                str(variant["value"]).casefold(), variant.get("layout_id")
            )
        if normalized and not linked_id:
            linked_id = raw_default
        if linked_id:
            linked_id = str(linked_id).casefold()
            if linked_id not in available_ids:
                raise ValueError(
                    f"Implementation {variant['value']!r} links to unknown PCB layout {linked_id!r}."
                )
            updated["layout_id"] = linked_id
        else:
            updated.pop("layout_id", None)
        updated_variants.append(updated)

    copied_paths = []
    try:
        for source, destination in files_to_copy:
            shutil.copy2(source, destination)
            copied_paths.append(destination)
    except OSError:
        for copied in copied_paths:
            copied.unlink(missing_ok=True)
        raise

    removed_files = previous_files - {
        str(name)
        for item in normalized
        for name in (item.get("file"), item.get("project_file"))
        if name
    }
    manifest["layouts"] = normalized
    manifest["default_layout"] = raw_default or None
    manifest["variants"] = updated_variants
    default_layout = next(
        (item for item in normalized if item["id"] == raw_default),
        {},
    )
    manifest["layout_file"] = default_layout.get("file")
    manifest["layout_project_file"] = default_layout.get("project_file")
    manifest["files"] = [
        name for name in manifest.get("files", []) if name not in removed_files
    ]
    for name in sorted(added_files):
        if name not in manifest["files"]:
            manifest["files"].append(name)
    atomic_write(
        block_path / MANIFEST_NAME,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    updated_record = _record(block_path)
    result = refresh_library_block(updated_record)
    if not result.get("changed"):
        manifest = read_manifest(block_path)
        manifest["version"] = int(manifest.get("version", 1)) + 1
        atomic_write(
            block_path / MANIFEST_NAME,
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        )
    for filename in removed_files:
        (block_path / filename).unlink(missing_ok=True)
    return _record(block_path)


def _new_blank_pcb_source() -> Path:
    """Create a minimal empty KiCad PCB document for a new stored layout."""
    descriptor, filename = tempfile.mkstemp(prefix="kicad-blocks-layout-", suffix=".kicad_pcb")
    os.close(descriptor)
    path = Path(filename)
    path.write_text(
        '(kicad_pcb\n'
        '  (version 20250114)\n'
        '  (generator "pcbnew")\n'
        '  (general (thickness 1.6))\n'
        '  (paper "A4")\n'
        '  (layers\n'
        '    (0 "F.Cu" signal)\n'
        '    (31 "B.Cu" signal)\n'
        '    (32 "B.Adhes" user "B.Adhesive")\n'
        '    (33 "F.Adhes" user "F.Adhesive")\n'
        '    (34 "B.Paste" user)\n'
        '    (35 "F.Paste" user)\n'
        '    (36 "B.SilkS" user)\n'
        '    (37 "F.SilkS" user)\n'
        '    (38 "B.Mask" user)\n'
        '    (39 "F.Mask" user)\n'
        '    (40 "Dwgs.User" user)\n'
        '    (41 "Cmts.User" user)\n'
        '    (42 "Eco1.User" user)\n'
        '    (43 "Eco2.User" user)\n'
        '    (44 "Edge.Cuts" user)\n'
        '    (45 "Margin" user)\n'
        '    (46 "B.CrtYd" user)\n'
        '    (47 "F.CrtYd" user)\n'
        '    (48 "B.Fab" user)\n'
        '    (49 "F.Fab" user)\n'
        '  )\n'
        '  (setup)\n'
        '  (net 0 "")\n'
        ')\n',
        encoding="utf-8",
    )
    return path


def _ask_layout_details(parent, record) -> dict | None:
    """Choose an existing board or create a blank starting point for a named layout."""
    dialog = tk.Toplevel(parent)
    dialog.title("Add PCB layout")
    dialog.transient(parent)
    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="PCB layout name").grid(row=0, column=0, sticky="w")
    name_value = tk.StringVar(value="New layout")
    name_entry = ttk.Entry(frame, textvariable=name_value)
    name_entry.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 12))
    ttk.Label(frame, text="Starting point").grid(row=2, column=0, sticky="w")
    mode_value = tk.StringVar(value="Create a new empty board")
    mode_combo = ttk.Combobox(
        frame,
        textvariable=mode_value,
        values=("Create a new empty board", "Choose an existing PCB file"),
        state="readonly",
    )
    mode_combo.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 12))
    source_value = tk.StringVar()
    source_entry = ttk.Entry(frame, textvariable=source_value)
    source_entry.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 12))
    browse_button = _action_button(frame, "Choose file…", lambda: None, "file", "Choose an existing KiCad PCB file")
    browse_button.grid(row=4, column=2, sticky="ew", padx=(8, 0), pady=(4, 12))
    implementation_values = ["Default"] + [
        str(item["value"]) for item in record.manifest.get("variants", [])
    ]
    link_value = tk.StringVar(value=implementation_values[0])
    ttk.Label(frame, text="Use for implementation").grid(row=5, column=0, sticky="w")
    link_combo = ttk.Combobox(
        frame,
        textvariable=link_value,
        values=implementation_values,
        state="readonly",
    )
    link_combo.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(4, 4))
    _add_tooltip(link_combo, "This layout is available when applying the selected implementation to a board.")
    frame.columnconfigure(0, weight=1)
    result = [None]

    def browse():
        selected = _pick_kicad_file(
            dialog,
            title="Choose PCB layout",
            suffix=".kicad_pcb",
            initial_path=source_value.get(),
        )
        if selected:
            source_value.set(selected)

    browse_button.configure(command=browse)

    def update_mode(_event=None):
        use_existing = mode_value.get() == "Choose an existing PCB file"
        if use_existing:
            source_entry.grid()
            browse_button.grid()
            source_entry.configure(state="normal")
            browse_button.configure(state="normal")
        else:
            source_entry.grid_remove()
            browse_button.grid_remove()

    mode_combo.bind("<<ComboboxSelected>>", update_mode)
    update_mode()

    def accept():
        name = name_value.get().strip()
        if not name or len(name) > 80:
            messagebox.showerror("Add PCB layout", "Enter a layout name of 1 to 80 characters.", parent=dialog)
            name_entry.focus_set()
            return
        if mode_value.get() == "Create a new empty board":
            source = _new_blank_pcb_source()
        else:
            source = Path(source_value.get()).expanduser().resolve()
            if not source.is_file() or source.suffix.casefold() != ".kicad_pcb":
                messagebox.showerror(
                    "Add PCB layout", "Choose an existing KiCad .kicad_pcb file.", parent=dialog
                )
                source_entry.focus_set()
                return
        result[0] = {
            "name": name,
            "source": str(source),
            "linked_implementation": link_value.get(),
        }
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 0))
    _action_button(buttons, "Cancel", dialog.destroy, "cancel", "Cancel layout creation").pack(side="right")
    _action_button(buttons, "Add layout", accept, "create", "Add this PCB layout to the block").pack(side="right", padx=(0, 8))
    dialog.bind("<Return>", lambda _event: accept())
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    _set_window_geometry(dialog, 860, 620, 760, 560)
    dialog.grab_set()
    name_entry.focus_set()
    dialog.wait_window()
    return result[0]


def _show_block_layout_editor(
    record, parent, kicad=None, editor_window_id: str | None = None
) -> Path | None:
    """Manage stored PCB boards and keep every board linked to an implementation."""
    from .model import slugify
    from .repository import read_manifest

    dialog = tk.Toplevel(parent)
    dialog.title(f"PCB layouts — {record.name}")
    dialog.transient(parent)
    frame = ttk.Frame(dialog, padding=14)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(1, weight=1)
    ttk.Label(frame, text="Stored PCB layouts", font=("TkDefaultFont", 12, "bold")).grid(
        row=0, column=0, sticky="w"
    )
    table_frame = ttk.Frame(frame)
    table_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 6))
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        table_frame,
        columns=("name", "assigned", "file"),
        show="headings",
        selectmode="browse",
    )
    tree.heading("name", text="Layout")
    tree.heading("assigned", text="Available to")
    tree.heading("file", text="PCB file")
    tree.column("name", width=190, minwidth=150, stretch=False)
    tree.column("assigned", width=220, minwidth=160, stretch=False)
    tree.column("file", width=470, minwidth=260, stretch=True)
    scroll = ttk.Scrollbar(
        table_frame, orient="vertical", command=tree.yview, style="Vertical.TScrollbar"
    )
    tree.configure(yscrollcommand=scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")

    layouts = [dict(item) for item in _layout_choices(record)]
    for item in layouts:
        item["id"] = str(
            item.get("id")
            or slugify(str(item.get("name") or Path(item["file"]).stem))
        ).casefold()
    default_layout_id = str(record.manifest.get("default_layout") or "").casefold()
    if not default_layout_id and layouts:
        default_layout_id = layouts[0]["id"]
    default_layout_id_holder = [default_layout_id]
    variant_layouts = {
        str(item["value"]): str(item.get("layout_id") or default_layout_id).casefold()
        for item in record.manifest.get("variants", [])
        if layouts
    }
    layout_implementations: dict[str, list[str]] = {}
    for item in layouts:
        links = list(item.get("implementations") or [])
        if not links and item["id"] == default_layout_id:
            links = ["Default"]
        if not links:
            links = [
                str(value)
                for value, linked in variant_layouts.items()
                if linked == item["id"]
            ]
        if not links:
            links = ["Default"]
        layout_implementations[item["id"]] = list(dict.fromkeys(links))

    changed = [False]
    added_ids = []
    status_value = tk.StringVar(
        value="Each saved PCB layout is associated with at least one implementation."
    )

    def assigned_to(layout_id: str) -> str:
        return ", ".join(layout_implementations.get(layout_id, ["Default"]))

    def render(selected_id: str | None = None):
        tree.delete(*tree.get_children())
        for index, item in enumerate(layouts):
            iid = str(index)
            tree.insert(
                "", "end", iid=iid,
                values=(item.get("name", item["id"]), assigned_to(item["id"]), item["file"]),
                tags=("even" if index % 2 == 0 else "odd",),
            )
            if item["id"] == selected_id:
                tree.selection_set(iid)
                tree.focus(iid)
        if selected_id is not None:
            links = layout_implementations.get(selected_id, ["Default"])
            implementation_value.set(links[0])
        update_actions()

    def selected_layout():
        selection = tree.selection()
        if not selection:
            return None
        index = int(selection[0])
        return layouts[index] if 0 <= index < len(layouts) else None

    def add_layout():
        details = _ask_layout_details(dialog, record)
        if details is None:
            return
        name = details["name"]
        layout_id = slugify(name).casefold()
        if any(
            item["id"] == layout_id or str(item["name"]).casefold() == name.casefold()
            for item in layouts
        ):
            messagebox.showerror(
                "Add PCB layout", f"A layout named {name!r} already exists.", parent=dialog
            )
            return
        linked_implementation = str(details["linked_implementation"])
        item = {
            "id": layout_id,
            "name": name,
            "source": details["source"],
            "project_file": None,
            "implementations": [linked_implementation],
        }
        layouts.append(item)
        layout_implementations[layout_id] = [linked_implementation]
        added_ids.append(layout_id)
        if linked_implementation == "Default":
            default_layout_id_holder[0] = layout_id
        else:
            variant_layouts[linked_implementation] = layout_id
        changed[0] = True
        status_value.set(
            f"Added {name}."
        )
        render(layout_id)

    def remove_layout():
        item = selected_layout()
        if item is None:
            return
        replacement_id = next(
            (candidate["id"] for candidate in layouts if candidate["id"] != item["id"]),
            None,
        )
        consequence = (
            f" Variants linked to it will use {next((candidate['name'] for candidate in layouts if candidate['id'] == replacement_id), 'the remaining layout')} instead."
            if replacement_id
            else " No PCB layouts will remain; variant layout assignments will be cleared."
        )
        if not messagebox.askyesno(
            "Remove PCB layout",
            f"Remove {item['name']}?{consequence}",
            parent=dialog,
        ):
            return
        removed_id = item["id"]
        layouts.remove(item)
        layout_implementations.pop(removed_id, None)
        added_ids[:] = [item_id for item_id in added_ids if item_id != removed_id]
        if default_layout_id_holder[0] == removed_id:
            default_layout_id_holder[0] = replacement_id
            if replacement_id:
                layout_implementations.setdefault(replacement_id, [])
                if "Default" not in layout_implementations[replacement_id]:
                    layout_implementations[replacement_id].append("Default")
        for value, linked in list(variant_layouts.items()):
            if linked == removed_id:
                if replacement_id:
                    variant_layouts[value] = replacement_id
                    layout_implementations.setdefault(replacement_id, [])
                    if value not in layout_implementations[replacement_id]:
                        layout_implementations[replacement_id].append(value)
                else:
                    variant_layouts.pop(value)
        changed[0] = True
        status_value.set(
            f"Removed {item['name']}."
        )
        render(replacement_id)

    def make_default():
        item = selected_layout()
        if item is None:
            return
        default_layout_id_holder[0] = item["id"]
        links = layout_implementations.setdefault(item["id"], [])
        if "Default" not in links:
            links.append("Default")
        changed[0] = True
        status_value.set(f"Default: {item['name']}.")
        render(item["id"])

    implementation_values = ["Default"] + [
        str(item["value"]) for item in record.manifest.get("variants", [])
    ]
    implementation_value = tk.StringVar(value="Default")

    def link_selected():
        item = selected_layout()
        if item is None:
            return
        selected = implementation_value.get()
        links = layout_implementations.setdefault(item["id"], [])
        if selected not in links:
            links.append(selected)
        if selected == "Default":
            default_layout_id_holder[0] = item["id"]
        else:
            variant_layouts[selected] = item["id"]
        changed[0] = True
        status_value.set(f"Assigned {item['name']} to {selected}.")
        render(item["id"])

    def update_actions(_event=None):
        has_selection = selected_layout() is not None
        remove_button.configure(state="normal" if has_selection else "disabled")
        default_button.configure(state="normal" if has_selection else "disabled")
        link_button.configure(state="normal" if has_selection else "disabled")
        save_button.configure(state="normal" if changed[0] else "disabled")

    palette = getattr(parent, "_kicad_blocks_palette", {})
    ttk.Label(
        frame,
        textvariable=status_value,
        foreground=palette.get("muted", "#5f6871"),
        font=("TkDefaultFont", 8),
        wraplength=900,
        justify="left",
    ).grid(row=2, column=0, sticky="ew", pady=(2, 4))
    actions = ttk.Frame(frame)
    actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    _action_button(actions, "Add layout", add_layout, "add", "Add a PCB layout").pack(side="left")
    remove_button = _action_button(actions, "Remove", remove_layout, "remove", "Remove the selected PCB layout", state="disabled")
    remove_button.pack(side="left", padx=(8, 0))
    default_button = _action_button(
        actions, "Set default", make_default, "default", "Use this layout as the default", state="disabled"
    )
    default_button.pack(side="left", padx=(8, 0))

    link_actions = ttk.Frame(frame)
    link_actions.grid(row=4, column=0, sticky="ew", pady=(8, 0))
    ttk.Label(link_actions, text="Make selected layout available to").pack(side="left")
    implementation_combo = ttk.Combobox(
        link_actions, textvariable=implementation_value,
        values=implementation_values, state="readonly", width=22,
    )
    implementation_combo.pack(side="left", padx=(8, 0))
    link_button = _action_button(
        link_actions, "Assign", link_selected, "select",
        "Associate the selected layout with this implementation", state="disabled"
    )
    link_button.pack(side="left", padx=(8, 0))


    def cancel_layout_edits():
        if changed[0] and not messagebox.askyesno(
            "Discard layout changes",
            "Discard the unsaved PCB layout changes?",
            parent=dialog,
        ):
            return
        dialog.destroy()

    footer = ttk.Frame(frame)
    footer.grid(row=5, column=0, sticky="ew", pady=(12, 0))
    _action_button(
        footer, "Cancel", cancel_layout_edits, "cancel",
        "Close without saving layout changes",
    ).pack(side="right")
    save_button = _action_button(
        footer, "Save layouts", lambda: None, "save", "Save PCB layout changes", state="disabled"
    )
    save_button.pack(side="right", padx=(0, 8))
    created_layout_to_open = [None]

    def save_and_close():
        if not changed[0]:
            dialog.destroy()
            return
        try:
            _save_block_layouts(
                record, layouts,
                default_layout_id=default_layout_id_holder[0],
                variant_layouts=variant_layouts,
                layout_implementations=layout_implementations,
            )
            if added_ids:
                updated = read_manifest(Path(record.path))
                new_item = next(
                    item for item in updated.get("layouts", [])
                    if item["id"] == added_ids[-1]
                )
                created_layout_to_open[0] = Path(record.path) / new_item["file"]
        except (OSError, RuntimeError, TypeError, ValueError, KeyError, StopIteration) as exc:
            messagebox.showerror("Save PCB layouts", str(exc), parent=dialog)
            return
        dialog.destroy()

    save_button.configure(command=save_and_close)
    tree.bind("<<TreeviewSelect>>", update_actions)
    dialog.bind("<Escape>", lambda _event: cancel_layout_edits())
    render(layouts[0]["id"] if layouts else None)
    dialog.protocol("WM_DELETE_WINDOW", cancel_layout_edits)
    _set_window_geometry(dialog, 1120, 800, 980, 650)
    dialog.grab_set()
    dialog.wait_window()
    return created_layout_to_open[0]

def _pick_save_file(
    parent,
    *,
    title: str,
    initial_path: str | Path,
    defaultextension: str,
    filetypes: list[tuple[str, str]],
) -> str | None:
    """Open Tk's native save-file dialog with the block's suggested filename."""
    initial = Path(initial_path).expanduser()
    directory = initial.parent if initial.suffix else initial
    if not directory.is_dir():
        directory = Path.cwd()
    initialfile = initial.name if initial.suffix else ""
    return filedialog.asksaveasfilename(
        title=title,
        initialdir=str(directory),
        initialfile=initialfile,
        defaultextension=defaultextension,
        filetypes=filetypes,
        parent=parent,
    ) or None


def _pick_kicad_file(
    parent,
    *,
    title: str,
    suffix: str,
    initial_path: str | Path = "",
) -> str | None:
    """Open Tk's native file chooser for a KiCad document."""
    initial = Path(initial_path).expanduser() if initial_path else Path.cwd()
    directory = initial.parent if initial.is_file() else initial
    if not directory.is_dir():
        directory = Path.cwd()
    filetypes = [
        (f"KiCad {suffix.upper().lstrip('.')} files", f"*{suffix}"),
        ("All files", "*.*"),
    ]
    return filedialog.askopenfilename(
        title=title,
        initialdir=str(directory),
        initialfile=initial.name if initial.is_file() else "",
        filetypes=filetypes,
        parent=parent,
    ) or None


def _pick_directory(
    parent, *, title: str, initial_path: str | Path | None = None
) -> str | None:
    """Open Tk's native directory chooser with a shared initial-directory policy."""
    initial = Path(initial_path).expanduser() if initial_path else Path.cwd()
    if initial.is_file():
        initial = initial.parent
    if not initial.is_dir():
        initial = Path.cwd()
    return filedialog.askdirectory(
        title=title,
        initialdir=str(initial),
        mustexist=True,
        parent=parent,
    ) or None


def _new_blank_schematic_source() -> Path:
    """Create a valid empty KiCad schematic as a temporary source for a new variant."""
    import uuid as uuid_module

    descriptor, filename = tempfile.mkstemp(prefix="kicad-blocks-implementation-", suffix=".kicad_sch")
    os.close(descriptor)
    path = Path(filename)
    path.write_text(
        "(kicad_sch (version 20250114) (generator \"eeschema\")\n"
        f"  (uuid \"{uuid_module.uuid4()}\")\n"
        '  (paper "A4")\n'
        "  (lib_symbols)\n"
        '  (sheet_instances (path "/" (page "1")))\n'
        "  (embedded_fonts no)\n"
        ")\n",
        encoding="utf-8",
    )
    return path


def _ask_implementation_details(
    parent,
    *,
    value: str = "",
    source: str = "",
    record=None,
    allow_create: bool = False,
) -> tuple[str, str] | None:
    """Collect an implementation selector and its starting schematic."""
    dialog = tk.Toplevel(parent)
    dialog.title("Implementation details")
    dialog.transient(parent)
    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text="New circuit implementation",
        font=("TkDefaultFont", 12, "bold"),
    ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

    ttk.Label(frame, text="Variant value").grid(
        row=1, column=0, columnspan=3, sticky="w"
    )
    selector_value = tk.StringVar(value=value)
    selector_entry = ttk.Entry(frame, textvariable=selector_value)
    selector_entry.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(2, 12))
    _add_tooltip(selector_entry, "Stored in KB_VARIANT on the placed facade.")

    mode_value = tk.StringVar(value="Choose a schematic file")
    source_value = tk.StringVar(value=source)
    copy_value = tk.StringVar()
    copy_sources: dict[str, Path] = {}
    if allow_create:
        ttk.Label(frame, text="Starting point").grid(
            row=3, column=0, columnspan=3, sticky="w"
        )
        modes = [
            "Choose a schematic file",
            "Create a new empty schematic",
            "Copy an existing implementation",
        ]
        mode_combo = ttk.Combobox(
            frame, textvariable=mode_value, values=modes, state="readonly"
        )
        mode_combo.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(4, 12))
    else:
        modes = ["Choose a schematic file"]
        mode_combo = None

    source_row = 5 if allow_create else 3
    source_label = ttk.Label(frame, text="Source schematic (.kicad_sch)")
    source_label.grid(row=source_row, column=0, columnspan=3, sticky="w")
    source_entry = ttk.Entry(frame, textvariable=source_value)
    source_entry.grid(row=source_row + 1, column=0, columnspan=2, sticky="ew", pady=(4, 14))
    browse_button = _action_button(
        frame, "Choose file…", lambda: None, "file", "Choose an implementation schematic"
    )
    browse_button.grid(row=source_row + 1, column=2, sticky="ew", padx=(8, 0), pady=(4, 14))
    copy_combo = ttk.Combobox(
        frame,
        textvariable=copy_value,
        values=(),
        state="readonly",
    )
    copy_combo.grid(
        row=source_row + 1, column=0, columnspan=3, sticky="ew", pady=(4, 14)
    )
    copy_combo.grid_remove()

    def browse():
        selected = _pick_kicad_file(
            dialog,
            title="Choose implementation schematic",
            suffix=".kicad_sch",
            initial_path=source_value.get(),
        )
        if selected:
            source_value.set(selected)
            source_entry.xview_moveto(1.0)

    browse_button.configure(command=browse)

    def update_source_mode(_event=None):
        mode = mode_value.get()
        copy_combo.grid_remove()
        source_entry.grid_remove()
        browse_button.grid_remove()
        if mode == "Create a new empty schematic":
            source_label.configure(text="The new implementation starts as a blank KiCad schematic.")
        elif mode == "Copy an existing implementation":
            source_label.configure(text="Copy this library implementation:")
            source_entry.grid_remove()
            browse_button.grid_remove()
            copy_combo.grid()
            copy_combo.set(next(iter(copy_sources), ""))
        else:
            source_label.configure(text="Source schematic (.kicad_sch)")
            source_entry.grid()
            browse_button.grid()

    if allow_create:
        for label, path in [
            ("Default", Path(record.path) / resolve_variant_implementation(record.manifest, ""))
        ] + [
            (str(item["value"]), Path(record.path) / str(item["file"]))
            for item in record.manifest.get("variants", [])
        ]:
            copy_sources[label] = path
        copy_combo.configure(values=list(copy_sources))
        mode_combo.bind("<<ComboboxSelected>>", update_source_mode)
    else:
        copy_sources.clear()

    frame.columnconfigure(0, weight=1)
    result = [None]

    def accept():
        selected_value = selector_value.get().strip()
        if not selected_value:
            messagebox.showerror(
                "Implementation details",
                "Enter an implementation selector.",
                parent=dialog,
            )
            selector_entry.focus_set()
            return
        mode = mode_value.get()
        if allow_create and mode == "Create a new empty schematic":
            selected_source = _new_blank_schematic_source()
        elif allow_create and mode == "Copy an existing implementation":
            selected_source = copy_sources.get(copy_value.get())
            if selected_source is None or not selected_source.is_file():
                messagebox.showerror(
                    "Implementation details",
                    "Choose an existing library implementation to copy.",
                    parent=dialog,
                )
                copy_combo.focus_set()
                return
        else:
            selected_source = Path(source_value.get()).expanduser().resolve()
            if (
                not selected_source.is_file()
                or selected_source.suffix.casefold() != ".kicad_sch"
            ):
                messagebox.showerror(
                    "Implementation details",
                    "Choose an existing KiCad .kicad_sch implementation.",
                    parent=dialog,
                )
                source_entry.focus_set()
                return
        result[0] = (selected_value, str(selected_source))
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=source_row + 2, column=0, columnspan=3, sticky="ew", pady=(12, 4))
    _action_button(buttons, "Cancel", dialog.destroy, "cancel", "Cancel implementation setup").pack(side="right")
    _action_button(
        buttons, "Use implementation", accept, "select", "Use the selected implementation",
    ).pack(side="right", padx=(0, 8))
    dialog.bind("<Return>", lambda _event: accept())
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    source_entry.xview_moveto(1.0)
    selector_entry.focus_set()
    _set_window_geometry(dialog, 860, 540, 740, 460)
    dialog.grab_set()
    dialog.wait_window()
    return result[0]




def _show_block_variant_editor(
    record, parent, *, kicad=None, editor_window_id: str | None = None
) -> Path | None:
    """Manage named implementations and select which one empty KB_VARIANT uses."""
    dialog = tk.Toplevel(parent)
    dialog.title(f"Implementations — {record.name}")
    dialog.transient(parent)

    frame = ttk.Frame(dialog, padding=14)
    frame.pack(fill="both", expand=True)
    palette = getattr(parent, "_kicad_blocks_palette", {})
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(2, weight=1)
    ttk.Label(
        frame, text="Circuit implementations", font=("TkDefaultFont", 12, "bold")
    ).grid(row=0, column=0, sticky="w")
    ttk.Label(
        frame,
        text="Empty KB_VARIANT uses the default implementation.",
        foreground=palette.get("muted", "#5f6871"),
        font=("TkDefaultFont", 8),
    ).grid(row=1, column=0, sticky="w", pady=(3, 8))

    table_frame = ttk.Frame(frame)
    table_frame.grid(row=2, column=0, sticky="nsew")
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        table_frame,
        columns=("value", "file", "role"),
        show="headings",
        selectmode="browse",
    )
    tree.heading("value", text="Implementation")
    tree.heading("file", text="Schematic file")
    tree.heading("role", text="Role")
    tree.column("value", width=220, minwidth=160, stretch=False)
    tree.column("file", width=560, minwidth=320, stretch=True)
    tree.column("role", width=150, minwidth=130, stretch=False)
    scroll = ttk.Scrollbar(
        table_frame, orient="vertical", command=tree.yview, style="Vertical.TScrollbar"
    )
    tree.configure(yscrollcommand=scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")

    palette = getattr(parent, "_kicad_blocks_palette", {})
    tree.tag_configure("even", background=palette.get("surface", "#f4f5f6"))
    tree.tag_configure("odd", background=palette.get("surface_alt", "#e8eaed"))
    variants = [
        {
            "value": str(item["value"]),
            "file": str(item["file"]),
            **({"layout_id": str(item["layout_id"]).casefold()} if item.get("layout_id") else {}),
        }
        for item in record.manifest.get("variants", [])
    ]
    default_variant = [str(record.manifest.get("default_variant") or "")]
    dirty = [False]
    new_implementation_to_open = [None]

    def render(selected_index: int | None = None):
        tree.delete(*tree.get_children())
        base_role = "Current default" if not default_variant[0] else "Base fallback"
        tree.insert(
            "", "end", iid="0",
            values=("Base implementation", str(record.manifest["implementation_file"]), base_role),
            tags=("even",),
        )
        for index, item in enumerate(variants, start=1):
            role = (
                "Current default"
                if item["value"].casefold() == default_variant[0].casefold()
                else "Alternative"
            )
            tree.insert(
                "", "end", iid=str(index),
                values=(item["value"], item["file"], role),
                tags=("odd" if index % 2 else "even",),
            )
        if selected_index is not None and 0 <= selected_index <= len(variants):
            iid = str(selected_index)
            tree.selection_set(iid)
            tree.focus(iid)
        update_actions()

    def selected_index() -> int | None:
        selection = tree.selection()
        return int(selection[0]) if selection else None

    def update_actions(_event=None):
        row_index = selected_index()
        has_selection = row_index is not None
        edit_button.configure(state="normal" if has_selection else "disabled")
        edit_button.configure(text="Edit default" if row_index == 0 else "Edit variant")
        remove_button.configure(
            state="normal" if row_index is not None and row_index > 0 else "disabled"
        )
        if row_index == 0:
            default_button.configure(
                text="Use base as default",
                state="normal" if default_variant[0] else "disabled",
            )
        elif row_index is not None:
            is_default = (
                variants[row_index - 1]["value"].casefold()
                == default_variant[0].casefold()
            )
            default_button.configure(
                text="Default selected" if is_default else "Set as default",
                state="disabled" if is_default else "normal",
            )
        else:
            default_button.configure(text="Set as default", state="disabled")
        save_button.configure(state="normal" if dirty[0] else "disabled")

    def add_variant():
        details = _ask_implementation_details(dialog, record=record, allow_create=True)
        if details is None:
            return
        value, source = details
        if any(item["value"].casefold() == value.casefold() for item in variants):
            messagebox.showerror(
                "Add implementation", f"The value {value!r} already exists.", parent=dialog
            )
            return
        variants.append({"value": value, "source": source, "open_after_save": True})
        dirty[0] = True
        render(len(variants))

    def edit_variant():
        row_index = selected_index()
        if row_index is None:
            return
        if row_index == 0:
            save_and_close(Path(record.path) / str(record.manifest["implementation_file"]))
            return
        index = row_index - 1
        current = variants[index]
        current_source = current.get("source") or str(Path(record.path) / current["file"])
        details = _ask_implementation_details(
            dialog, value=current["value"], source=current_source, record=record
        )
        if details is None:
            return
        value, source = details
        if any(
            other != index and item["value"].casefold() == value.casefold()
            for other, item in enumerate(variants)
        ):
            messagebox.showerror(
                "Edit implementation", f"The value {value!r} already exists.", parent=dialog
            )
            return
        updated = {"value": value, "source": source}
        if current.get("layout_id"):
            updated["layout_id"] = current["layout_id"]
        if current.get("open_after_save"):
            updated["open_after_save"] = True
        if (
            default_variant[0]
            and current["value"].casefold() == default_variant[0].casefold()
        ):
            default_variant[0] = value
        variants[index] = updated
        dirty[0] = True
        render(row_index)

    def remove_variant():
        row_index = selected_index()
        if row_index is None or row_index == 0:
            return
        index = row_index - 1
        candidate = variants[index]
        referenced_profiles = [
            str(item.get("name", item.get("id", "Unnamed profile")))
            for item in record.manifest.get("profiles", [])
            if str(item.get("variant", "")).casefold()
            == candidate["value"].casefold()
        ]
        if referenced_profiles:
            messagebox.showinfo(
                "Implementation is in use",
                f"{candidate['value']} is referenced by: "
                + ", ".join(referenced_profiles)
                + ". Change those profiles before removing this implementation.",
                parent=dialog,
            )
            return
        is_default = (
            default_variant[0]
            and candidate["value"].casefold() == default_variant[0].casefold()
        )
        consequence = (
            " It is the default, so new instances with an empty KB_VARIANT will use the base implementation."
            if is_default else ""
        )
        if not messagebox.askyesno(
            "Remove implementation",
            f"Remove {candidate['value']}?{consequence}",
            parent=dialog,
        ):
            return
        variants.pop(index)
        if is_default:
            default_variant[0] = ""
        dirty[0] = True
        render(min(row_index, len(variants)) if variants else 0)

    def make_default():
        row_index = selected_index()
        if row_index is None:
            return
        selected = "" if row_index == 0 else variants[row_index - 1]["value"]
        if selected.casefold() == default_variant[0].casefold():
            return
        default_variant[0] = selected
        dirty[0] = True
        render(row_index)

    def save_and_close(open_path: Path | None = None):
        if not dirty[0]:
            if open_path is not None:
                new_implementation_to_open[0] = open_path
            dialog.destroy()
            return
        open_value = next(
            (str(item["value"]) for item in variants if item.get("open_after_save")),
            None,
        )
        try:
            _save_block_variants(
                record, variants, default_variant=default_variant[0] or None
            )
            if open_value:
                from .repository import read_manifest

                saved = read_manifest(Path(record.path))
                selected = next(
                    item for item in saved.get("variants", [])
                    if str(item["value"]).casefold() == open_value.casefold()
                )
                new_implementation_to_open[0] = Path(record.path) / selected["file"]
            if open_path is not None:
                new_implementation_to_open[0] = open_path
        except (OSError, RuntimeError, TypeError, ValueError, KeyError, StopIteration) as exc:
            messagebox.showerror("Save implementations", str(exc), parent=dialog)
            return
        dialog.destroy()

    actions = ttk.Frame(frame)
    actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))
    _action_button(actions, "Add variant", add_variant, "add", "Add a named implementation variant").pack(side="left")
    edit_button = _action_button(actions, "Edit variant", edit_variant, "edit", "Edit the selected implementation", state="disabled")
    edit_button.pack(side="left", padx=(8, 0))
    default_button = _action_button(
        actions, "Set as default", make_default, "default",
        "Set the selected implementation as default", state="disabled"
    )
    default_button.pack(side="left", padx=(8, 0))
    remove_button = _action_button(
        actions, "Remove", remove_variant, "remove",
        "Remove the selected variant", state="disabled"
    )
    remove_button.pack(side="left", padx=(8, 0))
    def cancel_variant_edits():
        if dirty[0] and not messagebox.askyesno(
            "Discard implementation changes",
            "Discard the unsaved implementation changes?",
            parent=dialog,
        ):
            return
        dialog.destroy()

    _action_button(
        actions, "Cancel", cancel_variant_edits, "cancel",
        "Close without saving implementation changes",
    ).pack(side="right")
    save_button = _action_button(
        actions, "Save changes", save_and_close, "save",
        "Save implementation changes", state="disabled"
    )
    save_button.pack(side="right", padx=(0, 8))

    tree.bind("<<TreeviewSelect>>", update_actions)
    tree.bind("<Double-1>", lambda _event: edit_variant())
    tree.bind("<Delete>", lambda _event: remove_variant())
    dialog.bind("<Escape>", lambda _event: cancel_variant_edits())
    render(0)
    dialog.protocol("WM_DELETE_WINDOW", cancel_variant_edits)
    _set_window_geometry(dialog, 1080, 740, 900, 620)
    dialog.grab_set()
    dialog.wait_window()
    return new_implementation_to_open[0]


def _show_library_roots_dialog(parent) -> bool:
    """Manage registered library folders with explicit add/create/edit actions."""
    dialog = tk.Toplevel(parent)
    dialog.title("Manage Libraries")
    dialog.transient(parent)

    outer = ttk.Frame(dialog, padding=14)
    outer.pack(fill="both", expand=True)
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(1, weight=1)
    ttk.Label(
        outer,
        text="Registered libraries",
        font=("TkDefaultFont", 12, "bold"),
    ).grid(row=0, column=0, sticky="w", pady=(0, 8))

    table_frame = ttk.Frame(outer)
    table_frame.grid(row=1, column=0, sticky="nsew")
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        table_frame,
        columns=("library", "blocks", "role", "path"),
        show="headings",
        selectmode="browse",
    )
    tree.heading("library", text="Library")
    tree.heading("blocks", text="Blocks")
    tree.heading("role", text="Use")
    tree.heading("path", text="Registered folder")
    tree.column("library", width=190, minwidth=150, stretch=False)
    tree.column("blocks", width=70, minwidth=55, stretch=False, anchor="center")
    tree.column("role", width=140, minwidth=110, stretch=False, anchor="center")
    tree.column("path", width=500, minwidth=220, stretch=True)
    vertical_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
    horizontal_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview, style="Horizontal.TScrollbar")
    tree.configure(
        yscrollcommand=vertical_scroll.set,
        xscrollcommand=horizontal_scroll.set,
    )
    palette = getattr(parent, "_kicad_blocks_palette", {})
    tree.tag_configure("even", background=palette.get("surface", "#f4f5f6"))
    tree.tag_configure("odd", background=palette.get("surface_alt", "#e8eaed"))
    tree.grid(row=0, column=0, sticky="nsew")
    vertical_scroll.grid(row=0, column=1, sticky="ns")
    horizontal_scroll.grid(row=1, column=0, sticky="ew")
    _bind_mousewheel(tree, lambda amount: tree.yview_scroll(amount, "units"))

    status = tk.StringVar()
    ttk.Label(
        outer,
        textvariable=status,
        foreground=getattr(parent, "_kicad_blocks_palette", {}).get("muted", "#5f6871"),
    ).grid(row=2, column=0, sticky="w", pady=(6, 0))

    changed = [False]
    environment_value = os.environ.get("KICAD_BLOCKS_LIBRARY")
    environment_root = (
        Path(environment_value).expanduser().resolve()
        if environment_value else None
    )

    def selected_path() -> Path | None:
        selection = tree.selection()
        if not selection:
            return None
        raw = tree.set(selection[0], "path")
        return Path(raw).expanduser().resolve() if raw else None

    def render(select_path: Path | None = None) -> None:
        previous = select_path or selected_path()
        tree.delete(*tree.get_children())
        roots = configured_library_roots()
        default = default_library_root()
        for index, library_path in enumerate(roots):
            role = (
                "Environment override"
                if library_path == environment_root
                else "Default"
                if library_path == default
                else "Registered"
            )
            try:
                block_count = len(list_blocks(library_path))
            except (OSError, ValueError):
                block_count = "Unavailable"
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(library_path.name or str(library_path), block_count, role, str(library_path)),
                tags=("even" if index % 2 == 0 else "odd",),
            )
            if previous == library_path:
                tree.selection_set(str(index))
                tree.focus(str(index))
                tree.see(str(index))
        status.set(f"{len(roots)} registered libraries")
        update_selection_actions()

    def add_library() -> None:
        selected = _pick_directory(
            dialog, title="Add existing block library folder"
        )
        if not selected:
            return
        try:
            path = Path(selected).expanduser().resolve()
            add_library_root(path)
            changed[0] = True
            render(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Add existing library", str(exc), parent=dialog)

    def create_library() -> None:
        parent_folder = _pick_directory(
            dialog, title="Choose where to create the new library"
        )
        if not parent_folder:
            return
        name = simpledialog.askstring(
            "Create block library",
            "Name for the new library folder:",
            parent=dialog,
        )
        if name is None:
            return
        name = name.strip()
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or Path(name).is_absolute()
        ):
            messagebox.showerror(
                "Create block library",
                "Enter a single folder name without a path.",
                parent=dialog,
            )
            return
        target = Path(parent_folder).expanduser().resolve() / name
        try:
            target.mkdir(parents=False, exist_ok=False)
            try:
                add_library_root(target)
            except Exception:
                target.rmdir()
                raise
            changed[0] = True
            render(target)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Create block library", str(exc), parent=dialog)

    def change_library_folder() -> None:
        old_path = selected_path()
        if old_path is None or old_path == environment_root:
            return
        selected = _pick_directory(
            dialog,
            title="Choose the replacement registered folder",
            initial_path=old_path.parent,
        )
        if not selected:
            return
        new_path = Path(selected).expanduser().resolve()
        if new_path == old_path:
            return
        registered_before = set(configured_library_roots())
        was_default = environment_root is None and old_path == default_library_root()
        default_changed = False
        try:
            add_library_root(new_path)
            if was_default:
                set_library_root(new_path)
                default_changed = True
            remove_library_root(old_path)
            changed[0] = True
            render(new_path)
        except (OSError, ValueError) as exc:
            # Revert any completed registration/default changes if replacing
            # the selected root fails part-way through.
            if default_changed:
                with contextlib.suppress(OSError, ValueError):
                    set_library_root(old_path)
            if new_path not in registered_before:
                with contextlib.suppress(OSError, ValueError):
                    remove_library_root(new_path)
            messagebox.showerror("Change registered folder", str(exc), parent=dialog)

    def remove_library() -> None:
        path = selected_path()
        if path is None or path == environment_root:
            return
        if not messagebox.askyesno(
            "Unregister library",
            f"Unregister {path.name}? Its files remain on disk, but its blocks will no longer appear in the browser.",
            parent=dialog,
        ):
            return
        try:
            remove_library_root(path)
            changed[0] = True
            render()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Unregister library", str(exc), parent=dialog)

    def make_default() -> None:
        path = selected_path()
        if path is None or environment_root is not None:
            return
        try:
            set_library_root(path)
            changed[0] = True
            render(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Set default library", str(exc), parent=dialog)

    library_actions = ttk.Frame(outer, padding=(0, 9, 0, 6))
    library_actions.grid(row=3, column=0, sticky="ew")
    _action_button(
        library_actions, "Add library", add_library, "add",
        "Register an existing block-library folder",
    ).pack(side="left")
    _action_button(
        library_actions, "Create library", create_library, "create",
        "Create and register a new block-library folder",
    ).pack(side="left", padx=(8, 0))

    ttk.Separator(outer, orient="horizontal").grid(
        row=4, column=0, sticky="ew", pady=(2, 2)
    )
    selected_actions = ttk.Frame(outer, padding=(0, 6, 0, 0))
    selected_actions.grid(row=5, column=0, sticky="ew")
    change_button = _action_button(
        selected_actions, "Change folder", change_library_folder, "change",
        "Replace the selected registered folder without moving its files",
    )
    change_button.pack(side="left")
    remove_button = _action_button(
        selected_actions, "Unregister", remove_library, "remove",
        "Remove the selected folder from the registered list; files remain on disk",
    )
    remove_button.pack(side="left", padx=(8, 0))
    default_button = _action_button(
        selected_actions, "Set default", make_default, "default",
        "Use the selected library for newly created blocks",
    )
    default_button.pack(side="left", padx=(8, 0))
    _action_button(
        selected_actions, "Close", dialog.destroy, "close",
        "Close Manage Libraries",
    ).pack(side="right")

    def update_selection_actions(_event=None) -> None:
        path = selected_path()
        fixed = path is None or path == environment_root
        change_button.configure(state="disabled" if fixed else "normal")
        remove_button.configure(state="disabled" if fixed else "normal")
        already_default = path is not None and path == default_library_root()
        default_button.configure(
            state=(
                "disabled"
                if path is None or environment_root is not None or already_default
                else "normal"
            )
        )

    tree.bind("<<TreeviewSelect>>", update_selection_actions)
    render()
    update_selection_actions()
    _set_window_geometry(dialog, 1120, 680, 880, 500)
    dialog.grab_set()
    dialog.wait_window()
    return changed[0]


def _bind_mousewheel(widget, scroll_command, *, include_children: bool = False) -> None:
    """Bind wheel and high-resolution trackpad scrolling to a scrollable pane."""
    remainder = [0.0]

    def on_wheel(event):
        number = getattr(event, "num", None)
        if number == 4:
            amount = -1
        elif number == 5:
            amount = 1
        else:
            try:
                delta = float(getattr(event, "delta", 0) or 0)
            except (TypeError, ValueError):
                delta = 0.0
            if abs(delta) >= 120:
                delta /= 120.0
            remainder[0] -= delta
            amount = int(remainder[0])
            remainder[0] -= amount

        if amount:
            scroll_command(amount)
        return "break"

    def bind_tree(target) -> None:
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            target.bind(sequence, on_wheel, add="+")
        if include_children:
            for child in target.winfo_children():
                bind_tree(child)

    bind_tree(widget)


def _show_library_browser(
    window,
    schematic,
    kicad,
    project_dir: Path,
    library_root: Path | list[Path],
    editor_window_id: str | None,
) -> None:
    """Show every registered block library in one KiCad-themed browser."""
    window.title("Functional Block Library")
    current_roots = [_library_roots_for(library_root)]
    records: list = []
    records_by_id: dict[str, object] = {}

    style = ttk.Style(window)
    palette = getattr(window, "_kicad_blocks_palette", {})
    theme_background = palette.get("background") or style.lookup("TFrame", "background") or window.cget("background")
    theme_foreground = palette.get("foreground") or style.lookup("TLabel", "foreground") or ""
    outer = ttk.Frame(window, padding=10)
    outer.pack(fill="both", expand=True)
    header = ttk.Frame(outer)
    header.pack(fill="x", pady=(0, 8))
    ttk.Label(header, text="Functional Block Library").pack(side="left")
    library_path = tk.StringVar()
    ttk.Label(header, textvariable=library_path).pack(side="right", padx=(12, 0))

    panes = ttk.Panedwindow(outer, orient="horizontal")
    panes.pack(fill="both", expand=True)
    left = ttk.Frame(panes, padding=(0, 0, 8, 0))
    right = ttk.Frame(panes, padding=(8, 0, 0, 0))
    panes.add(left, weight=3)
    panes.add(right, weight=4)

    left.columnconfigure(0, weight=1)
    left.rowconfigure(2, weight=1)
    ttk.Label(left, text="Search blocks").grid(row=0, column=0, sticky="w")
    search = tk.StringVar()
    search_entry = ttk.Entry(left, textvariable=search)
    search_entry.grid(row=1, column=0, sticky="ew", pady=(4, 8))
    _add_tooltip(search_entry, "Filter blocks · ↑/↓ browse · Enter place")
    tree_frame = ttk.Frame(left)
    tree_frame.grid(row=2, column=0, sticky="nsew")
    tree_frame.columnconfigure(0, weight=1)
    tree_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        tree_frame,
        columns=("block", "library", "pins", "variants", "layouts"),
        show="headings",
        selectmode="browse",
    )
    tree.heading("block", text="Block")
    tree.heading("library", text="Library")
    tree.heading("pins", text="I/O")
    tree.heading("variants", text="Variants")
    tree.heading("layouts", text="PCB")
    tree.column("block", width=165, minwidth=140, stretch=True)
    tree.column("library", width=110, minwidth=90, stretch=False)
    tree.column("pins", width=44, minwidth=40, stretch=False, anchor="center")
    tree.column("variants", width=78, minwidth=72, stretch=False, anchor="center")
    tree.column("layouts", width=52, minwidth=48, stretch=False, anchor="center")
    tree_y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
    tree_x_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview, style="Horizontal.TScrollbar")
    tree.configure(
        yscrollcommand=tree_y_scroll.set,
        xscrollcommand=tree_x_scroll.set,
    )
    tree.grid(row=0, column=0, sticky="nsew")
    tree_y_scroll.grid(row=0, column=1, sticky="ns")
    tree_x_scroll.grid(row=1, column=0, sticky="ew")
    _bind_mousewheel(tree, lambda amount: tree.yview_scroll(amount, "units"))

    right.columnconfigure(0, weight=1)
    right.rowconfigure(2, weight=2)
    right.rowconfigure(3, weight=1)
    name_label = ttk.Label(right, text="Select a block")
    name_label.grid(row=0, column=0, sticky="ew")
    description_label = ttk.Label(
        right, text="", wraplength=560, justify="left"
    )
    description_label.grid(row=1, column=0, sticky="ew", pady=(4, 8))

    preview_box = ttk.LabelFrame(right, text="Preview", padding=5)
    preview_box.grid(row=2, column=0, sticky="nsew")
    preview_notebook = ttk.Notebook(preview_box)
    preview_notebook.pack(fill="both", expand=True)

    facade_page = ttk.Frame(preview_notebook)
    preview_notebook.add(facade_page, text="Facade")
    facade_frame = ttk.Frame(facade_page)
    facade_frame.pack(fill="both", expand=True)
    facade_frame.columnconfigure(0, weight=1)
    facade_frame.rowconfigure(0, weight=1)
    preview = tk.Canvas(
        facade_frame,
        height=180,
        background=theme_background,
        highlightthickness=0,
        borderwidth=0,
    )
    preview_y_scroll = ttk.Scrollbar(
        facade_frame, orient="vertical", command=preview.yview, style="Vertical.TScrollbar"
    )
    preview_x_scroll = ttk.Scrollbar(
        facade_frame, orient="horizontal", command=preview.xview, style="Horizontal.TScrollbar"
    )
    preview.configure(
        yscrollcommand=preview_y_scroll.set,
        xscrollcommand=preview_x_scroll.set,
    )
    preview.grid(row=0, column=0, sticky="nsew")
    preview_y_scroll.grid(row=0, column=1, sticky="ns")
    preview_x_scroll.grid(row=1, column=0, sticky="ew")
    _bind_mousewheel(preview, lambda amount: preview.yview_scroll(amount, "units"))

    schematic_page = ttk.Frame(preview_notebook)
    preview_notebook.add(schematic_page, text="Schematic")
    schematic_page.columnconfigure(0, weight=1)
    schematic_page.rowconfigure(1, weight=1)
    schematic_controls = ttk.Frame(schematic_page)
    schematic_controls.grid(row=0, column=0, sticky="ew", pady=(0, 5))
    ttk.Label(schematic_controls, text="Circuit").pack(side="left")
    implementation_choice = tk.StringVar(value="Default")
    implementation_selector = ttk.Combobox(
        schematic_controls,
        textvariable=implementation_choice,
        values=("Default",),
        state="disabled",
        width=22,
    )
    implementation_selector.pack(side="left", padx=(5, 0))
    _add_tooltip(implementation_selector, "Choose the implementation shown in the schematic preview")
    page_choice = tk.StringVar()
    page_selector_label = ttk.Label(schematic_controls, text="Sheet")
    page_selector = ttk.Combobox(
        schematic_controls,
        textvariable=page_choice,
        values=(),
        state="disabled",
        width=22,
    )
    schematic_frame = ttk.Frame(schematic_page)
    schematic_frame.grid(row=1, column=0, sticky="nsew")
    schematic_frame.columnconfigure(0, weight=1)
    schematic_frame.rowconfigure(0, weight=1)
    schematic_preview = tk.Canvas(
        schematic_frame,
        height=230,
        background=theme_background,
        highlightthickness=0,
        borderwidth=0,
    )
    schematic_y_scroll = ttk.Scrollbar(
        schematic_frame,
        orient="vertical",
        command=schematic_preview.yview,
        style="Vertical.TScrollbar",
    )
    schematic_x_scroll = ttk.Scrollbar(
        schematic_frame,
        orient="horizontal",
        command=schematic_preview.xview,
        style="Horizontal.TScrollbar",
    )
    schematic_preview.configure(
        yscrollcommand=schematic_y_scroll.set,
        xscrollcommand=schematic_x_scroll.set,
    )
    schematic_preview.grid(row=0, column=0, sticky="nsew")
    schematic_y_scroll.grid(row=0, column=1, sticky="ns")
    schematic_x_scroll.grid(row=1, column=0, sticky="ew")
    _bind_mousewheel(schematic_preview, lambda amount: schematic_preview.yview_scroll(amount, "units"))

    details_box = ttk.LabelFrame(right, text="Block overview", padding=5)
    details_box.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
    details_frame = ttk.Frame(details_box)
    details_frame.pack(fill="both", expand=True)
    details = tk.Text(
        details_frame,
        height=12,
        wrap="word",
        relief="flat",
        borderwidth=0,
        background=theme_background,
        foreground=theme_foreground,
        font=("TkDefaultFont", 9),
    )
    details_scroll = ttk.Scrollbar(
        details_frame, orient="vertical", command=details.yview, style="Vertical.TScrollbar"
    )
    details.configure(yscrollcommand=details_scroll.set)
    details.pack(side="left", fill="both", expand=True)
    details_scroll.pack(side="right", fill="y")
    details.configure(state="disabled")
    _bind_mousewheel(details, lambda amount: details.yview_scroll(amount, "units"))
    selected_record = [None]
    selected_project_catalog = [None]
    schematic_pages = [[]]
    implementation_choices = [{}]
    facade_button = [None]
    open_layout_button = [None]

    def draw_facade_for_selection():
        record = selected_record[0]
        if record is None:
            preview.delete("all")
            return
        try:
            _draw_facade_preview(
                preview,
                record,
                library_file=selected_project_catalog[0],
            )
        except Exception as exc:
            LOG.exception("Could not render facade preview for %s", record.name)
            preview.delete("all")
            style = ttk.Style(preview)
            preview.create_text(
                max(int(preview.winfo_width()), 480) / 2,
                max(int(preview.winfo_height()), 220) / 2,
                text=f"Facade preview unavailable\n{exc}",
                fill=style.lookup("TLabel", "foreground") or preview.cget("foreground"),
                justify="center",
                width=max(int(preview.winfo_width()), 480) - 32,
            )

    def render_schematic_for_selection():
        record = selected_record[0]
        if record is None:
            schematic_preview.delete("all")
            schematic_preview.create_text(
                max(int(schematic_preview.winfo_width()), 480) / 2,
                max(int(schematic_preview.winfo_height()), 240) / 2,
                text="Select a block to preview its implementation.",
                fill=theme_foreground or schematic_preview.cget("foreground"),
                justify="center",
                width=max(int(schematic_preview.winfo_width()), 480) - 32,
            )
            schematic_pages[0] = []
            return

        selector = implementation_choices[0].get(implementation_choice.get(), "")
        try:
            width = max(720, min(1200, int(schematic_preview.winfo_width())))
            pages = _native_schematic_preview_pages(
                record,
                selector,
                width=width,
            )
            schematic_pages[0] = pages
            page_labels = [item[0] for item in pages]
            page_selector.configure(
                values=page_labels,
                state="readonly" if len(page_labels) > 1 else "disabled",
            )
            if len(page_labels) > 1:
                if page_selector_label.winfo_manager() != "pack":
                    page_selector_label.pack(side="left", padx=(10, 4))
                if page_selector.winfo_manager() != "pack":
                    page_selector.pack(side="left")
            else:
                page_selector_label.pack_forget()
                page_selector.pack_forget()
            page_choice.set(page_labels[0] if page_labels else "")
            if pages:
                _draw_schematic_preview(
                    schematic_preview,
                    pages,
                    page_choice.get(),
                )
        except Exception as exc:
            LOG.exception(
                "Could not render implementation preview for %s",
                record.name,
            )
            schematic_pages[0] = []
            page_selector.configure(values=(), state="disabled")
            page_selector_label.pack_forget()
            page_selector.pack_forget()
            schematic_preview.delete("all")
            style = ttk.Style(schematic_preview)
            schematic_preview.create_text(
                max(int(schematic_preview.winfo_width()), 480) / 2,
                max(int(schematic_preview.winfo_height()), 240) / 2,
                text=f"Schematic preview unavailable\n{exc}",
                fill=style.lookup("TLabel", "foreground") or schematic_preview.cget("foreground"),
                justify="center",
                width=max(int(schematic_preview.winfo_width()), 480) - 32,
            )
            schematic_preview.configure(
                scrollregion=(
                    0,
                    0,
                    max(int(schematic_preview.winfo_width()), 480),
                    max(int(schematic_preview.winfo_height()), 240),
                )
            )

    def set_details(record):
        selected_record[0] = record
        selected_project_catalog[0] = None
        schematic_pages[0] = []
        if record is None:
            name_label.configure(
                text="No matching blocks" if search.get().strip() else "No blocks in registered libraries"
            )
            description_label.configure(text="")
            details.configure(state="normal")
            details.delete("1.0", "end")
            details.configure(state="disabled")
            preview.delete("all")
            schematic_preview.delete("all")
            implementation_selector.configure(values=(), state="disabled")
            implementation_choice.set("")
            implementation_choices[0] = {}
            page_selector.configure(values=(), state="disabled")
            page_choice.set("")
            page_selector_label.pack_forget()
            page_selector.pack_forget()
            return

        name_label.configure(text=record.name)
        description = " ".join(
            str(record.manifest.get("description", "") or "No description provided.").split()
        )
        if len(description) > 150:
            description = description[:147].rstrip() + "…"
        description_label.configure(
            text=f"{description}  ·  {record.path.parent.name}"
        )
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.tag_configure(
            "section",
            foreground=palette.get("accent", "#4c789f"),
            font=("TkDefaultFont", 9, "bold"),
            spacing1=8,
            spacing3=2,
        )
        overview = _block_overview_text(record)
        details.insert("1.0", overview)
        for line_number, line in enumerate(overview.splitlines(), start=1):
            if line.startswith((
                "Connections",
                "Parameters",
                "Circuit implementations",
                "PCB layouts",
                "Profiles",
            )):
                details.tag_add("section", f"{line_number}.0", f"{line_number}.end")
        details.configure(state="disabled")

        default_variant = str(record.manifest.get("default_variant") or "").strip()
        default_label = "Default" + (f" · {default_variant}" if default_variant else "")
        choices = {default_label: ""}
        for item in record.manifest.get("variants", []):
            label = str(item.get("value") or "").strip()
            if label:
                choices[label] = label
        implementation_choices[0] = choices
        implementation_selector.configure(
            values=tuple(choices),
            state="readonly",
        )
        implementation_choice.set(default_label)
        page_selector.configure(values=(), state="disabled")
        page_choice.set("")
        page_selector_label.pack_forget()
        page_selector.pack_forget()

        try:
            selected_project_catalog[0] = _ensure_project_facade_catalog(
                project_dir, record, kicad
            )
        except (OSError, RuntimeError, ValueError, LookupError) as exc:
            LOG.exception(
                "Could not resolve the project facade catalog for %s",
                record.name,
            )
            selected_project_catalog[0] = None
            preview.delete("all")
            style = ttk.Style(preview)
            preview.create_text(
                max(int(preview.winfo_width()), 480) / 2,
                max(int(preview.winfo_height()), 220) / 2,
                text=f"Project facade unavailable\n{exc}",
                fill=style.lookup("TLabel", "foreground") or preview.cget("foreground"),
                justify="center",
                width=max(int(preview.winfo_width()), 480) - 32,
            )

        if preview_notebook.index(preview_notebook.select()) == 0:
            draw_facade_for_selection()
        else:
            render_schematic_for_selection()

    def preview_tab_changed(_event=None):
        if preview_notebook.index(preview_notebook.select()) == 0:
            draw_facade_for_selection()
        else:
            render_schematic_for_selection()

    def implementation_changed(_event=None):
        render_schematic_for_selection()

    def sheet_changed(_event=None):
        _draw_schematic_preview(
            schematic_preview,
            schematic_pages[0],
            page_choice.get(),
        )

    implementation_selector.bind("<<ComboboxSelected>>", implementation_changed)
    page_selector.bind("<<ComboboxSelected>>", sheet_changed)
    preview_notebook.bind("<<NotebookTabChanged>>", preview_tab_changed)

    def render_records(*_args):
        visible = _filter_library_records(records, search.get())
        preferred = _preferred_library_selection(visible, selected_record[0])
        tree.delete(*tree.get_children())
        records_by_id.clear()
        for index, record in enumerate(visible):
            block_id = str(record.manifest["block_id"])
            row_id = f"{block_id}:{index}"
            records_by_id[row_id] = record
            tree.insert(
                "",
                "end",
                iid=row_id,
                values=(
                    record.name,
                    record.path.parent.name,
                    len(record.manifest.get("pins", [])),
                    len(record.manifest.get("variants", [])),
                    len(_layout_choices(record)),
                ),
                tags=("even" if index % 2 == 0 else "odd",),
            )
        library_path.set(
            f"{len(current_roots[0])} libraries  ·  {len(visible)} of {len(records)} blocks"
        )
        if preferred is not None:
            preferred_id = next(
                (key for key, value in records_by_id.items() if value is preferred),
                None,
            )
            if preferred_id is not None:
                tree.selection_set(preferred_id)
                tree.focus(preferred_id)
        set_details(preferred)

    def refresh_records():
        try:
            current_roots[0] = _library_roots_for(configured_library_roots())
            for root_path in current_roots[0]:
                root_path.mkdir(parents=True, exist_ok=True)
            refreshed = _list_library_records(current_roots[0])
        except Exception as exc:
            LOG.exception("Could not refresh registered block libraries")
            messagebox.showerror(
                "Refresh Block Library",
                f"Could not read registered libraries.\n\n{exc}",
                parent=window,
            )
            return False
        records[:] = refreshed
        render_records()
        return True

    def manage_libraries():
        _show_library_roots_dialog(window)
        refresh_records()

    def selection_changed(_event=None):
        selection = tree.selection()
        record = records_by_id.get(selection[0]) if selection else None
        set_details(record)
        if open_layout_button[0] is not None:
            open_layout_button[0].configure(
                state="normal" if record is not None and _layout_choices(record) else "disabled"
            )
        if facade_button[0] is not None:
            facade_button[0].configure(
                state=(
                    "normal"
                    if record is not None and selected_project_catalog[0] is not None
                    else "disabled"
                )
            )

    def place_selected(_event=None):
        record = selected_record[0]
        if record is None:
            return
        try:
            _register_block(project_dir, record, kicad, current_roots[0])
            if not _LAST_LIBRARY_RELOAD_OK:
                messagebox.showwarning(
                    "Block library saved",
                    _LAST_LIBRARY_RELOAD_WARNING or "Restart Eeschema to refresh the project library table.",
                    parent=window,
                )
                return
            minimized_windows = _minimize_placement_windows(window)
            try:
                _start_native_symbol_placement(
                    kicad, None, str(record.manifest["symbol_name"])
                )
            except Exception:
                _restore_placement_windows(minimized_windows)
                raise
        except Exception as exc:
            LOG.exception("Native block placement handoff failed")
            messagebox.showerror("Place Block", str(exc), parent=window)

    def open_implementation(_event=None):
        record = selected_record[0]
        if record is None:
            return
        try:
            _register_implementation_catalog(record, current_roots[0])
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Open implementation", str(exc), parent=window)
            return
        choices = [
            {
                "label": "Default — " + resolve_variant_implementation(record.manifest, ""),
                "file": resolve_variant_implementation(record.manifest, ""),
            },
            *[
                {"label": str(item["value"]), "file": str(item["file"])}
                for item in record.manifest.get("variants", [])
            ],
        ]
        if len(choices) == 1:
            try:
                _launch_block_asset(
                    kicad,
                    record.path / choices[0]["file"],
                    "implementation schematic",
                    editor_window_id=editor_window_id,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror("Open implementation", str(exc), parent=window)
            return
        chooser = tk.Toplevel(window)
        chooser.title(f"Open implementation — {record.name}")
        chooser.transient(window)
        chooser.grab_set()
        frame = ttk.Frame(chooser, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Select circuit implementation:").pack(anchor="w")
        choice = tk.StringVar(value=choices[0]["label"])
        combo = ttk.Combobox(
            frame,
            textvariable=choice,
            values=[item["label"] for item in choices],
            state="readonly",
            width=42,
        )
        combo.pack(fill="x", pady=8)

        def launch_variant():
            selected = next(
                (item for item in choices if item["label"] == choice.get()),
                None,
            )
            if selected is None:
                return
            chooser.destroy()
            try:
                _launch_block_asset(
                    kicad,
                    record.path / selected["file"],
                    "implementation schematic",
                    editor_window_id=editor_window_id,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror("Open implementation", str(exc), parent=window)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        _action_button(buttons, "Open", launch_variant, "open", "Open the selected implementation").pack(side="right")
        _action_button(buttons, "Cancel", chooser.destroy, "cancel", "Close without opening an implementation").pack(
            side="right", padx=(0, 6)
        )
        _set_window_geometry(chooser, 500, 190, 440, 160)
        combo.focus_set()

    def edit_facade(_event=None):
        record = selected_record[0]
        if record is None:
            return
        try:
            project_catalog = selected_project_catalog[0]
            if project_catalog is None:
                raise RuntimeError(
                    "The project facade preview is unavailable. Refresh the library and retry Edit Facade."
                )
            _edit_facade_symbol(
                record,
                kicad,
                editor_window_id,
                project_catalog=project_catalog,
                close_browser=window.destroy,
            )
        except (OSError, RuntimeError, ValueError, LookupError, subprocess.SubprocessError) as exc:
            LOG.exception("Could not open generated library facade")
            messagebox.showerror("Edit facade", str(exc), parent=window)

    def manage_layouts(_event=None):
        record = selected_record[0]
        if record is None:
            return
        try:
            created_layout = _show_block_layout_editor(
                record, window, kicad, editor_window_id
            )
            refresh_records()
            if created_layout is not None:
                _launch_block_asset(
                    kicad, created_layout, "PCB layout",
                    editor_window_id=editor_window_id,
                )
        except Exception as exc:
            LOG.exception("Could not update PCB layouts")
            messagebox.showerror("Manage PCB layouts", str(exc), parent=window)

    def edit_variants(_event=None):
        record = selected_record[0]
        if record is None:
            return
        try:
            new_implementation = _show_block_variant_editor(
                record,
                window,
                kicad=kicad,
                editor_window_id=editor_window_id,
            )
            refresh_records()
            if new_implementation is not None:
                _launch_block_asset(
                    kicad,
                    new_implementation,
                    "implementation schematic",
                    editor_window_id=editor_window_id,
                )
        except Exception as exc:
            LOG.exception("Could not update implementation variants")
            messagebox.showerror("Manage implementations", str(exc), parent=window)

    def open_layout(_event=None):
        record = selected_record[0]
        if record is None:
            return
        layouts = _layout_choices(record)
        if not layouts:
            return
        chooser = tk.Toplevel(window)
        chooser.title(f"Open PCB layout — {record.name}")
        chooser.transient(window)
        chooser.grab_set()
        frame = ttk.Frame(chooser, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Select PCB layout:").pack(anchor="w")
        labels = [
            f"{item.get('name', item.get('id'))} [{item.get('id', 'legacy')}]"
            for item in layouts
        ]
        selected_variant = str(record.manifest.get("default_variant") or "").casefold()
        selected_variant_record = next(
            (
                item for item in record.manifest.get("variants", [])
                if str(item.get("value", "")).casefold() == selected_variant
            ),
            None,
        )
        preferred_layout_id = str(
            (selected_variant_record or {}).get("layout_id")
            or record.manifest.get("default_layout")
            or ""
        ).casefold()
        default_label = next(
            (
                labels[index]
                for index, item in enumerate(layouts)
                if str(item.get("id") or "").casefold() == preferred_layout_id
            ),
            labels[0],
        )
        choice = tk.StringVar(value=default_label)
        combo = ttk.Combobox(
            frame, textvariable=choice, values=labels, state="readonly", width=36
        )
        combo.pack(fill="x", pady=8)

        def launch_layout():
            index = labels.index(choice.get()) if choice.get() in labels else -1
            if index < 0:
                return
            target = record.path / str(layouts[index]["file"])
            chooser.destroy()
            try:
                _launch_block_asset(
                    kicad, target, "PCB layout",
                    editor_window_id=editor_window_id,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror("Open PCB layout", str(exc), parent=window)

        def manage_layouts():
            chooser.destroy()
            created_layout = _show_block_layout_editor(
                record, window, kicad, editor_window_id
            )
            refresh_records()
            if created_layout is not None:
                _launch_block_asset(
                    kicad, created_layout, "PCB layout",
                    editor_window_id=editor_window_id,
                )

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        _action_button(buttons, "Manage layouts…", manage_layouts, "layouts", "Manage stored PCB layouts").pack(side="left")
        _action_button(buttons, "Open", launch_layout, "open", "Open the selected PCB layout").pack(side="right")
        _action_button(
            buttons, "Cancel", chooser.destroy, "cancel", "Close without opening a PCB layout",
        ).pack(side="right", padx=(0, 6))
        _set_window_geometry(chooser, 500, 200, 440, 170)
        combo.focus_set()

    search.trace_add("write", render_records)

    def focus_results(_event=None):
        children = tree.get_children()
        if children:
            selection = tree.selection()
            target = selection[0] if selection else children[0]
            tree.selection_set(target)
            tree.focus(target)
            tree.focus_set()
        return "break"

    def focus_search(_event=None):
        search_entry.focus_set()
        search_entry.icursor("end")
        return "break"

    def place_double_clicked(event):
        row_id = tree.identify_row(event.y)
        record = _library_record_for_tree_row(records_by_id, row_id)
        if record is None:
            return "break"
        tree.selection_set(row_id)
        tree.focus(row_id)
        set_details(record)
        place_selected()
        return "break"

    tree.bind("<<TreeviewSelect>>", selection_changed)
    tree.bind("<Double-1>", place_double_clicked)
    tree.bind("<Return>", place_selected)
    tree.bind("<Escape>", focus_search)
    search_entry.bind("<Down>", focus_results)
    preview.bind(
        "<Configure>",
        lambda _event: (
            draw_facade_for_selection()
            if selected_record[0] is not None
            and preview_notebook.index(preview_notebook.select()) == 0
            else None
        ),
    )

    action_toolbar = ttk.Frame(outer, padding=(0, 10, 0, 0))
    action_toolbar.pack(fill="x")
    place_button = _action_button(
        action_toolbar, "Place block", place_selected, "place",
        "Register the selected facade and start KiCad native symbol placement",
    )
    place_button.pack(side="left")
    implementation_button = _action_button(
        action_toolbar, "Implementation", open_implementation, "implementation",
        "Open the base or a named circuit implementation",
    )
    implementation_button.pack(side="left", padx=(6, 0))
    facade_button[0] = _action_button(
        action_toolbar, "Edit facade", edit_facade, "facade",
        "Open the generated library facade in KiCad Symbol Editor",
    )
    facade_button[0].pack(side="left", padx=(6, 0))
    variants_button = _action_button(
        action_toolbar, "Variants", edit_variants, "variants",
        "Add, edit, remove, or set the default circuit implementation",
    )
    variants_button.pack(side="left", padx=(6, 0))
    open_layout_button[0] = _action_button(
        action_toolbar, "Open PCB", open_layout, "pcb",
        "Open one of the block's stored PCB layouts",
    )
    open_layout_button[0].pack(side="left", padx=(6, 0))
    manage_layout_button = _action_button(
        action_toolbar, "PCB layouts", manage_layouts, "layouts",
        "Add and associate PCB layouts with implementations",
    )
    manage_layout_button.pack(side="left", padx=(6, 0))
    refresh_button = _action_button(
        action_toolbar, "Refresh", refresh_records, "refresh",
        "Reload registered libraries and block details",
    )
    refresh_button.pack(side="left", padx=(6, 0))
    libraries_button = _action_button(
        action_toolbar, "Libraries", manage_libraries, "libraries",
        "Register, create, change, or unregister block libraries",
    )
    libraries_button.pack(side="left", padx=(6, 0))
    refresh_records()
    selection_changed()
    _set_window_geometry(window, 1600, 1000, 1200, 720)
    window.bind("<FocusIn>", lambda _event: selection_changed(), add="+")
    search_entry.focus_set()

def run_library_browser() -> None:
    """Launch the all-libraries functional-block browser."""
    run_action("manage-library")



def _field_value(instance, field_name: str) -> str:
    for field in getattr(instance, "user_fields", ()):
        if str(getattr(field, "name", "")) == field_name:
            return str(getattr(getattr(field, "text", None), "value", "") or "").strip()
    return ""


def _sheet_variables_for_symbol(schematic, instance) -> dict[str, str]:
    """Collect custom fields on hierarchical sheet symbols containing this occurrence."""
    sheet_path = list(getattr(getattr(instance, "path", None), "path", ()))
    if not sheet_path:
        return {}
    # A hierarchical sheet's custom fields are scoped to that child sheet,
    # not to every descendant sheet in the hierarchy.
    containing_sheet_id = str(sheet_path[-1])
    variables: dict[str, str] = {}
    try:
        sheet_symbols = schematic.get_sheet_symbols()
    except Exception:
        LOG.exception("Could not read hierarchical sheet-symbol properties")
        return variables
    for sheet_symbol in sheet_symbols:
        identifiers = {
            str(getattr(sheet_symbol, "id", "")),
            str(getattr(sheet_symbol, "uuid", "")),
        }
        if containing_sheet_id not in identifiers:
            continue
        for field in getattr(sheet_symbol, "user_fields", ()):
            name = str(getattr(field, "name", "") or "").strip()
            if name and name not in {"Sheet name", "Sheet file"}:
                variables[name] = str(getattr(getattr(field, "text", None), "value", "") or "")
    return variables


def _resolve_variant_text(raw_value: str, project, sheet_variables: dict[str, str]) -> str:
    """Expand sheet custom fields and normal KiCad project text variables."""
    import re

    token = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    value = str(raw_value or "").strip()
    if not value:
        return ""
    for _ in range(12):
        previous = value

        def replace_sheet(match):
            return sheet_variables.get(match.group(1), match.group(0))

        value = token.sub(replace_sheet, value)
        if hasattr(project, "expand_text_variables"):
            value = project.expand_text_variables(value)
        if value == previous:
            break
    unresolved = token.search(value)
    if unresolved:
        raise ValueError(
            f"Could not resolve KiCad text variable ${unresolved.group(1)} "
            "for this block instance. Define it as a project text variable or "
            "a custom field on the containing sheet symbol."
        )
    return value.strip()


def _open_selected_block_implementation(root, schematic, kicad, project_dir, parent):
    """Open the selected facade's exact variant implementation; never guess a fallback."""
    instance = _selected_symbol(schematic)
    record = _block_record_for_instance(root, project_dir, instance)
    raw_variant = _field_value(instance, VARIANT_FIELD_NAME)
    resolved_variant = _resolve_variant_text(
        raw_variant, schematic.get_project(), _sheet_variables_for_symbol(schematic, instance)
    )
    implementation_name = resolve_variant_implementation(record.manifest, resolved_variant)
    implementation_path = (record.path / implementation_name).resolve()
    if not implementation_path.is_file():
        raise FileNotFoundError(
            f"The implementation for KB_VARIANT={resolved_variant!r} is missing: {implementation_path}"
        )
    _register_implementation_catalog(record, _library_roots_for(root))
    process = _launch_block_asset(
        kicad,
        implementation_path,
        "implementation schematic",
        editor_window_id=getattr(parent, "_kicad_blocks_editor_window_id", None),
    )
    if parent is not None:
        detail = (
            f"Opened {record.name} — variant {resolved_variant}."
            if record.manifest.get("variants")
            else f"Opened {record.name} implementation."
        )
        messagebox.showinfo("Block Implementation", detail, parent=parent)
    return process


def open_selected_implementation_action() -> None:
    """Standalone compatibility entry point for the selected block action."""
    run_action("open-implementation")


def _export_recursive_bom(root: Path | list[Path], parent, schematic=None, project_dir: Path | None = None) -> None:
    parameter_values = None
    selection = _safe_schematic_selection(schematic)
    selected = [
        item for item in selection
        if hasattr(item, "definition") and hasattr(item.definition, "id")
    ]
    if len(selected) == 1:
        record = _block_record_for_instance(root, project_dir, selected[0])
        parameter_values = _parameter_values_for_instance(selected[0], record)
    else:
        record = _pick_record(_list_library_records(root), parent, "Export Recursive Block BOM")
    if record is None:
        return
    dependency_result = synchronize_block_dependencies(
        record, _library_roots_for(root), find_kicad_cli()
    )
    record = dependency_result["block"]
    library_root = record.path.parent
    output = _pick_save_file(
        parent,
        title="Save recursive block BOM",
        initial_path=Path.cwd() / f"{record.manifest['slug']}_bom.csv",
        defaultextension=".csv",
        filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
    )
    if not output:
        return
    result = export_block_bom(
        record, Path(output), find_kicad_cli(), library_root, parameter_values=parameter_values
    )
    messagebox.showinfo(
        "Block BOM Exported",
        f"Exported {result['row_count']} rows from {result['block_count']} block(s):\n"
        f"{result['output']}",
        parent=parent,
    )


def _validate_shared_library(root: Path | list[Path], parent) -> None:
    """Validate every registered library, keeping each report in its own folder."""
    output = _pick_directory(
        parent, title="Select a folder for the library validation reports"
    )
    if not output:
        return
    results = []
    library_roots = _library_roots_for(root)
    cli = find_kicad_cli()
    for library_path in library_roots:
        for block in list_blocks(library_path):
            synchronize_block_dependencies(block, library_roots, cli)
        report_dir = Path(output) / library_path.name
        result = validate_block_library(
            library_path,
            report_dir,
            cli,
            run_erc=True,
        )
        results.append((library_path, result))

    blocks = [item for _path, result in results for item in result["blocks"]]
    passed = sum(result["passed_count"] for _path, result in results)
    warnings = sum(result["warning_count"] for _path, result in results)
    failed = sum(result["failed_count"] for _path, result in results)
    details = []
    for library_path, result in results:
        details.append(f"Library: {library_path}")
        for item in result["blocks"]:
            status = str(item["status"]).upper()
            detail = f"{status}: {item['name']}"
            problems = item.get("errors", []) + item.get("warnings", [])
            if problems:
                detail += "\\n  " + "\\n  ".join(problems)
            details.append(detail)
    message = (
        f"Validated {len(blocks)} block(s) across {len(results)} library/libraries.\\n"
        f"Passed: {passed}  |  Warnings: {warnings}  |  Failed: {failed}\\n\\n"
        + ("\\n\\n".join(details) if details else "The registered libraries contain no block assets.")
        + f"\\n\\nReports: {output}"
    )
    if failed:
        messagebox.showwarning("Block Library Validation — problems found", message, parent=parent)
    elif warnings:
        messagebox.showwarning("Block Library Validation — ERC warnings", message, parent=parent)
    else:
        messagebox.showinfo("Block Library Validation Passed", message, parent=parent)


def _validate_recursive_erc(root: Path | list[Path], parent, schematic=None, project_dir: Path | None = None) -> None:
    parameter_values = None
    selection = _safe_schematic_selection(schematic)
    selected = [
        item for item in selection
        if hasattr(item, "definition") and hasattr(item.definition, "id")
    ]
    if len(selected) == 1:
        record = _block_record_for_instance(root, project_dir, selected[0])
        parameter_values = _parameter_values_for_instance(selected[0], record)
    else:
        record = _pick_record(_list_library_records(root), parent, "Validate Block Tree (ERC)")
    if record is None:
        return
    dependency_result = synchronize_block_dependencies(
        record, _library_roots_for(root), find_kicad_cli()
    )
    record = dependency_result["block"]
    library_root = record.path.parent
    output = _pick_directory(
        parent, title="Select a folder for recursive ERC reports"
    )
    if not output:
        return
    reports = validate_block_tree(
        record, Path(output), find_kicad_cli(), library_root, parameter_values=parameter_values
    )
    failed = [item for item in reports if item["violations"]]
    details = "\n".join(
        f"{item['block']}: {'violations found' if item['violations'] else 'no violations'}"
        for item in reports
    )
    messagebox.showinfo(
        "Recursive Block ERC",
        f"Checked {len(reports)} block implementation(s); "
        f"{len(failed)} reported violations.\n\n{details}\n\nReports: {output}",
        parent=parent,
    )


def _publish_block(root, kicad, project_dir, parent) -> None:
    library_paths = _library_roots_for(root)
    record = _pick_record(_list_library_records(library_paths), parent, "Publish Library Block")
    if record is None:
        return
    project_catalog = Path(project_dir).expanduser().resolve() / ".kicad_blocks" / LIBRARY_CATALOG_NAME
    import_project_catalog_facade_edit(record, project_catalog)
    dependency_result = synchronize_block_dependencies(
        record, library_paths, find_kicad_cli()
    )
    record = dependency_result["block"]
    result = refresh_library_block(record)
    if result["changed"]:
        _register_block(project_dir, record, kicad, library_paths)
    messagebox.showinfo(
        "Library Block Published",
        f"Version: {result['version']}\n"
        f"Changed files: {', '.join(result['changed']) or 'none'}\n"
        f"Automatically detected {dependency_result['count']} child block(s)"
        + ("; dependency manifest updated.\n" if dependency_result["changed"] else ".\n")
        + "Local copies can now synchronize from this revision.",
        parent=parent,
    )


def _block_record_for_instance(library_root: Path | list[Path], project_dir: Path, instance):
    """Resolve the selected instance to its local copy or any registered shared library."""
    local_record = local_copy_for_instance(project_dir, str(instance.id))
    if local_record is not None:
        return local_record
    return _library_record_for_instance(library_root, instance)

def _parse_pin_net_map(value: str, pins: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in value.split(","):
        token = entry.strip()
        if not token:
            continue
        if ":" not in token:
            raise ValueError(f"Invalid mapping {token!r}; use FACADE_PIN:BOARD_NET.")
        pin, net = (part.strip() for part in token.split(":", 1))
        if pin in mapping:
            raise ValueError(f"Facade pin {pin!r} is mapped more than once.")
        mapping[pin] = net
    if set(mapping) != set(pins):
        missing = sorted(set(pins) - set(mapping))
        extra = sorted(set(mapping) - set(pins))
        raise ValueError(
            "Map each facade pin exactly once. "
            f"Missing: {missing}; unknown: {extra}."
        )
    return mapping


def _open_generated_pcb_copy(kicad, board_path: Path):
    """Open only the newly generated board copy in a separate PCB Editor process."""
    board_path = Path(board_path).expanduser().resolve()
    if not board_path.is_file():
        raise FileNotFoundError(f"Generated PCB board does not exist: {board_path}")
    executable = kicad.get_kicad_binary_path("pcbnew")
    if not executable:
        raise RuntimeError("KiCad did not provide a PCB Editor executable path.")
    return subprocess.Popen([executable, str(board_path)], close_fds=True)


def _apply_block_pcb_layout(
    schematic, kicad, project_dir: Path, library_root: Path, parent
) -> None:
    """Create a new project-board copy with the selected block's stored PCB layout."""
    instance = _selected_symbol(schematic)
    block = _block_record_for_instance(library_root, project_dir, instance)
    layout = _choose_block_layout(block, parent, f"PCB Layout — {instance.reference}", instance)
    if layout is None:
        return

    pins = [str(pin["name"]) for pin in block.manifest.get("pins", [])]
    board_path = _pick_kicad_file(
        parent,
        title="Select the existing project PCB board",
        suffix=".kicad_pcb",
    )
    if not board_path:
        return

    default_map = ", ".join(f"{pin}:{pin}" for pin in pins)
    map_text = simpledialog.askstring(
        "Map block facade pins to board nets",
        "Enter one mapping for every facade pin.\n"
        "Format: FACADE_PIN:BOARD_NET, comma-separated.",
        initialvalue=default_map,
        parent=parent,
    )
    if map_text is None:
        return
    pin_map = _parse_pin_net_map(map_text, pins)

    # The merged layout is staged at a predictable point. The PCB Editor's
    # Place Imported Block Layout action moves the complete imported object set
    # to the user's cursor/grid position using KiCad's native interactive tool.
    position = (100.0, 100.0)

    anchor_text = simpledialog.askstring(
        "Stored PCB layout origin",
        "Enter the X,Y coordinate in the stored block layout that should be placed at the selected position.",
        initialvalue="0,0",
        parent=parent,
    )
    if anchor_text is None:
        return
    anchor_values = [part.strip() for part in anchor_text.split(",")]
    if len(anchor_values) != 2:
        raise ValueError("Layout anchor must be two comma-separated millimetre coordinates.")
    try:
        anchor = (float(anchor_values[0]), float(anchor_values[1]))
    except ValueError as exc:
        raise ValueError("Layout anchor coordinates must be numeric millimetres.") from exc

    rotation_text = simpledialog.askstring(
        "PCB block rotation",
        "Enter the block rotation in degrees (positive is counter-clockwise).",
        initialvalue="0",
        parent=parent,
    )
    if rotation_text is None:
        return
    try:
        rotation = float(rotation_text)
    except ValueError as exc:
        raise ValueError("Block rotation must be a numeric degree value.") from exc

    source_board = Path(board_path)
    default_output = (
        source_board.with_name(f"{source_board.stem}_{instance.reference}_blocks.kicad_pcb")
    )
    output_path = _pick_save_file(
        parent,
        title="Save merged PCB board copy",
        initial_path=default_output,
        defaultextension=".kicad_pcb",
        filetypes=[("KiCad PCB", "*.kicad_pcb")],
    )
    if not output_path:
        return

    output_board = Path(output_path).expanduser().resolve()
    placement_sidecar = placement_manifest_path(output_board)
    if placement_sidecar.exists():
        raise FileExistsError(
            f"PCB placement sidecar already exists and will not be overwritten: {placement_sidecar}"
        )
    result = merge_block_layout(
        source_board,
        block,
        output_board,
        instance_reference=str(instance.reference),
        position_mm=position,
        pin_to_board_net=pin_map,
        layout_anchor_mm=anchor,
        rotation_deg=rotation,
        layout_id=layout.get("id"),
    )
    placement_sidecar = write_pcb_placement_manifest(output_board, result)
    opened_in_editor = False
    if messagebox.askyesno(
        "Open merged PCB board",
        "The merged board copy has been created without modifying the original. "
        "Open the generated copy in PCB Editor now?",
        parent=parent,
    ):
        _open_generated_pcb_copy(kicad, Path(result["output"]))
        opened_in_editor = True

    warning = result["skipped_top_level_items"]
    message = (
        f"Created a new board copy:\n{result['output']}\n\n"
        f"Footprints added: {result['footprint_count']}\n"
        f"Board objects added: {result['object_count']}\n"
        f"New internal nets: {', '.join(result['new_nets']) or 'none'}\n"
        f"Rotation applied: {result['rotation_deg']:g} degrees counter-clockwise.\n"
        f"Project settings copy: {result['project_file'] or 'not available'}\n"
        f"Merged netclasses: {', '.join(result['merged_netclasses']) or 'none'}\n"
        f"Updated net assignments: {', '.join(result['assigned_project_nets']) or 'none'}\n"
        f"Resolved netclass patterns: {len(result['applied_project_patterns'])}\n"
        f"PCB net chains added: {', '.join(result['merged_board_chains']) or 'none'}\n\n"
        "The original project board and project settings were not modified. "
        f"\nInteractive placement manifest: {placement_sidecar}\n"
        + (
            "In PCB Editor, choose Place Imported Block Layout and position the imported group with the cursor."
            if opened_in_editor
            else "Open the generated copy in PCB Editor, then choose Place Imported Block Layout to position the imported group with the cursor."
        )
    )
    if warning:
        message += "\n\nSkipped board-level item types: " + ", ".join(warning)
    if result["ignored_project_patterns"]:
        message += (
            "\n\nSome block netclass patterns did not match a net in the stored PCB "
            "or used unsupported bus syntax: "
            + (", ".join(result["unmatched_project_patterns"]) or "unknown")
        )
    if result["ignored_project_chain_classes"]:
        message += (
            "\n\nSome project net-chain settings could not be mapped to an actual chain "
            "in the stored PCB: "
            + (", ".join(result["unmatched_project_chains"]) or "unknown")
        )
    messagebox.showinfo("PCB Block Layout Applied", message, parent=parent)


def _apply_selected_block_pcb_layouts(
    schematic, kicad, project_dir: Path, library_root: Path, parent
) -> None:
    """Batch-apply the layouts for all selected block facade instances."""
    instances = _selected_symbols(schematic)
    if len(instances) < 2:
        raise ValueError("Select at least two block symbols for batch PCB layout application.")

    placements: list[BlockLayoutPlacement] = []
    for index, instance in enumerate(instances, start=1):
        block = _block_record_for_instance(library_root, project_dir, instance)
        layout = _choose_block_layout(block, parent, f"PCB Layout — {instance.reference}", instance)
        if layout is None:
            return
        pins = [str(pin["name"]) for pin in block.manifest.get("pins", [])]
        mapping_text = simpledialog.askstring(
            f"PCB nets — {instance.reference}",
            f"Map every facade pin of {block.name} to a net in the project board.\n"
            "Format: FACADE_PIN:BOARD_NET, comma-separated.",
            initialvalue=", ".join(f"{pin}:{pin}" for pin in pins),
            parent=parent,
        )
        if mapping_text is None:
            return
        pin_map = _parse_pin_net_map(mapping_text, pins)

        # Stagger batch imports for visibility before each is moved at cursor
        # from the PCB Editor action. This is only a staging coordinate.
        position = (100.0 + (index - 1) * 80.0, 100.0)

        anchor_text = simpledialog.askstring(
            f"PCB anchor — {instance.reference}",
            "Coordinate X,Y of the anchor in the stored block layout:",
            initialvalue="0,0",
            parent=parent,
        )
        if anchor_text is None:
            return
        anchor_parts = [part.strip() for part in anchor_text.split(",")]
        if len(anchor_parts) != 2:
            raise ValueError(f"Anchor for {instance.reference} must be X,Y.")
        try:
            anchor = (float(anchor_parts[0]), float(anchor_parts[1]))
        except ValueError as exc:
            raise ValueError(f"Anchor for {instance.reference} must be numeric.") from exc

        rotation_text = simpledialog.askstring(
            f"PCB rotation — {instance.reference}",
            "Counter-clockwise rotation in degrees:",
            initialvalue="0",
            parent=parent,
        )
        if rotation_text is None:
            return
        try:
            rotation = float(rotation_text)
        except ValueError as exc:
            raise ValueError(f"Rotation for {instance.reference} must be numeric.") from exc

        placements.append(
            BlockLayoutPlacement(
                block=block,
                instance_reference=str(instance.reference),
                position_mm=position,
                pin_to_board_net=pin_map,
                layout_anchor_mm=anchor,
                rotation_deg=rotation,
                layout_id=layout.get("id"),
            )
        )

    board_path = _pick_kicad_file(
        parent,
        title="Select the existing project PCB board",
        suffix=".kicad_pcb",
    )
    if not board_path:
        return
    source_board = Path(board_path)
    default_output = source_board.with_name(
        f"{source_board.stem}_{len(placements)}blocks.kicad_pcb"
    )
    output_path = _pick_save_file(
        parent,
        title="Save combined PCB block layout copy",
        initial_path=default_output,
        defaultextension=".kicad_pcb",
        filetypes=[("KiCad PCB", "*.kicad_pcb")],
    )
    if not output_path:
        return

    output_board = Path(output_path).expanduser().resolve()
    placement_sidecar = placement_manifest_path(output_board)
    if placement_sidecar.exists():
        raise FileExistsError(
            f"PCB placement sidecar already exists and will not be overwritten: {placement_sidecar}"
        )
    result = merge_block_layouts(source_board, placements, output_board)
    placement_sidecar = write_pcb_placement_manifest(output_board, result)
    if messagebox.askyesno(
        "Open combined PCB board",
        f"Applied {result['block_count']} block layouts to a separate board copy.\n"
        "Open that output copy in PCB Editor now?",
        parent=parent,
    ):
        _open_generated_pcb_copy(kicad, Path(result["output"]))

    messagebox.showinfo(
        "PCB Block Layouts Applied",
        f"Created board copy: {result['output']}\n"
        f"Block instances: {result['block_count']}\n"
        f"Footprints added: {result['footprint_count']}\n"
        f"Board objects added: {result['object_count']}\n"
        f"Internal nets added: {', '.join(result['new_nets']) or 'none'}\n"
        f"PCB net chains added: {', '.join(result['merged_board_chains']) or 'none'}\n"
        f"Interactive placement manifest: {placement_sidecar}\n"
        "Open the output in PCB Editor and choose Place Imported Block Layout once for each block instance. "
        "The original board and project settings were not modified.",
        parent=parent,
    )


def _show_action_hub(root):
    """Show compact, icon-led task actions with secondary guidance in tooltips."""
    _configure_tk_theme(root)
    root.title("KiCad Blocks — Tasks")

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill="both", expand=True)
    ttk.Label(
        outer,
        text="KiCad Blocks",
        font=("TkDefaultFont", 16, "bold"),
    ).pack(anchor="w", pady=(0, 10))

    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)

    groups = [
        (
            "Use & build",
            [
                (
                    "Browse and place blocks",
                    "Browse registered libraries, configure a facade, and start native placement.",
                    "manage-library",
                    "libraries",
                ),
                (
                    "Create block from sheet",
                    "Select a hierarchical sheet symbol, or create from the currently open sheet.",
                    "create-block",
                    "create",
                ),
                (
                    "Open block implementation",
                    "Open the implementation for one selected block instance.",
                    "open-implementation",
                    "implementation",
                ),
            ],
        ),
        (
            "Edit & maintain",
            [
                (
                    "Configure selected instance",
                    "Edit the profile, variant, parameters, or PCB layout for one instance.",
                    "configure-instance",
                    "configure",
                ),
                (
                    "Edit facade symbol",
                    "Edit the generated library facade for the highlighted block.",
                    "edit-facade",
                    "facade",
                ),
                (
                    "Publish implementation",
                    "Publish the current library implementation and update dependent metadata.",
                    "publish-block",
                    "publish",
                ),
                (
                    "Make selected instance local",
                    "Create an independent project-local copy of the selected shared block.",
                    "make-local-copy",
                    "local",
                ),
                (
                    "Synchronize local copy",
                    "Reconcile the selected local implementation with its shared-library source.",
                    "synchronize-local-copy",
                    "sync",
                ),
            ],
        ),
        (
            "Checks & PCB",
            [
                (
                    "Validate registered libraries",
                    "Validate manifests, assets, dependencies, revisions, and recursive ERC.",
                    "validate-libraries",
                    "validate",
                ),
                (
                    "Export recursive BOM",
                    "Export the bill of materials for one block and its dependencies.",
                    "recursive-bom",
                    "bom",
                ),
                (
                    "Run recursive ERC",
                    "Run electrical-rule checks for one implementation and its dependencies.",
                    "recursive-erc",
                    "erc",
                ),
                (
                    "Apply selected PCB layout",
                    "Create a merged board copy from one selected block layout.",
                    "apply-pcb-layout",
                    "pcb",
                ),
                (
                    "Apply multiple PCB layouts",
                    "Create one combined board copy from multiple selected block layouts.",
                    "apply-multiple-pcb-layouts",
                    "layouts",
                ),
            ],
        ),
    ]

    def dispatch(selected_action: str) -> None:
        root.destroy()
        run_action(selected_action)

    for tab_name, actions in groups:
        tab = ttk.Frame(tabs, padding=(12, 10))
        tabs.add(tab, text=tab_name)
        for row, (title, description, action_id, icon_name) in enumerate(actions):
            cell = ttk.Frame(tab)
            cell.grid(row=row, column=0, sticky="ew", pady=3)
            button = _action_button(
                cell,
                title,
                command=lambda selected=action_id: dispatch(selected),
                icon=icon_name,
                tooltip=description,
            )
            button.pack(fill="x", expand=True)
        tab.columnconfigure(0, weight=1)

    footer = ttk.Frame(outer)
    footer.pack(fill="x", pady=(10, 0))
    _action_button(footer, "Close", root.destroy, "close", "Close the task hub").pack(side="right")
    _set_window_geometry(root, 820, 580, 680, 480)
    return tabs


def run_action(action: str, *, editor_window_id: str | None = None) -> None:
    """Run one operation from the task hub or KiCad's PCB Editor action."""
    # Older installations may still invoke this identifier; route it to the
    # one library-browser selection and placement workflow.
    if action == "place-block":
        action = "manage-library"

    valid_actions = {
        "open-hub",
        "manage-library",
        "create-block",
        "open-implementation",
        "configure-instance",
        "make-local-copy",
        "synchronize-local-copy",
        "edit-facade",
        "publish-block",
        "recursive-bom",
        "recursive-erc",
        "validate-libraries",
        "apply-pcb-layout",
        "apply-multiple-pcb-layouts",
    }
    if action not in valid_actions:
        raise ValueError(f"Unknown KiCad Blocks action: {action!r}")

    # KiCad IPC is bound to the editor instance that launched this action.
    # No native cross-process window handle is needed for these operations.
    editor_window_id = None
    _load_tkinter()
    root = tk.Tk()
    _configure_tk_theme(root)
    root.title("KiCad Blocks — Tasks" if action == "open-hub" else "KiCad Blocks")
    _attach_tk_window_to_editor(root, editor_window_id)
    try:
        if action == "open-hub":
            _show_action_hub(root)
            root.mainloop()
            return

        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise ValueError("No active schematic was found. Open and save a project first.")
        project_dir = project_directory(schematic)
        library_paths = configured_library_roots()
        library_root = default_library_root()
        library_root.mkdir(parents=True, exist_ok=True)

        if action == "manage-library":
            _show_library_browser(
                root, schematic, kicad, project_dir, library_paths, editor_window_id
            )
            root.mainloop()
            return

        root.withdraw()
        if action == "create-block":
            _create_and_place(
                library_root, schematic, kicad, project_dir, root, editor_window_id
            )
        elif action == "open-implementation":
            _open_selected_block_implementation(
                library_paths, schematic, kicad, project_dir, root
            )
        elif action == "configure-instance":
            _show_selected_block_configuration(
                schematic, project_dir, library_paths, root
            )
        elif action == "make-local-copy":
            path = make_selected_local(
                schematic, kicad, project_dir, library_paths
            )
            messagebox.showinfo(
                "Local Copy Created",
                f"The selected instance now uses an independent project-local block:\n{path}",
                parent=root,
            )
        elif action == "synchronize-local-copy":
            result = sync_selected_local(schematic, kicad, project_dir)
            messagebox.showinfo(
                "Local Block Synchronized",
                "Published local edits: " + (", ".join(result["published"]) or "none") +
                "\\nPulled library edits: " + (", ".join(result["pulled"]) or "none") +
                "\\nConverged edits: " + (", ".join(result["converged"]) or "none"),
                parent=root,
            )
        elif action == "edit-facade":
            record = _pick_record(
                _list_library_records(library_paths), root, "Edit Facade"
            )
            if record is not None:
                project_catalog = _ensure_project_facade_catalog(
                    project_dir, record, kicad
                )
                _edit_facade_symbol(
                    record,
                    kicad,
                    project_catalog=project_catalog,
                    close_browser=root.destroy,
                )
                messagebox.showinfo(
                    "Edit facade",
                    "Symbol Editor handoff requested. In its library tree, search for "
                    + f"{LIBRARY_NICKNAME}:{record.manifest['symbol_name']}"
                    + " and select the result. Saved facade edits are imported by Publish implementation.",
                )
        elif action == "publish-block":
            _publish_block(library_paths, kicad, project_dir, root)
        elif action == "recursive-bom":
            _export_recursive_bom(library_paths, root, schematic, project_dir)
        elif action == "recursive-erc":
            _validate_recursive_erc(library_paths, root, schematic, project_dir)
        elif action == "validate-libraries":
            _validate_shared_library(library_paths, root)
        elif action == "apply-pcb-layout":
            _apply_block_pcb_layout(
                schematic, kicad, project_dir, library_paths, root
            )
        elif action == "apply-multiple-pcb-layouts":
            _apply_selected_block_pcb_layouts(
                schematic, kicad, project_dir, library_paths, root
            )
    except (ValueError, LookupError, FileNotFoundError, PermissionError) as exc:
        LOG.info("KiCad Blocks action needs user input or context (%s): %s", action, exc)
        messagebox.showwarning(
            "Action needs attention",
            f"{exc}\n\nReview the task description and the required selection or project context.",
            parent=root,
        )
    except Exception as exc:
        LOG.exception("KiCad Blocks action failed: %s", action)
        messagebox.showerror(
            "KiCad Blocks",
            f"The action could not be completed: {exc}",
            parent=root,
        )
    finally:
        with contextlib.suppress(tk.TclError):
            root.destroy()


def run() -> None:
    """Compatibility entry point for older manager installations."""
    run_action("manage-library")
