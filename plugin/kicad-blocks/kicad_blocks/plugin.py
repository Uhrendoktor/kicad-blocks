"""KiCad IPC actions for opaque, library-backed schematic block symbols."""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
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
from .extraction import selected_sheet_file
from .model import (
    LAYOUT_FIELD_NAME,
    PARAMETER_FIELD_PREFIX,
    PROFILE_FIELD_NAME,
    VARIANT_FIELD_NAME,
    parameter_field_name,
    resolve_parameter_values,
    resolve_profile_configuration,
    resolve_variant_implementation,
    slugify,
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
    set_library_root,
)

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


def project_directory(schematic) -> Path:
    project = schematic.get_project()
    raw = Path(project.path).expanduser()
    if raw.is_file() or raw.suffix.lower() == ".kicad_pro":
        raw = raw.parent
    resolved = raw.resolve()
    if not resolved.is_dir():
        raise RuntimeError(
            f"The current project directory does not exist: {resolved}. Save the project first."
        )
    return resolved


def _reload_symbol_library(kicad, nickname: str) -> None:
    """Load or reload a project symbol library, including new table entries."""
    from kipy.errors import LibraryCommandError

    # kiapi.common.types: LT_SYMBOL=1; LibraryTableScope.LTS_PROJECT=2.
    try:
        kicad.reload_library(type=1, scope=2, nicknames=[nickname])
    except LibraryCommandError:
        # A library added to sym-lib-table after KiCad startup may not yet be
        # present in the IPC library manager's cache. Ask KiCad to load the
        # current project table, then retry the exact nickname.
        try:
            kicad.load_all_libraries(types=[1])
            kicad.wait_for_libraries(types=[1], timeout_s=120.0)
            kicad.reload_library(type=1, scope=2, nicknames=[nickname])
        except LibraryCommandError as retry_error:
            raise RuntimeError(
                f"KiCad has not picked up the new symbol library {nickname!r}. "
                "Its project sym-lib-table entry was written, but this Eeschema "
                "session cannot load newly added table rows through IPC. Save the "
                "project, restart Eeschema, then use the Place action."
            ) from retry_error
    kicad.wait_for_libraries(types=[1], scope=2, timeout_s=60.0)


