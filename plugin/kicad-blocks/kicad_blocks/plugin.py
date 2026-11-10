"""KiCad IPC actions for opaque, library-backed schematic block symbols."""

from __future__ import annotations

import logging
from pathlib import Path

# The core block model and IPC helpers must remain importable in headless
# environments. Load Tkinter only when the interactive manager is invoked.
tk = None
filedialog = None
messagebox = None
simpledialog = None

from .composition import compose_block_implementation
from .model import parse_pins
from .operations import export_block_bom, find_kicad_cli, validate_block_tree
from .pcb_layout import merge_block_layout
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
    global tk, filedialog, messagebox, simpledialog
    if tk is not None:
        return

    import tkinter as tkinter_module
    from tkinter import filedialog as filedialog_module
    from tkinter import messagebox as messagebox_module
    from tkinter import simpledialog as simpledialog_module

    tk = tkinter_module
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


def _selected_symbol(schematic):
    selection = list(schematic.get_selection())
    symbols = [
        item for item in selection
        if hasattr(item, "definition") and hasattr(item.definition, "id")
    ]
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
    layout = filedialog.askopenfilename(
        title="Optional: select a pre-routed .kicad_pcb block layout",
        filetypes=[("KiCad PCB", "*.kicad_pcb"), ("All files", "*.*")],
        parent=parent,
    )
    layout_project = None
    if layout:
        layout_project = filedialog.askopenfilename(
            title="Optional: select the matching .kicad_pro netclass/settings file",
            filetypes=[("KiCad Project", "*.kicad_pro"), ("All files", "*.*")],
            parent=parent,
        )
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
        layout_file=Path(layout) if layout else None,
        layout_project_file=Path(layout_project) if layout_project else None,
    )
    record = _register_block(project_dir, record, kicad)
    instance = place_block(schematic, record, x_mm, y_mm)
    messagebox.showinfo(
        "Block Created",
        f"Created and placed {name} ({instance.reference}).\n\n"
        f"Library asset: {record.path}\n"
        f"Implementation schematic: {record.implementation}\n"
        f"PCB layout: {record.path / record.manifest['layout_file'] if record.manifest.get('layout_file') else 'none'}\n"
        f"PCB project settings: {record.path / record.manifest['layout_project_file'] if record.manifest.get('layout_project_file') else 'none'}\n\n"
        "Open the implementation file separately in Eeschema to edit the internal circuit.",
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


def _edit_block_implementation(root, schematic, kicad, project_dir, parent) -> None:
    """Open a selected local implementation or the shared library implementation."""
    selected = list(schematic.get_selection())
    if len(selected) == 1 and hasattr(selected[0], "definition"):
        record = local_copy_for_instance(project_dir, str(selected[0].id))
        if record is None:
            record = _library_record_for_instance(root, selected[0])
    else:
        record = _pick_record(list_blocks(root), parent, "Edit Block Implementation")
    if record is None:
        raise FileNotFoundError("The selected block implementation could not be found.")
    executable = kicad.get_kicad_binary_path("eeschema")
    import subprocess

    process = subprocess.Popen(
        [executable, str(record.implementation)],
        close_fds=True,
    )
    messagebox.showinfo(
        "Block Implementation",
        f"Opened {record.name} implementation in Eeschema.\n\n"
        "After editing a shared library block, run Publish. "
        "A project-local implementation remains independent until explicitly synchronized.",
        parent=parent,
    )
    return process

def _export_recursive_bom(root: Path, parent) -> None:
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
    result = export_block_bom(record, Path(output), find_kicad_cli(), root)
    messagebox.showinfo(
        "Block BOM Exported",
        f"Exported {result['row_count']} rows from {result['block_count']} block(s):\n"
        f"{result['output']}",
        parent=parent,
    )


def _validate_recursive_erc(root: Path, parent) -> None:
    record = _pick_record(list_blocks(root), parent, "Validate Block Tree (ERC)")
    if record is None:
        return
    output = filedialog.askdirectory(
        title="Select a folder for recursive ERC reports",
        parent=parent,
    )
    if not output:
        return
    reports = validate_block_tree(record, Path(output), find_kicad_cli(), root)
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


def _apply_block_pcb_layout(schematic, project_dir: Path, library_root: Path, parent) -> None:
    """Create a new project-board copy with the selected block's stored PCB layout."""
    instance = _selected_symbol(schematic)
    block = _block_record_for_instance(library_root, project_dir, instance)
    if not block.manifest.get("layout_file"):
        raise ValueError(f"Block {block.name!r} has no stored PCB layout.")

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
    )
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
        "The original project board and project settings were not modified. Review the generated board, "
        "then open it in PCB Editor."
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
        updated = add_dependency(parent_record, child_record, pin_map=pin_map)
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
        f"{updated.manifest['dependencies'][-1]['reference']}.\n"
        f"New facade symbols: {len(outcome['placed'])}; new net labels: {outcome['labels']}.\n"
        "The implementation is composed inline with no hierarchical sheet.",
        parent=parent_window,
    )


def run() -> None:
    """Main plugin action. All blocks are placed as normal schematic symbols."""
    _load_tkinter()
    root = tk.Tk()
    root.withdraw()
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError("No active schematic was found. Open and save a project first.")
        project_dir = project_directory(schematic)
        library_root = default_library_root()
        library_root.mkdir(parents=True, exist_ok=True)

        action = simpledialog.askstring(
            "KiCad Blocks",
            "Enter an action:\n\n"
            "create  — create a library block and place it\n"
            "place   — place an existing library block\n"
            "local   — make the selected block an independent local copy\n"
            "sync    — synchronize the selected local copy\n"
            "edit    — open the selected block implementation\n"
            "publish — publish edited library files as a new revision\n"
            "bom     — export merged BOM for a block and its dependencies\n"
            "erc     — run ERC on a block and all dependencies\n"
            "pcb     — apply the selected block PCB layout to a new board copy\n"
            "nest    — add a nested block dependency",
            initialvalue="create",
            parent=root,
        )
        if not action:
            return
        action = action.strip().casefold()
        if action == "create":
            library_root = _choose_library_root(root)
            _create_and_place(library_root, schematic, kicad, project_dir, root)
        elif action == "place":
            _place_existing(library_root, schematic, kicad, project_dir, root)
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
            _edit_block_implementation(library_root, schematic, kicad, project_dir, root)
        elif action == "publish":
            _publish_block(library_root, kicad, project_dir, root)
        elif action == "bom":
            library_root = _choose_library_root(root)
            _export_recursive_bom(library_root, root)
        elif action == "erc":
            library_root = _choose_library_root(root)
            _validate_recursive_erc(library_root, root)
        elif action == "pcb":
            _apply_block_pcb_layout(schematic, project_dir, library_root, root)
        elif action == "nest":
            library_root = _choose_library_root(root)
            _add_nested_dependency(library_root, root, root)
        else:
            raise ValueError("Choose one of: create, place, local, sync, edit, publish, bom, erc, pcb, nest.")
    except Exception as exc:
        LOG.exception("KiCad Blocks action failed")
        messagebox.showerror("KiCad Blocks", str(exc), parent=root)
    finally:
        root.destroy()
