"""KiCad IPC actions for opaque, library-backed schematic block symbols."""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The core block model and IPC helpers must remain importable in headless
# environments. Load Tkinter only when the interactive manager is invoked.
tk = None
ttk = None
filedialog = None
messagebox = None
simpledialog = None

from .composition import compose_block_implementation
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
    LIBRARY_NICKNAME,
    add_dependency,
    add_library_root,
    build_project_symbol_catalog,
    create_block,
    default_library_root,
    ensure_stable_block_identity,
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


def _system_prefers_dark_theme() -> bool:
    """Detect the desktop dark appearance, with a KiCad Blocks override."""
    override = os.environ.get("KICAD_BLOCKS_THEME", "system").strip().casefold()
    if override in {"dark", "true", "1", "yes", "on"}:
        return True
    if override in {"light", "false", "0", "no", "off"}:
        return False

    if sys.platform == "win32":
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        except (ImportError, OSError, ValueError):
            return False

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            return result.returncode == 0 and result.stdout.strip().casefold() == "dark"
        except (OSError, subprocess.TimeoutExpired):
            return False

    if sys.platform.startswith("linux"):
        gtk_theme = os.environ.get("GTK_THEME", "")
        if gtk_theme:
            return "dark" in gtk_theme.casefold()

        for schema, key in (
            ("org.gnome.desktop.interface", "color-scheme"),
            ("org.gnome.desktop.interface", "gtk-theme"),
        ):
            try:
                result = subprocess.run(
                    ["gsettings", "get", schema, key],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                value = result.stdout.strip().strip("'").strip('"').casefold()
                if key == "color-scheme" and value in {"prefer-dark", "dark"}:
                    return True
                if key == "color-scheme" and value in {"prefer-light", "light"}:
                    return False
                if key == "gtk-theme" and "dark" in value:
                    return True

        kdeglobals = Path.home() / ".config" / "kdeglobals"
        try:
            in_general = False
            for line in kdeglobals.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("["):
                    in_general = line.casefold() == "[general]"
                elif in_general and line.casefold().startswith("colorscheme="):
                    return "dark" in line.split("=", 1)[1].casefold()
        except OSError:
            pass

    return False


def _configure_tk_theme(root) -> None:
    """Apply a compact KiCad-compatible palette to themed and classic Tk widgets."""
    dark = _system_prefers_dark_theme()
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
    style.configure("TLabelFrame", background=colors["background"], foreground=colors["foreground"],
                    bordercolor=colors["border"], relief="solid", borderwidth=1)
    style.configure("TLabelFrame.Label", background=colors["background"], foreground=colors["muted"], padding=(4, 0))
    style.configure("TButton", background=colors["surface_alt"], foreground=colors["foreground"],
                    bordercolor=colors["border"], focuscolor=colors["accent"], focusthickness=1,
                    padding=(11, 7), relief="flat")
    style.map("TButton",
              background=[("disabled", colors["surface"]), ("pressed", colors["selected"]), ("active", colors["hover"])],
              foreground=[("disabled", colors["muted"]), ("!disabled", colors["foreground"])],
              bordercolor=[("focus", colors["accent"]), ("active", colors["accent"]), ("!focus", colors["border"])])
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
                    rowheight=29, relief="flat", borderwidth=0, padding=(4, 2))
    style.map("Treeview", background=[("selected", colors["selected"])],
              foreground=[("selected", colors["foreground"])])
    style.configure("Treeview.Heading", background=colors["surface_alt"], foreground=colors["muted"],
                    bordercolor=colors["border"], lightcolor=colors["surface_alt"],
                    darkcolor=colors["border"], relief="flat", padding=(8, 8),
                    font=("TkDefaultFont", 9, "bold"))
    style.map("Treeview.Heading", background=[("active", colors["hover"])],
              foreground=[("active", colors["foreground"])])
    style.configure("TNotebook", background=colors["background"], borderwidth=0, tabmargins=(2, 4, 2, 0))
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
    """Size and center utility windows so action rows remain visible."""
    window.update_idletasks()
    sw = max(int(window.winfo_screenwidth()), 640)
    sh = max(int(window.winfo_screenheight()), 480)
    usable_width = max(640, sw - 80)
    usable_height = max(480, sh - 100)
    w = min(width, usable_width)
    h = min(height, usable_height)
    window.geometry(f"{w}x{h}+{max(0, (sw-w)//2)}+{max(0, (sh-h)//2)}")
    window.minsize(min(min_width, w), min(min_height, h))


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


