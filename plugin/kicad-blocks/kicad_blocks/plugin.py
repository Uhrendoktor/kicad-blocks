"""KiCad IPC actions for opaque, library-backed schematic block symbols."""

from __future__ import annotations

import contextlib
import logging
import subprocess
from pathlib import Path

# The core block model and IPC helpers must remain importable in headless
# environments. Load Tkinter only when the interactive manager is invoked.
tk = None
ttk = None
filedialog = None
messagebox = None
simpledialog = None

from .composition import compose_block_implementation
from .model import (
    LAYOUT_FIELD_NAME,
    PARAMETER_FIELD_PREFIX,
    VARIANT_FIELD_NAME,
    parameter_field_name,
    parse_parameters,
    parse_pins,
    resolve_parameter_values,
    resolve_variant_implementation,
)
from .operations import export_block_bom, find_kicad_cli, validate_block_tree
from .pcb_layout import BlockLayoutPlacement, merge_block_layout, merge_block_layouts
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


def _show_selected_block_configuration(
    schematic, project_dir: Path, library_root: Path, parent
) -> None:
    """Show how to edit the instance-local values with KiCad's native UI."""
    instance = _selected_symbol(schematic)
    block = _block_record_for_instance(library_root, project_dir, instance)
    parameters = block.manifest.get("parameters", [])
    layouts = _layout_choices(block)
    current_values = _parameter_values_for_instance(instance, block)
    field_names = {
        str(getattr(field, "name", ""))
        for field in getattr(instance, "user_fields", ())
    }
    missing = [
        parameter_field_name(item["name"])
        for item in parameters
        if parameter_field_name(item["name"]) not in field_names
    ]
    if layouts and LAYOUT_FIELD_NAME not in field_names:
        missing.append(LAYOUT_FIELD_NAME)
    if missing:
        raise RuntimeError(
            f"Block instance {instance.reference} is missing field(s): "
            + ", ".join(missing)
            + ". Reload the facade from the block library and replace the instance."
        )

    parameter_lines = (
        "\n".join(
            f"  {parameter['name']} ({parameter['type']}) = "
            f"{current_values[parameter['name']]}"
            for parameter in parameters
        )
        if parameters
        else "  (none)"
    )
    layout_ids = (
        "\n".join(
            f"  {item.get('id')}: {item.get('name', item.get('id'))}"
            for item in layouts
        )
        if layouts
        else "  (none)"
    )
    selected_layout = next(
        (
            getattr(getattr(field, "text", None), "value", "")
            for field in getattr(instance, "user_fields", ())
            if str(getattr(field, "name", "")) == LAYOUT_FIELD_NAME
        ),
        "",
    )
    messagebox.showinfo(
        "Configure Block Instance",
        f"Selected block: {instance.reference} — {block.name}\n\n"
        "Edit these instance-local fields in KiCad itself:\n"
        "1. Close this dialog and select the block symbol.\n"
        "2. Press E (Symbol Properties).\n"
        "3. Open the Fields tab and edit the values below.\n"
        "4. Confirm the dialog and save the schematic.\n\n"
        f"Parameters:\n{parameter_lines}\n\n"
        f"PCB layout alternatives:\n{layout_ids}\n"
        f"Current KB_LAYOUT: {selected_layout or '(unset)'}\n\n"
        "The values are stored on this symbol instance, not in the shared block library. "
        "The recursive BOM/ERC and PCB layout actions read the saved values.",
        parent=parent,
    )


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
    if not records:
        raise ValueError("No blocks were found in the configured library.")
    names = [record.name for record in records]
    if len(records) == 1:
        return records[0]
    choice = simpledialog.askstring(
        title,
        "Choose a block by name:\n\n" + "\n".join(names),
        initialvalue=names[0],
        parent=parent,
    )
    if choice is None:
        return None
    for record in records:
        if record.name.casefold() == choice.strip().casefold():
            return record
    raise ValueError(f"No block named {choice!r} was found.")


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