def _register_block(project_dir: Path, record, kicad):
    """Ensure a block is present in the stable, already-registered catalog."""
    record = ensure_stable_block_identity(record)
    register_project_symbol_library(
        project_dir,
        LIBRARY_NICKNAME,
        record.symbol_library,
        description="KiCad Blocks — shared facade catalog",
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


def _library_record_for_instance(library_root: Path, instance):
    library_nickname, symbol_name = _symbol_identifier(instance)
    for record in list_blocks(library_root):
        manifest = record.manifest
        if (
            manifest.get("library_nickname") == library_nickname
            and manifest.get("symbol_name") == symbol_name
        ):
            return record
    raise FileNotFoundError(
        f"The selected symbol {library_nickname}:{symbol_name} is not in the configured block library."
    )


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
        font=("TkDefaultFont", 14, "bold"),
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
                foreground="#667785",
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

def _pick_record(records, parent, title: str):
    """Show a searchable modal picker for manager actions that need a library block."""
    if not records:
        raise ValueError("No blocks were found in the configured library.")
    if len(records) == 1:
        return records[0]

    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.geometry("760x500")
    dialog.minsize(600, 360)
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
        foreground="#667785",
    ).pack(anchor="w", pady=(0, 6))

    results_frame = ttk.Frame(outer)
    results_frame.pack(fill="both", expand=True)
    results_frame.columnconfigure(0, weight=1)
    results_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        results_frame,
        columns=("pins", "variants", "layouts"),
        show="tree headings",
        selectmode="browse",
    )
    tree.heading("#0", text="Block")
    tree.heading("pins", text="Pins")
    tree.heading("variants", text="Variants")
    tree.heading("layouts", text="Layouts")
    tree.column("#0", width=340, minwidth=180, stretch=True)
    tree.column("pins", width=55, minwidth=45, stretch=False, anchor="center")
    tree.column("variants", width=75, minwidth=60, stretch=False, anchor="center")
    tree.column("layouts", width=65, minwidth=55, stretch=False, anchor="center")
    scrollbar = ttk.Scrollbar(results_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    description = ttk.Label(
        outer,
        text="Select a block to see its description.",
        wraplength=700,
        justify="left",
        foreground="#53616b",
    )
    description.pack(fill="x", anchor="w", pady=(8, 0))
    count_label = ttk.Label(outer, foreground="#667785")
    count_label.pack(anchor="w", pady=(4, 0))

    records_by_id = {
        str(record.manifest["block_id"]): record
        for record in records
    }
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
        for record in visible:
            block_id = str(record.manifest["block_id"])
            tree.insert(
                "", "end", iid=block_id, text=record.name,
                values=(
                    len(record.manifest.get("pins", [])),
                    len(record.manifest.get("variants", [])),
                    len(_layout_choices(record)),
                ),
            )
        count_label.configure(text=f"{len(visible)} of {len(records)} blocks")
        if preferred is None:
            selected_record[0] = None
            description.configure(text="No matching blocks.")
            select_button.configure(state="disabled")
            return
        preferred_id = str(preferred.manifest["block_id"])
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


def _create_and_place(
    root, schematic, kicad, project_dir, parent,
    editor_window_id: str | None = None,
) -> None:
    """Create a facade from the selected hierarchical sheet; never ask for pins."""
    if not hasattr(schematic, "get_selection_as_string"):
        raise RuntimeError(
            "This KiCad build does not expose the schematic selection serializer."
        )
    selection_fragment = schematic.get_selection_as_string()
    source_sheet = selected_sheet_file(selection_fragment, project_dir)

    name = simpledialog.askstring(
        "Create Block from Hierarchical Sheet",
        "Block name:",
        initialvalue=source_sheet.stem.replace("_", " ").replace("-", " ").title(),
        parent=parent,
    )
    if not name:
        return
    description = simpledialog.askstring(
        "Block Description",
        "Description (optional):",
        initialvalue="",
        parent=parent,
    )
    if description is None:
        return

    record = create_block(
        root,
        name,
        source_schematic=source_sheet,
        source_project_name=Path(project_dir).name,
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
        "facade pin names and electrical types automatically.",
        parent=parent,
    )


def _edit_facade_symbol(root, kicad, parent) -> None:
    """Open the selected block's single-symbol library asset in KiCad Symbol Editor."""
    record = _pick_record(list_blocks(root), parent, "Edit Facade Symbol")
    if record is None:
        return
    executable = kicad.get_kicad_binary_path("symbol_editor")
    if not executable:
        messagebox.showinfo(
            "Edit Facade Symbol",
            "Open this block's symbol library in KiCad Symbol Editor:\\n"
            f"{record.symbol_library}\\n\\n"
            "Save the library asset, then run Publish Library Block to refresh "
            "the shared catalog and synchronize its hierarchical-label interface.",
            parent=parent,
        )
        return
    subprocess.Popen([executable, str(record.symbol_library)], close_fds=True)

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


def _draw_facade_preview(canvas, record) -> None:
    """Draw a lightweight vector preview of the facade pin interface."""
    canvas.delete("all")
    width = max(int(canvas.winfo_width()), 480)
    height = max(int(canvas.winfo_height()), 250)
    manifest = record.manifest
    pins = list(manifest.get("pins", []))
    split = (len(pins) + 1) // 2
    left_pins, right_pins = pins[:split], pins[split:]
    rows = max(len(left_pins), len(right_pins), 1)
    body_height = max(88, rows * 27 + 28)
    body_width = 190
    body_left = max(110, (width - body_width) / 2)
    body_top = max(24, (height - body_height) / 2)
    body_right = body_left + body_width
    body_bottom = body_top + body_height
    canvas.create_rectangle(
        body_left, body_top, body_right, body_bottom,
        outline="#263746", width=2, fill="#f3f6f8",
    )
    canvas.create_text(
        (body_left + body_right) / 2,
        body_top + body_height * 0.38,
        text=str(manifest.get("name", record.name)),
        width=body_width - 24,
        justify="center",
        fill="#14212b",
        font=("TkDefaultFont", 11, "bold"),
    )
    canvas.create_text(
        (body_left + body_right) / 2,
        body_top + body_height * 0.68,
        text="SCHEMATIC FACADE",
        fill="#54636f",
        font=("TkDefaultFont", 8),
    )

    def draw_pin(pin, x1, x2, y, anchor):
        name = str(pin.get("name", ""))
        number = str(pin.get("number", ""))
        kind = str(pin.get("electrical_type", "passive"))
        canvas.create_line(x1, y, x2, y, fill="#263746", width=2)
        text_x = x1 - 7 if anchor == "e" else x2 + 7
        canvas.create_text(
            text_x, y - 4, text=name, anchor=anchor, fill="#14212b",
            font=("TkDefaultFont", 9),
        )
        canvas.create_text(
            text_x, y + 8, text=f"{number} - {kind}", anchor=anchor,
            fill="#667785", font=("TkDefaultFont", 7),
        )

    def y_for(index, count):
        if count <= 1:
            return (body_top + body_bottom) / 2
        return body_top + 16 + index * (body_height - 32) / (count - 1)

    for index, pin in enumerate(left_pins):
        y = y_for(index, len(left_pins))
        draw_pin(pin, body_left - 42, body_left, y, "e")
    for index, pin in enumerate(right_pins):
        y = y_for(index, len(right_pins))
        draw_pin(pin, body_right, body_right + 42, y, "w")

    if not pins:
        canvas.create_text(width / 2, height / 2, text="No facade pins", fill="#667785")
    canvas.configure(scrollregion=(0, 0, width, max(height, body_bottom + 32)))


def _block_overview_text(record) -> str:
    manifest = record.manifest
    pin_lines = [
        f"  {pin.get('number', ''):>3}  {pin.get('name', '')}  ({pin.get('electrical_type', 'passive')})"
        for pin in manifest.get("pins", [])
    ] or ["  (none)"]
    parameter_lines = [
        f"  {item.get('name')} : {item.get('type')} = {item.get('default')}"
        for item in manifest.get("parameters", [])
    ] or ["  (none)"]
    variant_lines = [
        f"  {item.get('value')}  ->  {item.get('file')}"
        for item in manifest.get("variants", [])
    ] or ["  (single implementation)"]
    layout_lines = [
        f"  {item.get('name', item.get('id'))}  [{item.get('id', 'legacy')}]"
        for item in _layout_choices(record)
    ] or ["  (none)"]
    profile_lines = [
        "  "
        + str(item.get("name", item.get("id")))
        + f"  [{item.get('id')}]"
        + f"  | variant={item.get('variant') or 'single'}"
        + f"  | layout={item.get('layout') or 'none'}"
        for item in manifest.get("profiles", [])
    ] or ["  (none)"]
    return (
        f"Reference prefix: {manifest.get('reference_prefix', 'X')}\n"
        f"Block version: {manifest.get('version', 1)}\n"
        f"Block ID: {manifest.get('block_id', '')}\n\n"
        f"External pins ({len(manifest.get('pins', []))}):\n"
        + "\n".join(pin_lines)
        + "\n\nParameters:\n"
        + "\n".join(parameter_lines)
        + "\n\nImplementation variants:\n"
        + "\n".join(variant_lines)
        + "\n\nPCB layouts:\n"
        + "\n".join(layout_lines)
        + "\n\nConfiguration profiles:\n"
        + "\n".join(profile_lines)
        + f"\n\nLibrary asset: {record.path}"
    )


def _launch_block_asset(kicad, path: Path, description: str):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"The selected {description} is missing: {path}")
    executable_name = "pcbnew" if description == "PCB layout" else "eeschema"
    executable = kicad.get_kicad_binary_path(executable_name)
    if not executable:
        raise RuntimeError(f"KiCad did not provide an executable path for {description}.")
    return subprocess.Popen([executable, str(path)], close_fds=True)