def _selected_symbols(schematic) -> list:
    selection = list(schematic.get_selection())
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
    source = _library_record_for_instance(library_root, instance)
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
    dialog.geometry("700x720")
    dialog.minsize(580, 500)
    dialog.transient(parent)
    dialog.grab_set()

    outer = ttk.Frame(dialog, padding=18)
    outer.pack(fill="both", expand=True)
    ttk.Label(
        outer,
        text=f"{instance.reference}  ·  {block.name}",
    ).pack(anchor="w")
    ttk.Label(
        outer,
        text="Choose a profile to load a matching circuit variant, parameter set and PCB layout. "
        "You can then customize individual values for this instance.",
        wraplength=640,
        justify="left",
    ).pack(anchor="w", pady=(4, 14))

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
        ttk.Label(
            section,
            text="A profile is a reusable preset. Selecting one fills all linked fields below.",
            wraplength=620,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        profile_combo = ttk.Combobox(
            section,
            textvariable=profile_var,
            values=list(profile_labels),
            state="readonly",
        )
        profile_combo.pack(fill="x")

    variant_var = None
    if variants:
        section = ttk.LabelFrame(content, text="Implementation variant", padding=10)
        section.pack(fill="x", pady=(0, 10))
        variant_var = tk.StringVar(value=_field_value(instance, VARIANT_FIELD_NAME))
        ttk.Label(
            section,
            text="Select a declared implementation, or enter a KiCad text-variable reference.",
            wraplength=620,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        ttk.Combobox(
            section,
            textvariable=variant_var,
            values=[str(item["value"]) for item in variants],
            state="normal",
        ).pack(fill="x")

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
            ttk.Label(
                section,
                text=description,
                wraplength=600,
                justify="left",
            ).grid(row=row * 2 + 1, column=0, columnspan=2, sticky="w", pady=(0, 4))

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
        ttk.Label(
            section,
            text="The selected layout is used by the PCB apply actions.",
        ).pack(anchor="w", pady=(0, 6))
        ttk.Combobox(
            section,
            textvariable=layout_var,
            values=list(layout_ids),
            state="readonly",
        ).pack(fill="x")

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

    ttk.Button(footer, text="Cancel", command=dialog.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(footer, text="Save instance", command=save_configuration).pack(side="right")
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
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
    result = publish_local_copy(local_record)
    _reload_symbol_library(kicad, LIBRARY_NICKNAME)
    return result

def _pick_block_for_placement(records, parent):
    """Choose, inspect and configure a functional block before native placement."""
    if not records:
        raise ValueError("No blocks were found in the registered functional-block libraries.")

    dialog = tk.Toplevel(parent)
    dialog.title("Place Functional Block")
    _set_window_geometry(dialog, 1200, 800, 980, 640)
    dialog.transient(parent)
    dialog.grab_set()

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
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
    ttk.Button(buttons, text="Place Block", command=choose).pack(side="right", padx=(0, 8))
    search_entry.focus_set()
    render_results()
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
    _set_window_geometry(dialog, 920, 640, 760, 480)
    dialog.transient(parent)
    dialog.grab_set()

    outer = ttk.Frame(dialog, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="Search the block library").pack(anchor="w")
    search = tk.StringVar()
    search_entry = ttk.Entry(outer, textvariable=search)
    search_entry.pack(fill="x", pady=(4, 3))
    ttk.Label(
        outer,
        text="Type to filter · ↓ browse results · Enter select · Esc cancel",
    ).pack(anchor="w", pady=(0, 6))

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
        text="Select a block to see its description.",
        wraplength=700,
        justify="left",
    )
    description.pack(fill="x", anchor="w", pady=(8, 0))
    count_label = ttk.Label(outer)
    count_label.pack(anchor="w", pady=(4, 0))

    records_by_id: dict[str, object] = {}
    selected_record = [None]
    chosen_record = [None]
    select_button = ttk.Button(outer, text="Select block", state="disabled")
    select_button.pack(side="right", pady=(10, 0))
    ttk.Button(
        outer,
        text="Cancel",
        command=dialog.destroy,
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
        description.configure(
            text=str(preferred.manifest.get("description", "") or "No description provided.")
        )
        select_button.configure(state="normal")

    def selection_changed(_event=None):
        selection = tree.selection()
        record = records_by_id.get(selection[0]) if selection else None
        selected_record[0] = record
        if record is None:
            description.configure(text="Select a block to see its description.")
            select_button.configure(state="disabled")
        else:
            description.configure(
                text=str(record.manifest.get("description", "") or "No description provided.")
            )
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
    """Resolve the instance's stored layout choice or prompt for one."""
    layouts = _layout_choices(block)
    if not layouts:
        raise ValueError(f"Block {block.name!r} has no stored PCB layout.")
    if instance is not None:
        for field in getattr(instance, "user_fields", ()):
            if str(getattr(field, "name", "")) == LAYOUT_FIELD_NAME:
                field_text = getattr(field, "text", None)
                selected_id = str(getattr(field_text, "value", "") or "").strip()
                if selected_id:
                    for item in layouts:
                        if selected_id.casefold() in {
                            str(item.get("id", "")).casefold(),
                            str(item.get("name", "")).casefold(),
                        }:
                            return item
                    raise ValueError(
                        f"Block {block.name!r} instance {instance.reference} selects unknown "
                        f"layout {selected_id!r}. Choose a valid layout in Configure PCB layout."
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
    selected = filedialog.askdirectory(
        title="Select the shared KiCad block library folder",
        initialdir=str(default),
        parent=parent,
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
    _set_window_geometry(dialog, 660, 390, 580, 340)
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
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
    ttk.Button(buttons, text="Create Block", command=accept).pack(side="right", padx=(0, 8))
    dialog.bind("<Return>", lambda _event: accept())
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.grab_set()
    name_entry.focus_set()
    dialog.wait_window()
    return result[0]


def _create_and_place(
    root, schematic, kicad, project_dir, parent,
    editor_window_id: str | None = None,
) -> None:
    """Create from the selected sheet symbol or the currently displayed sheet."""
    selection_fragment = (
        schematic.get_selection_as_string()
        if hasattr(schematic, "get_selection_as_string") else ""
    )
    if selection_fragment.strip():
        source_sheet = selected_sheet_file(selection_fragment, project_dir)
        source_sheet_uuid = selected_sheet_instance_uuid(selection_fragment)
    else:
        source_sheet, source_sheet_uuid = _active_sheet_source(schematic, project_dir)

    details = _ask_create_block_details(parent, source_sheet)
    if details is None:
        return
    name, description = details

    record = create_block(
        root,
        name,
        source_schematic=source_sheet,
        source_project_name=Path(project_dir).name,
        source_sheet_uuid=source_sheet_uuid,
        description=description,
    )
    record = _register_block(project_dir, record, kicad)
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


def _edit_facade_symbol(
    root, schematic, project_dir, editor_window_id, parent
) -> None:
    """Open the selected library-backed facade through Eeschema's native Symbol Editor action."""
    instance = _selected_symbol(schematic)
    record = _block_record_for_instance(root, project_dir, instance)
    xdotool = shutil.which("xdotool")
    if not xdotool:
        raise RuntimeError(
            "Editing a selected facade from the manager requires xdotool on the "
            "supported Linux/X11 setup. In Eeschema, select the block symbol and "
            "press Ctrl+Shift+E to open its library version in Symbol Editor."
        )
    if not editor_window_id:
        editor_window_id = _capture_eeschema_window_id()
    if not editor_window_id:
        raise RuntimeError(
            "Could not identify the active Eeschema window. Select the block symbol "
            "in Eeschema and retry Edit facade symbol."
        )

    minimized_windows = _minimize_placement_windows(parent)
    try:
        subprocess.run(
            [xdotool, "windowfocus", "--sync", editor_window_id],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        )
        subprocess.run(
            [xdotool, "key", "--clearmodifiers", "ctrl+shift+e"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        )
        deadline = time.monotonic() + 5.0
        focused = editor_window_id
        while time.monotonic() < deadline:
            result = subprocess.run(
                [xdotool, "getwindowfocus"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
            focused = result.stdout.strip()
            if result.returncode == 0 and focused and focused != editor_window_id:
                LOG.info(
                    "Opened library facade %s:%s in KiCad Symbol Editor",
                    record.manifest.get("library_nickname"),
                    record.manifest.get("symbol_name"),
                )
                return
            time.sleep(0.1)
        raise RuntimeError(
            "Eeschema did not hand focus to Symbol Editor after Ctrl+Shift+E. "
            "Make sure the selected facade is linked to the block library and "
            "that the hotkey is still assigned to Edit Library Symbol."
        )
    except Exception:
        _restore_placement_windows(minimized_windows)
        raise

def _place_existing(root, schematic, kicad, project_dir, parent) -> None:
    """Open the searchable browser; placement is handed to KiCad's native cursor tool."""
    _show_library_browser(
        tk.Toplevel(parent), schematic, kicad, project_dir,
        default_library_root(), _capture_eeschema_window_id(),
    )


def _edit_block_implementation(root, schematic, kicad, project_dir, parent) -> None:
    """Compatibility helper for opening a library implementation from the manager."""
    selected = list(schematic.get_selection())
    if len(selected) == 1 and hasattr(selected[0], "definition"):
        record = _block_record_for_instance(root, project_dir, selected[0])
    else:
        record = _pick_record(list_blocks(root), parent, "Edit Block Implementation")
    if record is None:
        return None
    executable = kicad.get_kicad_binary_path("eeschema")
    if not executable:
        raise RuntimeError("KiCad did not provide an Eeschema executable path.")
    process = subprocess.Popen([executable, str(record.implementation)], close_fds=True)
    messagebox.showinfo(
        "Block Implementation",
        f"Opened {record.name} implementation in Eeschema.\n\n"
        "After editing a shared library block, run Publish. "
        "A project-local implementation remains independent until explicitly synchronized.",
        parent=parent,
    )
    return process



def _capture_eeschema_window_id() -> str | None:
    """Return the currently focused Eeschema X11 window, if detectable."""
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return None
    focused = subprocess.run(
        [xdotool, "getwindowfocus"],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    )
    window_id = focused.stdout.strip()
    if focused.returncode != 0 or not window_id:
        return None
    window_class = subprocess.run(
        [xdotool, "getwindowclassname", window_id],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    ).stdout.strip().casefold()
    window_title = subprocess.run(
        [xdotool, "getwindowname", window_id],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    ).stdout.strip().casefold()
    if "eeschema" not in window_class and "schematic editor" not in window_title:
        return None
    return window_id


def _start_native_symbol_placement(
    _kicad,
    editor_window_id: str | None,
    symbol_name: str,
    *,
    xdotool: str | None = None,
    command_runner=subprocess.run,
    pause=time.sleep,
) -> None:
    """Start Eeschema's native Place Symbol tool and choose the facade.

    The tested Eeschema IPC server does not register the generic RunAction
    request. V1 therefore sends KiCad's default Add Symbol hotkey (A) to the
    captured editor window, then uses the native chooser and cursor placement.
    """
    executable = xdotool or shutil.which("xdotool")
    if not executable:
        raise RuntimeError(
            "Cursor placement needs xdotool on the supported Linux desktop. "
            "Install it with sudo apt install xdotool, then reopen the Block Library."
        )
    if not editor_window_id:
        raise RuntimeError(
            "Could not identify the active Eeschema window. Open the schematic editor "
            "and launch the Block Library from its toolbar."
        )
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The selected block has an invalid KiCad symbol name.")

    def send(*arguments: str) -> str:
        result = command_runner(
            [executable, *arguments],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return str(getattr(result, "stdout", "") or "").strip()

    editor_pid = send("getwindowpid", str(editor_window_id))
    if not editor_pid:
        raise RuntimeError("Could not determine the active Eeschema process for cursor placement.")
    send("windowfocus", "--sync", str(editor_window_id))
    pause(0.1)
    send("key", "--clearmodifiers", "a")

    chooser_id = ""
    for _ in range(50):
        focused_id = send("getwindowfocus")
        if focused_id and focused_id != str(editor_window_id):
            focused_pid = send("getwindowpid", focused_id)
            if focused_pid == editor_pid:
                chooser_id = focused_id
                break
        pause(0.1)

    if not chooser_id:
        if send("getwindowfocus") == str(editor_window_id):
            with contextlib.suppress(Exception):
                send("key", "--clearmodifiers", "Escape")
        raise RuntimeError(
            "KiCad's native symbol chooser did not open in the active Eeschema process. "
            "Check Eeschema Preferences → Hotkeys and bind Add Symbol to A, then try again."
        )

    # The native chooser accepts type-to-search in its focused filter/list.
    send("type", "--clearmodifiers", "--delay", "12", symbol_name)
    pause(0.15)
    send("key", "--clearmodifiers", "Return")

    # Selecting the symbol should close the chooser and return to Eeschema;
    # the symbol is then attached to the cursor until the user clicks or Escapes.
    for _ in range(30):
        if send("getwindowfocus") == str(editor_window_id):
            return
        pause(0.1)
    raise RuntimeError(
        "KiCad accepted the symbol chooser selection but did not return focus "
        "to Eeschema. Check the native chooser and try placing the block again."
    )

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


def _native_symbol_preview_photo(record, width: int = 680):
    """Render the actual facade with KiCad's own symbol SVG exporter."""
    symbol_name = str(record.manifest["symbol_name"])
    library_file = Path(record.symbol_library).expanduser().resolve()
    if not library_file.is_file():
        raise FileNotFoundError(f"Facade symbol library is missing: {library_file}")

    cache_key = (
        str(library_file),
        library_file.stat().st_mtime_ns,
        symbol_name,
        int(width),
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
                import cairosvg
            except ImportError as exc:
                raise RuntimeError(
                    "The native KiCad preview rasterizer is missing. Reinstall KiCad Blocks "
                    "so its CairoSVG dependency is installed."
                ) from exc
            try:
                import xml.etree.ElementTree as ET
                svg_root = ET.parse(svg_file).getroot()
                display_names = {
                    str(record.manifest.get("name", "")).strip(),
                    str(record.manifest.get("symbol_name", "")).strip(),
                }
                for parent_node in svg_root.iter():
                    for node in list(parent_node):
                        if node.text and node.text.strip() in display_names:
                            parent_node.remove(node)
                cleaned_svg = output_dir / "facade-preview.svg"
                ET.ElementTree(svg_root).write(cleaned_svg, encoding="utf-8", xml_declaration=True)
                svg_file = cleaned_svg
            except (OSError, ValueError):
                pass
            png_data = cairosvg.svg2png(url=str(svg_file), output_width=int(width))
            try:
                from io import BytesIO

                from PIL import Image
                image = Image.open(BytesIO(png_data)).convert("RGBA")
                bounds = image.getbbox()
                if bounds:
                    image = image.crop(bounds)
                max_height = 260
                scale = min(1.0, int(width) / max(1, image.width), max_height / max(1, image.height))
                if scale < 1.0:
                    image = image.resize(
                        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                png_data = buffer.getvalue()
            except (ImportError, OSError, ValueError):
                pass
        _NATIVE_PREVIEW_CACHE[cache_key] = png_data

    # Tk images belong to a particular Tcl interpreter, so cache only PNG bytes.
    return tk.PhotoImage(data=base64.b64encode(png_data).decode("ascii"))


_NATIVE_PREVIEW_CACHE: dict[tuple, object] = {}


def _draw_facade_preview(canvas, record) -> None:
    """Display a faithful KiCad-rendered facade preview; never approximate it."""
    canvas.delete("all")
    try:
        photo = _native_symbol_preview_photo(record)
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
    """Summarize a block in terms useful to circuit designers."""
    manifest = record.manifest
    lines = ["Purpose"]
    description = str(manifest.get("description", "") or "").strip()
    lines.extend([description or "No description has been added yet.", ""])
    pins = manifest.get("pins", [])
    lines.append(f"Connections · {len(pins)}")
    if pins:
        lines.extend(
            f"  {pin.get('name', 'Unnamed')}  ·  {str(pin.get('electrical_type', 'passive')).replace('_', ' ').title()}"
            for pin in pins
        )
    else:
        lines.append("  No external connections")
    lines.append("")
    parameters = manifest.get("parameters", [])
    lines.append("Configurable settings")
    if parameters:
        lines.extend(
            f"  {item.get('name')}  ·  {item.get('type', 'text')}  ·  default: {item.get('default', '')}"
            for item in parameters
        )
    else:
        lines.append("  No configurable settings declared")
    lines.append("")
    variants = manifest.get("variants", [])
    lines.append("Circuit implementations")
    lines.extend([f"  {item.get('value')}" for item in variants] if variants else ["  One implementation"])
    lines.append("")
    layouts = _layout_choices(record)
    lines.append("PCB layout options")
    lines.extend([f"  {item.get('name', item.get('id'))}" for item in layouts] if layouts else ["  No stored PCB layout"])
    lines.append("")
    profiles = manifest.get("profiles", [])
    lines.append("Ready-made configurations")
    lines.extend(
        [
            f"  {item.get('name', item.get('id'))}"
            + (f"  ·  {item.get('description')}" if item.get("description") else "")
            for item in profiles
        ] if profiles else ["  No named configurations"]
    )
    lines.extend(["", f"Library folder: {record.path.parent.name}", f"Library version: {manifest.get('version', 1)}"])
    return "\n".join(lines)




def _launch_block_asset(kicad, path: Path, description: str):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"The selected {description} is missing: {path}")
    executable_name = "pcbnew" if description == "PCB layout" else "eeschema"
    executable = kicad.get_kicad_binary_path(executable_name)
    if not executable:
        raise RuntimeError(f"KiCad did not provide an executable path for {description}.")
    return subprocess.Popen([executable, str(path)], close_fds=True)


def _save_block_variants(record, variants: list[dict]) -> None:
    """Add/remove implementation files and publish the updated variant metadata."""
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
    normalized = []
    retained_files = set()
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
                raise ValueError(f"Implementation variant must be an existing .kicad_sch file: {source}")
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
        if source is not None:
            shutil.copy2(source, block_path / filename)
            added_files.add(filename)
        retained_files.add(filename)
        normalized.append({"value": value, "file": filename})

    available_values = {item["value"].casefold() for item in normalized}
    dangling_profiles = [
        str(item.get("name", item.get("id", "Unnamed profile")))
        for item in manifest.get("profiles", [])
        if item.get("variant")
        and str(item["variant"]).casefold() not in available_values
    ]
    if dangling_profiles:
        raise ValueError(
            "These configuration profiles still use an implementation being removed or renamed: "
            + ", ".join(dangling_profiles)
            + ". Update those profiles before changing the implementation list."
        )

    removed_files = previous_files - retained_files
    manifest["variants"] = normalized
    manifest["files"] = [
        filename for filename in manifest.get("files", [])
        if filename not in removed_files
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


def _show_block_variant_editor(record, parent) -> None:
    """Manage named implementation variants after the block has been created."""
    dialog = tk.Toplevel(parent)
    dialog.title(f"Implementations — {record.name}")
    dialog.transient(parent)
    _set_window_geometry(dialog, 980, 620, 800, 500)
    frame = ttk.Frame(dialog, padding=14)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text="Circuit implementations",
        font=("TkDefaultFont", 12, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        frame,
        text="Each value selects a complete alternative .kicad_sch implementation through KB_VARIANT.",
        wraplength=900,
    ).pack(anchor="w", pady=(4, 12))
    table = ttk.Frame(frame)
    table.pack(fill="both", expand=True)
    table.columnconfigure(0, weight=1)
    table.rowconfigure(0, weight=1)
    tree = ttk.Treeview(table, columns=("value", "file"), show="headings", selectmode="browse")
    tree.heading("value", text="Variant value")
    tree.heading("file", text="Implementation file")
    tree.column("value", width=220, minwidth=160, stretch=False)
    tree.column("file", width=620, minwidth=300, stretch=True)
    scroll = ttk.Scrollbar(table, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
    tree.configure(yscrollcommand=scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    palette = getattr(parent, "_kicad_blocks_palette", {})
    tree.tag_configure("even", background=palette.get("surface", "#f4f5f6"))
    tree.tag_configure("odd", background=palette.get("surface_alt", "#e8eaed"))
    variants = [
        {"value": str(item["value"]), "file": str(item["file"])}
        for item in record.manifest.get("variants", [])
    ]

    def render():
        tree.delete(*tree.get_children())
        for index, item in enumerate(variants):
            tree.insert(
                "", "end", iid=str(index),
                values=(item["value"], item["file"]),
                tags=("even" if index % 2 == 0 else "odd",),
            )

    def add_variant():
        value = simpledialog.askstring(
            "Add implementation",
            "Variant value (for example 1V8 or 3V3):",
            parent=dialog,
        )
        if value is None:
            return
        value = value.strip()
        if not value:
            return
        if any(item["value"].casefold() == value.casefold() for item in variants):
            messagebox.showerror("Add implementation", f"The value {value!r} already exists.", parent=dialog)
            return
        source = filedialog.askopenfilename(
            title="Choose implementation schematic",
            filetypes=[("KiCad schematic", "*.kicad_sch")],
            parent=dialog,
        )
        if not source:
            return
        variants.append({"value": value, "source": source})
        render()

    def edit_variant():
        selection = tree.selection()
        if not selection:
            return
        index = int(selection[0])
        current = variants[index]
        value = simpledialog.askstring(
            "Edit implementation",
            "Variant value:",
            initialvalue=current["value"],
            parent=dialog,
        )
        if value is None:
            return
        value = value.strip()
        if not value:
            return
        if any(
            other_index != index and item["value"].casefold() == value.casefold()
            for other_index, item in enumerate(variants)
        ):
            messagebox.showerror("Edit implementation", f"The value {value!r} already exists.", parent=dialog)
            return
        source = filedialog.askopenfilename(
            title="Choose replacement implementation schematic",
            filetypes=[("KiCad schematic", "*.kicad_sch")],
            parent=dialog,
        )
        if not source:
            return
        variants[index] = {"value": value, "source": source}
        render()

    def remove_variant():
        selection = tree.selection()
        if not selection:
            return
        variants.pop(int(selection[0]))
        render()

    def save_and_close():
        try:
            _save_block_variants(record, variants)
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            messagebox.showerror("Save implementation variants", str(exc), parent=dialog)
            return
        dialog.destroy()

    actions = ttk.Frame(frame)
    actions.pack(fill="x", pady=(12, 0))
    ttk.Button(actions, text="Add implementation…", command=add_variant).pack(side="left")
    ttk.Button(actions, text="Edit selected…", command=edit_variant).pack(side="left", padx=(8, 0))
    ttk.Button(actions, text="Remove selected", command=remove_variant).pack(side="left", padx=(8, 0))
    ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="right")
    ttk.Button(actions, text="Save changes", command=save_and_close).pack(side="right", padx=(0, 8))
    render()
    dialog.grab_set()
    dialog.wait_window()


def _show_library_roots_dialog(parent) -> bool:
    """Manage registered library folders with explicit add/create/edit actions."""
    dialog = tk.Toplevel(parent)
    dialog.title("Manage KiCad Functional Block Libraries")
    _set_window_geometry(dialog, 1180, 720, 940, 600)
    dialog.transient(parent)
    dialog.grab_set()

    outer = ttk.Frame(dialog, padding=14)
    outer.pack(fill="both", expand=True)
    ttk.Label(
        outer,
        text="Registered block libraries",
        font=("TkDefaultFont", 12, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Add an existing folder or create a new, empty library. Select a row to change "
            "its registered folder, choose the default destination, or unregister it. "
            "Unregistering never deletes files. Changing a path does not move files."
        ),
        wraplength=900,
        justify="left",
    ).pack(anchor="w", pady=(4, 12))

    table_frame = ttk.Frame(outer)
    table_frame.pack(fill="both", expand=True)
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
    ttk.Label(outer, textvariable=status, wraplength=900).pack(anchor="w", pady=(8, 0))

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
        status.set(
            f"{len(roots)} registered folders. "
            + (
                "KICAD_BLOCKS_LIBRARY overrides the default destination."
                if environment_root is not None
                else "The row marked Default receives newly created blocks."
            )
        )
        update_selection_actions()

    def add_library() -> None:
        selected = filedialog.askdirectory(
            title="Add existing block library folder",
            mustexist=True,
            parent=dialog,
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
        parent_folder = filedialog.askdirectory(
            title="Choose where to create the new library",
            mustexist=True,
            parent=dialog,
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
        selected = filedialog.askdirectory(
            title="Choose the replacement registered folder",
            initialdir=str(old_path.parent),
            mustexist=True,
            parent=dialog,
        )
        if not selected:
            return
        new_path = Path(selected).expanduser().resolve()
        if new_path == old_path:
            return
        try:
            was_default = environment_root is None and old_path == default_library_root()
            add_library_root(new_path)
            if was_default:
                set_library_root(new_path)
            remove_library_root(old_path)
            changed[0] = True
            render(new_path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Change registered folder", str(exc), parent=dialog)

    def remove_library() -> None:
        path = selected_path()
        if path is None or path == environment_root:
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

    actions = ttk.LabelFrame(outer, text="Library folder actions", padding=8)
    actions.pack(fill="x", pady=(10, 0))
    ttk.Button(actions, text="Add existing folder…", command=add_library).pack(side="left")
    ttk.Button(
        actions, text="Create new library…", command=create_library
    ).pack(side="left", padx=(6, 0))
    change_button = ttk.Button(
        actions, text="Change registered folder…", command=change_library_folder
    )
    change_button.pack(side="left", padx=(6, 0))
    remove_button = ttk.Button(actions, text="Unregister", command=remove_library)
    remove_button.pack(side="left", padx=(6, 0))
    default_button = ttk.Button(actions, text="Set as default", command=make_default)
    default_button.pack(side="left", padx=(6, 0))
    ttk.Button(actions, text="Close", command=dialog.destroy).pack(side="right")

    def update_selection_actions(_event=None) -> None:
        path = selected_path()
        fixed = path is None or path == environment_root
        change_button.configure(state="disabled" if fixed else "normal")
        remove_button.configure(state="disabled" if fixed else "normal")
        default_button.configure(
            state="disabled" if path is None or environment_root is not None else "normal"
        )

    tree.bind("<<TreeviewSelect>>", update_selection_actions)
    render()
    update_selection_actions()
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
            if abs(delta) >= 120 or sys.platform == "win32":
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
    _set_window_geometry(window, 1480, 980, 1160, 740)
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

    ttk.Label(left, text="Search blocks").pack(anchor="w")
    search = tk.StringVar()
    search_entry = ttk.Entry(left, textvariable=search)
    search_entry.pack(fill="x", pady=(4, 3))
    ttk.Label(
        left,
        text="Type to filter · ↓ browse results · Enter place",
    ).pack(anchor="w", pady=(0, 6))
    tree_frame = ttk.Frame(left)
    tree_frame.pack(fill="both", expand=True)
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
    tree.heading("pins", text="Pins")
    tree.heading("variants", text="Variants")
    tree.heading("layouts", text="Layouts")
    tree.column("block", width=210, minwidth=160, stretch=True)
    tree.column("library", width=115, minwidth=90, stretch=False)
    tree.column("pins", width=42, minwidth=35, stretch=False, anchor="center")
    tree.column("variants", width=60, minwidth=48, stretch=False, anchor="center")
    tree.column("layouts", width=55, minwidth=45, stretch=False, anchor="center")
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

    library_footer = ttk.Frame(left)
    library_footer.pack(fill="x", pady=(8, 0))

    name_label = ttk.Label(right, text="Select a block")
    name_label.pack(anchor="w")
    description_label = ttk.Label(
        right, text="", wraplength=650, justify="left"
    )
    description_label.pack(anchor="w", pady=(4, 8))
    preview_box = ttk.LabelFrame(right, text="Facade preview", padding=5)
    preview_box.pack(fill="x")
    preview_frame = ttk.Frame(preview_box)
    preview_frame.pack(fill="x", expand=True)
    preview = tk.Canvas(
        preview_frame,
        height=250,
        background=theme_background,
        highlightthickness=0,
        borderwidth=0,
    )
    preview_scroll = ttk.Scrollbar(
        preview_frame, orient="vertical", command=preview.yview, style="Vertical.TScrollbar"
    )
    preview.configure(yscrollcommand=preview_scroll.set)
    preview.pack(side="left", fill="x", expand=True)
    preview_scroll.pack(side="right", fill="y")
    _bind_mousewheel(preview, lambda amount: preview.yview_scroll(amount, "units"))

    details_box = ttk.LabelFrame(right, text="Block overview", padding=5)
    details_box.pack(fill="both", expand=True, pady=(8, 0))
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

    def set_details(record):
        selected_record[0] = record
        if record is None:
            name_label.configure(
                text="No matching blocks" if search.get().strip() else "No blocks in registered libraries"
            )
            description_label.configure(text="")
            details.configure(state="normal")
            details.delete("1.0", "end")
            details.configure(state="disabled")
            preview.delete("all")
            return
        name_label.configure(text=record.name)
        description_label.configure(
            text=(
                str(record.manifest.get("description", "") or "No description provided.")
                + f"\nLibrary: {record.path.parent.name}"
            )
        )
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert("1.0", _block_overview_text(record))
        details.configure(state="disabled")
        _draw_facade_preview(preview, record)

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

    def place_selected(_event=None):
        record = selected_record[0]
        if record is None:
            return
        try:
            if not editor_window_id:
                raise RuntimeError(
                    "The Eeschema window was not captured. Close this browser and launch "
                    "it again from the Schematic Editor."
                )
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
                    kicad, editor_window_id, str(record.manifest["symbol_name"])
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
        variants = list(record.manifest.get("variants", []))
        if not variants:
            try:
                _launch_block_asset(kicad, record.implementation, "implementation schematic")
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror("Open implementation", str(exc), parent=window)
            return
        chooser = tk.Toplevel(window)
        chooser.title(f"Open implementation — {record.name}")
        chooser.transient(window)
        chooser.grab_set()
        frame = ttk.Frame(chooser, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Select implementation variant:").pack(anchor="w")
        choice = tk.StringVar(value=str(variants[0]["value"]))
        combo = ttk.Combobox(
            frame,
            textvariable=choice,
            values=[str(item["value"]) for item in variants],
            state="readonly",
            width=32,
        )
        combo.pack(fill="x", pady=8)

        def launch_variant():
            selected = next(
                (item for item in variants if str(item["value"]) == choice.get()),
                None,
            )
            if selected is None:
                return
            chooser.destroy()
            try:
                _launch_block_asset(
                    kicad, record.path / selected["file"], "implementation schematic"
                )
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror("Open implementation", str(exc), parent=window)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Open", command=launch_variant).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=chooser.destroy).pack(
            side="right", padx=(0, 6)
        )
        combo.focus_set()

    def edit_facade(_event=None):
        record = selected_record[0]
        if record is None:
            return
        selection = list(schematic.get_selection()) if hasattr(schematic, "get_selection") else []
        if len(selection) != 1 or not hasattr(selection[0], "definition"):
            messagebox.showinfo(
                "Edit facade symbol",
                "Select a placed instance of this block in the schematic, then open the library browser again. "
                "KiCad's native Symbol Editor opens the library definition from that selected instance.",
                parent=window,
            )
            return
        try:
            selected_record_for_instance = _block_record_for_instance(
                current_roots[0], project_dir, selection[0]
            )
            if selected_record_for_instance.manifest.get("block_id") != record.manifest.get("block_id"):
                raise ValueError(
                    "The selected schematic symbol is not the block highlighted in the library browser."
                )
            _edit_facade_symbol(
                current_roots[0], schematic, project_dir, editor_window_id, window
            )
        except (OSError, RuntimeError, ValueError, LookupError, subprocess.SubprocessError) as exc:
            messagebox.showerror("Edit facade symbol", str(exc), parent=window)

    def edit_variants(_event=None):
        record = selected_record[0]
        if record is None:
            return
        try:
            _show_block_variant_editor(record, window)
            refresh_records()
        except Exception as exc:
            LOG.exception("Could not update implementation variants")
            messagebox.showerror("Manage implementations", str(exc), parent=window)

    def open_layout(_event=None):
        record = selected_record[0]
        if record is None:
            return
        layouts = _layout_choices(record)
        if not layouts:
            messagebox.showinfo(
                "PCB Layout",
                f"{record.name} has no stored PCB layout.",
                parent=window,
            )
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
        choice = tk.StringVar(value=labels[0])
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
                _launch_block_asset(kicad, target, "PCB layout")
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror("Open PCB layout", str(exc), parent=window)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Open", command=launch_layout).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=chooser.destroy).pack(
            side="right", padx=(0, 6)
        )
        combo.focus_set()

    ttk.Button(
        library_footer,
        text="Manage library folders…",
        command=manage_libraries,
    ).pack(fill="x")

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
            _draw_facade_preview(preview, selected_record[0])
            if selected_record[0] is not None else None
        ),
    )

    actions = ttk.Frame(right)
    actions.pack(fill="x", pady=(10, 0))
    ttk.Button(actions, text="Place at cursor", command=place_selected).pack(side="left")
    ttk.Button(
        actions, text="Edit implementation…", command=open_implementation
    ).pack(side="left", padx=(6, 0))
    ttk.Button(
        actions, text="Edit facade…", command=edit_facade
    ).pack(side="left", padx=(6, 0))
    ttk.Button(
        actions, text="Manage variants…", command=edit_variants
    ).pack(side="left", padx=(6, 0))
    ttk.Button(
        actions, text="Open PCB layout…", command=open_layout
    ).pack(side="left", padx=(6, 0))
    ttk.Button(actions, text="Refresh", command=refresh_records).pack(side="right")
    refresh_records()
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
    executable = kicad.get_kicad_binary_path("eeschema")
    if not executable:
        raise RuntimeError("KiCad did not provide an Eeschema executable path.")
    process = subprocess.Popen([executable, str(implementation_path)], close_fds=True)
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
    selection = list(schematic.get_selection()) if schematic is not None else []
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
    library_root = record.path.parent
    output = filedialog.asksaveasfilename(
        title="Save recursive block BOM",
        defaultextension=".csv",
        initialfile=f"{record.manifest['slug']}_bom.csv",
        filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        parent=parent,
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
    output = filedialog.askdirectory(
        title="Select a folder for the library validation reports",
        parent=parent,
    )
    if not output:
        return
    results = []
    for library_path in _library_roots_for(root):
        report_dir = Path(output) / library_path.name
        result = validate_block_library(
            library_path,
            report_dir,
            find_kicad_cli(),
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
    selection = list(schematic.get_selection()) if schematic is not None else []
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
    library_root = record.path.parent
    output = filedialog.askdirectory(
        title="Select a folder for recursive ERC reports",
        parent=parent,
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
    result = refresh_library_block(record)
    if result["changed"]:
        _register_block(project_dir, record, kicad, library_paths)
    messagebox.showinfo(
        "Library Block Published",
        f"Version: {result['version']}\n"
        f"Changed files: {', '.join(result['changed']) or 'none'}\n"
        "Local copies can now synchronize from this revision.",
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
    board_path = filedialog.askopenfilename(
        title="Select the existing project PCB board",
        filetypes=[("KiCad PCB", "*.kicad_pcb"), ("All files", "*.*")],
        parent=parent,
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
    output_path = filedialog.asksaveasfilename(
        title="Save merged PCB board copy",
        initialdir=str(source_board.parent),
        initialfile=default_output.name,
        defaultextension=".kicad_pcb",
        filetypes=[("KiCad PCB", "*.kicad_pcb")],
        parent=parent,
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

    board_path = filedialog.askopenfilename(
        title="Select the existing project PCB board",
        filetypes=[("KiCad PCB", "*.kicad_pcb"), ("All files", "*.*")],
        parent=parent,
    )
    if not board_path:
        return
    source_board = Path(board_path)
    default_output = source_board.with_name(
        f"{source_board.stem}_{len(placements)}blocks.kicad_pcb"
    )
    output_path = filedialog.asksaveasfilename(
        title="Save combined PCB block layout copy",
        initialdir=str(source_board.parent),
        initialfile=default_output.name,
        defaultextension=".kicad_pcb",
        filetypes=[("KiCad PCB", "*.kicad_pcb")],
        parent=parent,
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


def _add_nested_dependency(root, parent, parent_window) -> None:
    library_paths = _library_roots_for(root)
    records = _list_library_records(library_paths)
    parent_record = _pick_record(records, parent_window, "Select Parent Block")
    if parent_record is None:
        return
    child_records = [record for record in records if record.path != parent_record.path]
    child_record = _pick_record(child_records, parent_window, "Select Nested Block")
    if child_record is None:
        return

    default_map = ", ".join(
        f"{pin['name']}:{pin['name']}" for pin in child_record.manifest.get("pins", [])
    )
    mapping_text = simpledialog.askstring(
        "Nested Block Pin Map",
        "Map each child pin to a net in the parent implementation.\n"
        "Format: CHILD_PIN:PARENT_NET, comma-separated.",
        initialvalue=default_map,
        parent=parent_window,
    )
    if mapping_text is None:
        return
    orientation_deg = simpledialog.askinteger(
        "Nested Block Orientation",
        "Rotate the child facade counter-clockwise by 0, 90, 180 or 270 degrees:",
        initialvalue=0,
        minvalue=0,
        maxvalue=270,
        parent=parent_window,
    )
    if orientation_deg is None:
        return
    if orientation_deg not in (0, 90, 180, 270):
        raise ValueError("Nested block rotation must be 0, 90, 180 or 270 degrees.")
    pin_map = {}
    for entry in mapping_text.split(","):
        token = entry.strip()
        if not token:
            continue
        if ":" not in token:
            raise ValueError(f"Invalid pin-map entry {token!r}; use CHILD_PIN:PARENT_NET.")
        child_pin, parent_net = (part.strip() for part in token.split(":", 1))
        if child_pin in pin_map:
            raise ValueError(f"Pin {child_pin!r} appears more than once in the pin map.")
        pin_map[child_pin] = parent_net

    manifest_path = parent_record.path / "manifest.json"
    original_manifest = manifest_path.read_bytes()
    try:
        updated = add_dependency(
            parent_record, child_record, pin_map=pin_map, orientation=orientation_deg
        )
        result = compose_block_implementation(
            updated, parent_record.path.parent, find_kicad_cli()
        )
    except Exception:
        manifest_path.write_bytes(original_manifest)
        raise
    outcome = next(
        (item for item in result["composed"] if item["block"] == parent_record.name),
        {"placed": [], "labels": 0},
    )
    messagebox.showinfo(
        "Nested Block Composed",
        f"Added {child_record.name} to {parent_record.name} as "
        f"{updated.manifest['dependencies'][-1]['reference']} at {orientation_deg}°.\n"
        f"New facade symbols: {len(outcome['placed'])}; new net labels: {outcome['labels']}.\n"
        "The implementation is composed inline with no hierarchical sheet.",
        parent=parent_window,
    )


def _show_action_hub(root):
    """Show a scrollable task hub for less frequent and context-specific actions."""
    _configure_tk_theme(root)
    root.title("KiCad Blocks — Tasks")
    root.geometry("900x660")
    root.minsize(760, 540)

    outer = ttk.Frame(root, padding=18)
    outer.pack(fill="both", expand=True)
    ttk.Label(
        outer,
        text="KiCad Blocks",
        font=("TkDefaultFont", 18, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Choose an operation. For normal block selection and placement, use the "
            "Functional Block Library browser; the remaining actions are grouped by workflow."
        ),
        wraplength=840,
        justify="left",
    ).pack(anchor="w", pady=(4, 14))

    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)

    groups = [
        (
            "Use & build",
            [
                (
                    "Browse and place blocks",
                    "Search registered libraries, inspect a facade, configure it, and place it from the browser.",
                    "manage-library",
                ),
                (
                    "Create block from sheet",
                    "Select a hierarchical sheet symbol in its parent, or run this from the open sheet with nothing selected.",
                    "create-block",
                ),
                (
                    "Open block implementation",
                    "Select one placed block instance to open its implementation or matching variant.",
                    "open-implementation",
                ),
            ],
        ),
        (
            "Edit & maintain",
            [
                (
                    "Configure selected instance",
                    "Select one block instance to edit its profile, variant, parameters, or PCB layout.",
                    "configure-instance",
                ),
                (
                    "Edit facade symbol",
                    "Select one block instance to edit its library symbol in Symbol Editor.",
                    "edit-facade",
                ),
                (
                    "Publish implementation",
                    "Select a library block implementation and publish its current changes.",
                    "publish-block",
                ),
                (
                    "Make selected instance local",
                    "Select one shared-library block instance to create an independent project copy.",
                    "make-local-copy",
                ),
                (
                    "Synchronize local copy",
                    "Select one local block instance to reconcile it with its shared-library source.",
                    "synchronize-local-copy",
                ),
                (
                    "Nest a block",
                    "Choose a parent and child block to compose the child into the parent implementation.",
                    "nest-block",
                ),
            ],
        ),
        (
            "Checks & PCB",
            [
                (
                    "Validate registered libraries",
                    "Check manifests, assets, dependencies, revisions, and recursive ERC for all libraries.",
                    "validate-libraries",
                ),
                (
                    "Export recursive BOM",
                    "Select one block instance to export its BOM including nested dependencies.",
                    "recursive-bom",
                ),
                (
                    "Run recursive ERC",
                    "Select one block instance to check its implementation and nested dependencies.",
                    "recursive-erc",
                ),
                (
                    "Apply selected PCB layout",
                    "Select one block instance with a stored layout; a new board copy is created.",
                    "apply-pcb-layout",
                ),
                (
                    "Apply multiple PCB layouts",
                    "Select two or more block instances with stored layouts; a combined board copy is created.",
                    "apply-multiple-pcb-layouts",
                ),
            ],
        ),
    ]

    def dispatch(selected_action: str) -> None:
        root.destroy()
        run_action(selected_action)

    canvas_background = (
        ttk.Style(root).lookup("TFrame", "background") or root.cget("background")
    )
    for tab_name, actions in groups:
        tab = ttk.Frame(tabs)
        tabs.add(tab, text=tab_name)
        scroll_area = ttk.Frame(tab)
        scroll_area.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            scroll_area,
            background=canvas_background,
            highlightthickness=0,
            borderwidth=0,
        )
        scrollbar = ttk.Scrollbar(
            scroll_area, orient="vertical", command=canvas.yview, style="Vertical.TScrollbar"
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        content = ttk.Frame(canvas, padding=14)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def update_scroll_region(_event=None, target=canvas):
            target.configure(scrollregion=target.bbox("all"))

        def fit_content_width(event, target=canvas, item=content_id):
            target.itemconfigure(item, width=event.width)

        content.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", fit_content_width)
        _bind_mousewheel(
            canvas,
            lambda amount, target=canvas: target.yview_scroll(amount, "units"),
            include_children=True,
        )

        for row, (title, description, action_id) in enumerate(actions):
            cell = ttk.Frame(content, padding=(0, 3))
            cell.grid(row=row, column=0, sticky="ew", pady=4)
            ttk.Button(
                cell,
                text=title,
                command=lambda selected=action_id: dispatch(selected),
                width=31,
            ).grid(row=0, column=0, sticky="nw", padx=(0, 12))
            ttk.Label(
                cell,
                text=description,
                wraplength=590,
                justify="left",
            ).grid(row=0, column=1, sticky="w")
            cell.columnconfigure(1, weight=1)
        content.columnconfigure(0, weight=1)

    footer = ttk.Frame(outer)
    footer.pack(fill="x", pady=(12, 0))
    ttk.Label(
        footer,
        text=(
            "PCB Editor: use “Place Imported Block Layout” after applying a layout "
            "from the schematic actions."
        ),
        wraplength=700,
        justify="left",
    ).pack(side="left", anchor="w")
    ttk.Button(footer, text="Close", command=root.destroy).pack(side="right")
    return tabs


def run_action(action: str) -> None:
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
        "nest-block",
    }
    if action not in valid_actions:
        raise ValueError(f"Unknown KiCad Blocks action: {action!r}")

    _load_tkinter()
    root = tk.Tk()
    _configure_tk_theme(root)
    root.title("KiCad Blocks — Tasks" if action == "open-hub" else "KiCad Blocks")
    try:
        if action == "open-hub":
            _show_action_hub(root)
            root.mainloop()
            return

        editor_window_id = _capture_eeschema_window_id()
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
            _edit_facade_symbol(
                library_paths, schematic, project_dir, editor_window_id, root
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
        elif action == "nest-block":
            _add_nested_dependency(library_paths, root, root)
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