def _create_and_place(root, schematic, kicad, project_dir, parent) -> None:
    name = simpledialog.askstring(
        "Create Block", "Block name:", initialvalue="Buck Converter", parent=parent
    )
    if not name:
        return
    pin_text = simpledialog.askstring(
        "Block Interface",
        "External pins, comma-separated as NAME:type.\n"
        "Types: input, output, bidirectional, tristate, passive.",
        initialvalue="VIN:input, GND:passive, VOUT:output, EN:input",
        parent=parent,
    )
    if not pin_text:
        return
    pins = parse_pins(pin_text)
    description = simpledialog.askstring(
        "Block Description", "Description (optional):", initialvalue="", parent=parent
    ) or ""
    parameter_text = simpledialog.askstring(
        "Block Parameters",
        "Optional typed parameters, comma-separated as NAME[:type]=DEFAULT.\n"
        "Types: string, number, integer, boolean. Example: OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true",
        initialvalue="",
        parent=parent,
    )
    if parameter_text is None:
        return
    parameters = parse_parameters(parameter_text)

    selected_variant_schematics = filedialog.askopenfilenames(
        title="Optional: select variant implementation schematics",
        filetypes=[("KiCad Schematic", "*.kicad_sch"), ("All files", "*.*")],
        parent=parent,
    )
    variant_files = []
    for variant_path in selected_variant_schematics:
        variant_path = Path(variant_path)
        variant_value = simpledialog.askstring(
            "Block Variant",
            f"Which exact KB_VARIANT value opens {variant_path.name}?\\n"
            "Example: 3V3 (the facade field may contain ${SUPPLY_VOLTAGE}).",
            initialvalue=variant_path.stem,
            parent=parent,
        )
        if variant_value is None:
            return
        variant_files.append({"value": variant_value, "path": variant_path})

    selected_layouts = filedialog.askopenfilenames(
        title="Optional: select one or more named PCB layout alternatives",
        filetypes=[("KiCad PCB", "*.kicad_pcb"), ("All files", "*.*")],
        parent=parent,
    )
    layouts = []
    for index, layout_path in enumerate(selected_layouts, start=1):
        layout_path = Path(layout_path)
        layout_name = simpledialog.askstring(
            f"PCB Layout {index}",
            "Name this layout alternative:",
            initialvalue=layout_path.stem,
            parent=parent,
        )
        if layout_name is None:
            return
        layout_project = filedialog.askopenfilename(
            title=f"Optional: select project settings for {layout_name}",
            filetypes=[("KiCad Project", "*.kicad_pro"), ("All files", "*.*")],
            parent=parent,
        )
        default_ports = ", ".join(f"{pin.name}:{pin.name}" for pin in pins)
        ports_text = simpledialog.askstring(
            f"PCB Layout Ports — {layout_name}",
            "Map each facade pin to the corresponding net name in this stored layout.\n"
            "Format: FACADE_PIN:LAYOUT_NET, comma-separated.",
            initialvalue=default_ports,
            parent=parent,
        )
        if ports_text is None:
            return
        ports = _parse_pin_net_map(ports_text, [pin.name for pin in pins])
        layouts.append({
            "name": layout_name.strip() or layout_path.stem,
            "path": layout_path,
            "project_path": Path(layout_project) if layout_project else None,
            "ports": ports,
        })

    x_mm = simpledialog.askfloat(
        "Placement", "X position in mm:", initialvalue=100.0, parent=parent
    )
    if x_mm is None:
        return
    y_mm = simpledialog.askfloat(
        "Placement", "Y position in mm:", initialvalue=100.0, parent=parent
    )
    if y_mm is None:
        return

    record = create_block(
        root,
        name,
        pins,
        description=description,
        layout_files=layouts if layouts else None,
        parameters=parameters,
        variant_files=variant_files,
    )
    record = _register_block(project_dir, record, kicad)
    instance = place_block(schematic, record, x_mm, y_mm)
    layout_names = ", ".join(item["name"] for item in record.manifest.get("layouts", [])) or "none"
    parameter_names = ", ".join(item["name"] for item in record.manifest.get("parameters", [])) or "none"
    messagebox.showinfo(
        "Block Created",
        f"Created and placed {name} ({instance.reference}).\n\n"
        f"Library asset: {record.path}\n"
        f"Implementation schematic: {record.implementation}\n"
        f"PCB layout alternatives: {layout_names}\n"
        f"Block parameters: {parameter_names}\n\n"
        "Open the implementation file separately in Eeschema to edit the internal circuit. "
        "Use KiCad's symbol Properties dialog to override parameter fields per instance.",
        parent=parent,
    )