def _show_library_browser(
    window,
    schematic,
    kicad,
    project_dir: Path,
    library_root: Path,
    editor_window_id: str | None,
) -> None:
    """Build the searchable library browser in the supplied Tk window."""
    window.title("KiCad Blocks - Library")
    window.geometry("1120x720")
    window.minsize(900, 590)
    current_root = [Path(library_root).expanduser().resolve()]
    records: list = []
    records_by_id: dict[str, object] = {}

    outer = ttk.Frame(window, padding=12)
    outer.pack(fill="both", expand=True)
    header = ttk.Frame(outer)
    header.pack(fill="x", pady=(0, 10))
    ttk.Label(
        header, text="Block Library", font=("TkDefaultFont", 16, "bold")
    ).pack(side="left")
    library_path = tk.StringVar(value=str(current_root[0]))
    ttk.Label(header, textvariable=library_path, foreground="#667785").pack(
        side="right", padx=(12, 0)
    )

    panes = ttk.Panedwindow(outer, orient="horizontal")
    panes.pack(fill="both", expand=True)
    left = ttk.Frame(panes, padding=(0, 0, 10, 0))
    right = ttk.Frame(panes, padding=(10, 0, 0, 0))
    panes.add(left, weight=1)
    panes.add(right, weight=3)

    ttk.Label(left, text="Search blocks").pack(anchor="w")
    search = tk.StringVar()
    search_entry = ttk.Entry(left, textvariable=search)
    search_entry.pack(fill="x", pady=(4, 3))
    ttk.Label(
        left,
        text="Type to filter · ↓ browse results · Enter place",
        foreground="#667785",
    ).pack(anchor="w", pady=(0, 6))
    tree_frame = ttk.Frame(left)
    tree_frame.pack(fill="both", expand=True)
    tree_frame.columnconfigure(0, weight=1)
    tree_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        tree_frame,
        columns=("pins", "variants", "layouts"),
        show="tree headings",
        selectmode="browse",
    )
    tree.heading("#0", text="Block")
    tree.heading("pins", text="Pins")
    tree.heading("variants", text="Variants")
    tree.heading("layouts", text="Layouts")
    tree.column("#0", width=175, minwidth=140, stretch=True)
    tree.column("pins", width=42, minwidth=35, stretch=False, anchor="center")
    tree.column("variants", width=60, minwidth=48, stretch=False, anchor="center")
    tree.column("layouts", width=55, minwidth=45, stretch=False, anchor="center")
    tree_y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree_x_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(
        yscrollcommand=tree_y_scroll.set,
        xscrollcommand=tree_x_scroll.set,
    )
    tree.grid(row=0, column=0, sticky="nsew")
    tree_y_scroll.grid(row=0, column=1, sticky="ns")
    tree_x_scroll.grid(row=1, column=0, sticky="ew")
    library_footer = ttk.Frame(left)
    library_footer.pack(fill="x", pady=(8, 0))

    name_label = ttk.Label(
        right, text="Select a block", font=("TkDefaultFont", 14, "bold")
    )
    name_label.pack(anchor="w")
    description_label = ttk.Label(
        right, text="", wraplength=650, justify="left", foreground="#53616b"
    )
    description_label.pack(anchor="w", pady=(4, 8))
    preview_box = ttk.LabelFrame(right, text="Facade preview", padding=6)
    preview_box.pack(fill="x")
    preview_frame = ttk.Frame(preview_box)
    preview_frame.pack(fill="x", expand=True)
    preview = tk.Canvas(
        preview_frame, height=250, background="#ffffff", highlightthickness=1,
        highlightbackground="#c8d0d6",
    )
    preview_scroll = ttk.Scrollbar(
        preview_frame, orient="vertical", command=preview.yview
    )
    preview.configure(yscrollcommand=preview_scroll.set)
    preview.pack(side="left", fill="x", expand=True)
    preview_scroll.pack(side="right", fill="y")
    details_box = ttk.LabelFrame(right, text="Block overview", padding=6)
    details_box.pack(fill="both", expand=True, pady=(8, 0))
    details_frame = ttk.Frame(details_box)
    details_frame.pack(fill="both", expand=True)
    details = tk.Text(
        details_frame, height=12, wrap="word", relief="flat",
        background="#f7f9fa", font=("TkFixedFont", 9),
    )
    details_scroll = ttk.Scrollbar(
        details_frame, orient="vertical", command=details.yview
    )
    details.configure(yscrollcommand=details_scroll.set)
    details.pack(side="left", fill="both", expand=True)
    details_scroll.pack(side="right", fill="y")
    details.configure(state="disabled")

    selected_record = [None]

    def set_details(record):
        selected_record[0] = record
        if record is None:
            name_label.configure(
                text="No matching blocks" if search.get().strip() else "No blocks in this library"
            )
            description_label.configure(text="")
            details.configure(state="normal")
            details.delete("1.0", "end")
            details.configure(state="disabled")
            preview.delete("all")
            return
        name_label.configure(text=record.name)
        description_label.configure(
            text=str(record.manifest.get("description", "") or "No description provided.")
        )
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert("1.0", _block_overview_text(record))
        details.configure(state="disabled")
        _draw_facade_preview(preview, record)

    def render_records(*_args):
        visible = _filter_library_records(records, search.get())
        preferred = _preferred_library_selection(visible, selected_record[0])
        children = tree.get_children()
        if children:
            tree.delete(*children)
        for record in visible:
            block_id = str(record.manifest["block_id"])
            tree.insert(
                "", "end", iid=block_id, text=record.name,
                values=(
                    len(record.manifest.get("pins", [])),
                    len(record.manifest.get("variants", [])),
                    len(_layout_choices(record)),
                ),
            )
        library_path.set(f"{current_root[0]}  -  {len(visible)} of {len(records)} blocks")
        if preferred is not None:
            preferred_id = str(preferred.manifest["block_id"])
            tree.selection_set(preferred_id)
            tree.focus(preferred_id)
        set_details(preferred)

    def refresh_records(library_root: Path | None = None):
        candidate_root = (
            Path(library_root).expanduser().resolve()
            if library_root is not None else current_root[0]
        )
        try:
            refreshed = list_blocks(candidate_root)
            if candidate_root != current_root[0]:
                candidate_root = set_library_root(candidate_root)
        except Exception as exc:
            LOG.exception("Could not refresh the block library at %s", candidate_root)
            messagebox.showerror(
                "Refresh Block Library",
                f"Could not read the selected library:\n{candidate_root}\n\n{exc}",
                parent=window,
            )
            return False
        current_root[0] = candidate_root
        records[:] = refreshed
        records_by_id.clear()
        records_by_id.update(
            {str(record.manifest["block_id"]): record for record in records}
        )
        render_records()
        return True

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
                    "it again using the KiCad Blocks toolbar action inside Schematic Editor."
                )
            _register_block(project_dir, record, kicad)
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
        chooser.title(f"Open implementation - {record.name}")
        chooser.transient(window)
        chooser.grab_set()
        frame = ttk.Frame(chooser, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Select implementation variant:").pack(anchor="w")
        choice = tk.StringVar(value=str(variants[0]["value"]))
        combo = ttk.Combobox(
            frame, textvariable=choice,
            values=[str(item["value"]) for item in variants],
            state="readonly", width=32,
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

    def open_layout(_event=None):
        record = selected_record[0]
        if record is None:
            return
        layouts = _layout_choices(record)
        if not layouts:
            messagebox.showinfo(
                "PCB Layout", f"{record.name} has no stored PCB layout.", parent=window
            )
            return
        chooser = tk.Toplevel(window)
        chooser.title(f"Open PCB layout - {record.name}")
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
        combo = ttk.Combobox(frame, textvariable=choice, values=labels, state="readonly", width=36)
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

    def change_library():
        selected = filedialog.askdirectory(
            title="Select shared KiCad Blocks library",
            initialdir=str(current_root[0]),
            parent=window,
        )
        if selected:
            refresh_records(Path(selected))

    ttk.Button(library_footer, text="Change library...", command=change_library).pack(side="left")
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
    preview.bind("<Configure>", lambda _event: (
        _draw_facade_preview(preview, selected_record[0])
        if selected_record[0] is not None else None
    ))

    actions = ttk.Frame(right)
    actions.pack(fill="x", pady=(10, 0))
    ttk.Button(actions, text="Place at cursor", command=place_selected).pack(side="left")
    ttk.Button(actions, text="Open implementation...", command=open_implementation).pack(
        side="left", padx=(8, 0)
    )
    ttk.Button(actions, text="Open PCB layout...", command=open_layout).pack(
        side="left", padx=(8, 0)
    )
    ttk.Button(actions, text="Refresh", command=refresh_records).pack(side="right")
    ttk.Label(
        right,
        text="Eeschema takes over during placement. Restore this window after Esc to browse again.",
        foreground="#667785",
        wraplength=680,
    ).pack(anchor="w", pady=(5, 0))
    refresh_records()
    search_entry.focus_set()


def run_library_browser() -> None:
    """Launch the searchable block library browser as a standalone KiCad action."""
    _load_tkinter()
    editor_window_id = _capture_eeschema_window_id()
    from kipy import KiCad

    kicad = KiCad()
    schematic = kicad.get_schematic()
    if schematic is None:
        raise RuntimeError("No active schematic was found. Open and save a project first.")
    project_dir = project_directory(schematic)
    library_root = default_library_root()
    library_root.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    _show_library_browser(
        root, schematic, kicad, project_dir, library_root, editor_window_id
    )
    root.mainloop()


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
    """Standalone KiCad add-on action for opening the selected block facade."""
    _load_tkinter()
    parent = tk.Tk()
    parent.withdraw()
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError("No active schematic was found. Open a project first.")
        project_dir = project_directory(schematic)
        library_root = default_library_root()
        if not library_root.is_dir():
            raise FileNotFoundError(
                f"KiCad Blocks library was not found: {library_root}. "
                "Use the Manager to create or configure a block library first."
            )
        _open_selected_block_implementation(
            library_root, schematic, kicad, project_dir, parent
        )
    except Exception as exc:
        LOG.exception("Open Block Implementation failed")
        messagebox.showerror("Open Block Implementation", str(exc), parent=parent)
    finally:
        with contextlib.suppress(tk.TclError):
            parent.destroy()


def _export_recursive_bom(root: Path, parent, schematic=None, project_dir: Path | None = None) -> None:
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
        record = _pick_record(list_blocks(root), parent, "Export Recursive Block BOM")
    if record is None:
        return
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
        record, Path(output), find_kicad_cli(), root, parameter_values=parameter_values
    )
    messagebox.showinfo(
        "Block BOM Exported",
        f"Exported {result['row_count']} rows from {result['block_count']} block(s):\n"
        f"{result['output']}",
        parent=parent,
    )


def _validate_shared_library(root: Path, parent) -> None:
    """Validate every block's manifest, assets, profiles, dependencies and ERC."""
    output = filedialog.askdirectory(
        title="Select a folder for the library validation report",
        parent=parent,
    )
    if not output:
        return
    result = validate_block_library(
        root,
        Path(output),
        find_kicad_cli(),
        run_erc=True,
    )
    details = []
    for item in result["blocks"]:
        status = str(item["status"]).upper()
        detail = f"{status}: {item['name']}"
        problems = item.get("errors", []) + item.get("warnings", [])
        if problems:
            detail += "\\n  " + "\\n  ".join(problems)
        details.append(detail)
    message = (
        f"Validated {result['block_count']} block(s).\\n"
        f"Passed: {result['passed_count']}  |  Warnings: {result['warning_count']}  |  "
        f"Failed: {result['failed_count']}\\n\\n"
        + ("\\n\\n".join(details) if details else "The library contains no block assets.")
        + f"\\n\\nMachine-readable report: {result['report']}"
    )
    if result["status"] == "failed":
        messagebox.showwarning("Block Library Validation — problems found", message, parent=parent)
    elif result["status"] == "warnings":
        messagebox.showwarning("Block Library Validation — ERC warnings", message, parent=parent)
    else:
        messagebox.showinfo("Block Library Validation Passed", message, parent=parent)


def _validate_recursive_erc(root: Path, parent, schematic=None, project_dir: Path | None = None) -> None:
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
        record = _pick_record(list_blocks(root), parent, "Validate Block Tree (ERC)")
    if record is None:
        return
    output = filedialog.askdirectory(
        title="Select a folder for recursive ERC reports",
        parent=parent,
    )
    if not output:
        return
    reports = validate_block_tree(
        record, Path(output), find_kicad_cli(), root, parameter_values=parameter_values
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
    record = _pick_record(list_blocks(root), parent, "Publish Library Block")
    if record is None:
        return
    result = refresh_library_block(record)
    if result["changed"]:
        _reload_symbol_library(kicad, LIBRARY_NICKNAME)
    messagebox.showinfo(
        "Library Block Published",
        f"Version: {result['version']}\n"
        f"Changed files: {', '.join(result['changed']) or 'none'}\n"
        "Local copies can now synchronize from this revision.",
        parent=parent,
    )


def _block_record_for_instance(library_root: Path, project_dir: Path, instance):
    """Resolve the selected instance to its local copy or shared library block."""
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
    records = list_blocks(root)
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
        result = compose_block_implementation(updated, root, find_kicad_cli())
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


def run() -> None:
    """Open a small action dashboard for the active schematic document."""
    _load_tkinter()
    editor_window_id = _capture_eeschema_window_id()
    root = tk.Tk()
    root.title("KiCad Blocks")
    root.geometry("700x720")
    root.minsize(560, 620)
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError("No active schematic was found. Open and save a project first.")
        project_dir = project_directory(schematic)
        library_root = default_library_root()
        library_root.mkdir(parents=True, exist_ok=True)

        actions = [
            ("create", "Create block from selected sheet", "Select one hierarchical sheet. Its hierarchical labels automatically define the facade pins and electrical types."),
            ("place", "Browse & place block", "Search the library, preview a facade and place it at the mouse cursor."),
            ("configure", "Configure block instance", "Choose a linked variant, parameter and PCB layout profile, then customize per-instance values."),
            ("local", "Make local copy", "Give the selected instance an independent implementation."),
            ("sync", "Synchronize local copy", "Publish and pull non-conflicting local/library edits."),
            ("edit", "Open implementation", "Open the selected facade's resolved KB_VARIANT in Eeschema."),
            ("edit-facade", "Edit facade symbol", "Open the selected block's facade library in KiCad Symbol Editor."),
            ("publish", "Publish library block", "Publish edits to a shared library block."),
            ("bom", "Recursive BOM", "Export the selected block and all nested components."),
            ("erc", "Recursive ERC", "Run ERC on the selected block and dependencies."),
            ("validate-library", "Validate block library", "Check every block's manifest, assets, profiles, dependencies and recursive ERC."),
            ("pcb", "Apply one PCB layout", "Merge one selected block layout into a new board copy."),
            ("pcb-many", "Apply multiple PCB layouts", "Merge layouts for multiple selected block instances."),
            ("nest", "Nest block", "Compose a reusable block into another block."),
        ]
        selected_action = tk.StringVar(value="create")
        outer = ttk.Frame(root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text="Reusable schematic blocks",
            font=("TkDefaultFont", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="Choose an operation for the active KiCad schematic. "
            "Select block symbols in Eeschema before running instance actions.",
            wraplength=610,
            justify="left",
        ).pack(anchor="w", pady=(4, 14))
        choice_pane = ttk.Frame(outer)
        choice_pane.pack(fill="both", expand=True)
        canvas = tk.Canvas(choice_pane, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(choice_pane, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        scroll_content = ttk.Frame(canvas)
        scroll_window = canvas.create_window(
            (0, 0), window=scroll_content, anchor="nw"
        )
        scroll_content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(scroll_window, width=event.width),
        )
        choices = ttk.LabelFrame(scroll_content, text="Actions", padding=10)
        choices.pack(fill="both", expand=True)
        for key, label, description in actions:
            row = ttk.Frame(choices)
            row.pack(fill="x", pady=4)
            ttk.Radiobutton(
                row, text=label, value=key, variable=selected_action
            ).pack(anchor="w")
            ttk.Label(
                row, text=description, foreground="#555555", wraplength=570,
                justify="left",
            ).pack(anchor="w", padx=(25, 0))
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(14, 0))

        def dispatch() -> None:
            action = selected_action.get()
            try:
                if action == "create":
                    library = _choose_library_root(root)
                    _create_and_place(
                        library, schematic, kicad, project_dir, root, editor_window_id
                    )
                elif action == "place":
                    _show_library_browser(
                        tk.Toplevel(root), schematic, kicad, project_dir,
                        library_root, editor_window_id
                    )
                elif action == "configure":
                    _show_selected_block_configuration(
                        schematic, project_dir, library_root, root
                    )
                elif action == "local":
                    path = make_selected_local(schematic, kicad, project_dir, library_root)
                    messagebox.showinfo(
                        "Local Copy Created",
                        f"The selected instance now uses an independent project-local block:\n{path}",
                        parent=root,
                    )
                elif action == "sync":
                    result = sync_selected_local(schematic, kicad, project_dir)
                    messagebox.showinfo(
                        "Local Block Synchronized",
                        "Published local edits: " + (", ".join(result["published"]) or "none") +
                        "\nPulled library edits: " + (", ".join(result["pulled"]) or "none") +
                        "\nConverged edits: " + (", ".join(result["converged"]) or "none"),
                        parent=root,
                    )
                elif action == "edit":
                    _open_selected_block_implementation(
                        library_root, schematic, kicad, project_dir, root
                    )
                elif action == "edit-facade":
                    _edit_facade_symbol(library_root, kicad, root)
                elif action == "publish":
                    _publish_block(library_root, kicad, project_dir, root)
                elif action == "bom":
                    library = _choose_library_root(root)
                    _export_recursive_bom(library, root, schematic, project_dir)
                elif action == "erc":
                    library = _choose_library_root(root)
                    _validate_recursive_erc(library, root, schematic, project_dir)
                elif action == "validate-library":
                    library = _choose_library_root(root)
                    _validate_shared_library(library, root)
                elif action == "pcb":
                    _apply_block_pcb_layout(schematic, kicad, project_dir, library_root, root)
                elif action == "pcb-many":
                    _apply_selected_block_pcb_layouts(
                        schematic, kicad, project_dir, library_root, root
                    )
                elif action == "nest":
                    library = _choose_library_root(root)
                    _add_nested_dependency(library, root, root)
            except Exception as exc:
                LOG.exception("KiCad Blocks action failed")
                messagebox.showerror("KiCad Blocks", str(exc), parent=root)

        ttk.Button(footer, text="Run action", command=dispatch).pack(side="right")
        ttk.Button(footer, text="Close", command=root.destroy).pack(side="right", padx=(0, 8))
        root.mainloop()
    except Exception as exc:
        LOG.exception("KiCad Blocks manager failed")
        messagebox.showerror("KiCad Blocks", str(exc), parent=root)
    finally:
        with contextlib.suppress(tk.TclError):
            root.destroy()