def _place_existing(root, schematic, kicad, project_dir, parent) -> None:
    record = _pick_record(list_blocks(root), parent, "Place Block")
    if record is None:
        return
    x_mm = simpledialog.askfloat("Placement", "X position in mm:", initialvalue=100.0, parent=parent)
    if x_mm is None:
        return
    y_mm = simpledialog.askfloat("Placement", "Y position in mm:", initialvalue=100.0, parent=parent)
    if y_mm is None:
        return
    _register_block(project_dir, record, kicad)
    instance = place_block(schematic, record, x_mm, y_mm)
    messagebox.showinfo(
        "Block Placed", f"Placed {record.name} as {instance.reference}.", parent=parent
    )


def _field_value(instance, field_name: str) -> str:
    for field in getattr(instance, "user_fields", ()):
        if str(getattr(field, "name", "")) == field_name:
            return str(getattr(getattr(field, "text", None), "value", "") or "").strip()
    return ""


def _sheet_variables_for_symbol(schematic, instance) -> dict[str, str]:
    """Collect custom fields on hierarchical sheet symbols containing this occurrence."""
    symbol_path = {
        str(item) for item in getattr(getattr(instance, "path", None), "path", ())
    }
    variables: dict[str, str] = {}
    if not symbol_path:
        return variables
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
        if not (identifiers & symbol_path):
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

    position_text = simpledialog.askstring(
        "PCB block placement",
        "Enter the X,Y position in millimetres for the stored layout anchor.",
        initialvalue="100,100",
        parent=parent,
    )
    if position_text is None:
        return
    position_values = [part.strip() for part in position_text.split(",")]
    if len(position_values) != 2:
        raise ValueError("Placement must be two comma-separated millimetre coordinates.")
    try:
        position = (float(position_values[0]), float(position_values[1]))
    except ValueError as exc:
        raise ValueError("Placement coordinates must be numeric millimetres.") from exc

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

    result = merge_block_layout(
        source_board,
        block,
        Path(output_path),
        instance_reference=str(instance.reference),
        position_mm=position,
        pin_to_board_net=pin_map,
        layout_anchor_mm=anchor,
        rotation_deg=rotation,
        layout_id=layout.get("id"),
    )
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
        + ("The generated copy was opened in PCB Editor." if opened_in_editor else
           "Open the generated copy in PCB Editor to review it.")
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

        position_text = simpledialog.askstring(
            f"PCB position — {instance.reference}",
            "Position X,Y for the stored layout anchor in millimetres:",
            initialvalue=f"{100.0 + (index - 1) * 60.0:g},100",
            parent=parent,
        )
        if position_text is None:
            return
        position_parts = [part.strip() for part in position_text.split(",")]
        if len(position_parts) != 2:
            raise ValueError(f"Position for {instance.reference} must be X,Y.")
        try:
            position = (float(position_parts[0]), float(position_parts[1]))
        except ValueError as exc:
            raise ValueError(f"Position for {instance.reference} must be numeric.") from exc

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

    result = merge_block_layouts(source_board, placements, Path(output_path))
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
            ("create", "Create block", "Generate a facade, typed parameters and PCB layout alternatives."),
            ("place", "Place block", "Place an existing reusable block from the library."),
            ("configure", "Configure block instance", "Open guidance for editing per-instance parameters and PCB layout in KiCad Symbol Properties."),
            ("local", "Make local copy", "Give the selected instance an independent implementation."),
            ("sync", "Synchronize local copy", "Publish and pull non-conflicting local/library edits."),
            ("edit", "Open implementation", "Open the selected facade's resolved KB_VARIANT in Eeschema."),
            ("publish", "Publish library block", "Publish edits to a shared library block."),
            ("bom", "Recursive BOM", "Export the selected block and all nested components."),
            ("erc", "Recursive ERC", "Run ERC on the selected block and dependencies."),
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
                    _create_and_place(library, schematic, kicad, project_dir, root)
                elif action == "place":
                    library = _choose_library_root(root)
                    _place_existing(library, schematic, kicad, project_dir, root)
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
                elif action == "publish":
                    _publish_block(library_root, kicad, project_dir, root)
                elif action == "bom":
                    library = _choose_library_root(root)
                    _export_recursive_bom(library, root, schematic, project_dir)
                elif action == "erc":
                    library = _choose_library_root(root)
                    _validate_recursive_erc(library, root, schematic, project_dir)
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
