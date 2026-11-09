"""KiCad 11 schematic action for creating an encapsulated block."""

from __future__ import annotations

import logging
from pathlib import Path
import re
import tkinter as tk
from tkinter import messagebox, simpledialog

from .model import BlockPin, block_geometry, parse_pins, slugify
from .sheet_file import build_child_schematic, write_child_schematic

LOG = logging.getLogger("kicad_blocks")


def _uuid_text(value: object) -> str:
    """Normalize a KiCad KIID wrapper to a UUID string."""
    text = str(value).strip().strip("{}")
    match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        text,
    )
    if not match:
        raise RuntimeError(f"Could not read a UUID from KiCad identifier {value!r}")
    return match.group(0).lower()


def _root_uuid(schematic) -> str:
    """Find the active root sheet UUID using the hierarchy first, then file text."""
    try:
        root = schematic.get_hierarchy().root
        path_ids = root.path.path
        if path_ids:
            return _uuid_text(path_ids[0])
    except Exception:
        LOG.debug("Could not read root UUID from hierarchy", exc_info=True)

    native_text = schematic.get_as_string()
    match = re.search(
        r'\(kicad_sch\s+\(version\s+[^)]*\)\s+'
        r'\(generator\s+[^)]*\)\s+\(uuid\s+"?([0-9a-fA-F-]{36})',
        native_text,
    )
    if not match:
        match = re.search(r'\(uuid\s+"?([0-9a-fA-F-]{36})', native_text)
    if not match:
        raise RuntimeError("Could not determine the root sheet UUID. Save the project and retry.")
    return match.group(1).lower()


def _project_directory(schematic) -> tuple[Path, str]:
    project = schematic.get_project()
    raw_path = Path(project.path).expanduser()
    if raw_path.is_dir():
        root_dir = raw_path
    elif raw_path.suffix.lower() in {".kicad_pro", ".kicad_sch", ".pro"}:
        root_dir = raw_path.parent
    else:
        root_dir = raw_path.parent if raw_path.suffix else raw_path
    root_dir.mkdir(parents=True, exist_ok=True)
    project_name = getattr(project, "name", "") or root_dir.name
    return root_dir, project_name


def _create_sheet_symbol(
    name: str,
    filename: str,
    pins: list[BlockPin],
    x_mm: float,
    y_mm: float,
):
    """Build one native KiCad hierarchical sheet symbol with two-sided pins."""
    from kipy.geometry import Vector2
    from kipy.schematic_types import SheetPin, SheetSymbol

    height_mm, left_count, _right_count = block_geometry(len(pins))
    width_mm = 38.1
    sheet = SheetSymbol()
    sheet.position = Vector2.from_xy_mm(x_mm, y_mm)
    sheet.size = Vector2.from_xy_mm(width_mm, height_mm)
    sheet.name_field.text.value = name
    sheet.name_field.visible = True
    sheet.filename_field.text.value = filename
    sheet.filename_field.visible = False
    sheet.fields_autoplaced = True

    for index, pin_def in enumerate(pins):
        pin = SheetPin()
        if index < left_count:
            row = index
            px = x_mm
            py = y_mm + 5.08 + row * 5.08
            side = 0  # SHEET_SIDE.LEFT
        else:
            row = index - left_count
            px = x_mm + width_mm
            py = y_mm + 5.08 + row * 5.08
            side = 1  # SHEET_SIDE.RIGHT
        pin.position = Vector2.from_xy_mm(px, py)
        pin.text.value = pin_def.name
        pin.shape = pin_def.shape
        pin.side = side
        sheet.pins.append(pin)
    return sheet


def _collect_user_input() -> tuple[str, list[BlockPin], float, float] | None:
    root = tk.Tk()
    root.withdraw()
    try:
        name = simpledialog.askstring(
            "Create Encapsulated Block",
            "Block name (shown on the parent schematic):",
            initialvalue="DC_DC_Converter",
            parent=root,
        )
        if not name:
            return None
        pin_spec = simpledialog.askstring(
            "Block Interface",
            "Pins, comma-separated as NAME:type\n"
            "Types: input, output, bidirectional, tristate, passive",
            initialvalue="VIN:input, GND:passive, VOUT:output, EN:input",
            parent=root,
        )
        if not pin_spec:
            return None
        pins = parse_pins(pin_spec)
        pos = simpledialog.askstring(
            "Block Placement",
            "Top-left position in mm (x,y):",
            initialvalue="100,100",
            parent=root,
        )
        if not pos:
            return None
        try:
            x_mm, y_mm = (float(part.strip()) for part in pos.split(",", 1))
        except Exception as exc:
            raise ValueError(
                "Position must be two numbers separated by a comma, e.g. 100,100."
            ) from exc
        if not (-1000 <= x_mm <= 1000 and -1000 <= y_mm <= 1000):
            raise ValueError("Position must be between -1000 mm and 1000 mm.")
        return name.strip(), pins, x_mm, y_mm
    finally:
        root.destroy()


def create_encapsulated_block(
    schematic,
    name: str,
    pins: list[BlockPin],
    x_mm: float,
    y_mm: float,
) -> Path:
    """Create a child sheet and add its native hierarchical sheet symbol."""
    from kipy.geometry import Vector2  # noqa: F401 - fail early if IPC bindings are absent

    project_dir, _project_name = _project_directory(schematic)
    root_uuid = _root_uuid(schematic)
    block_slug = slugify(name)
    block_dir = project_dir / ".kicad_blocks"
    block_dir.mkdir(parents=True, exist_ok=True)

    existing = list(block_dir.glob(f"{block_slug}_*.kicad_sch"))
    child_path = block_dir / f"{block_slug}_{len(existing) + 1:02d}.kicad_sch"
    relative_path = child_path.relative_to(project_dir).as_posix()
    page_number = str(len(schematic.get_sheet_symbols()) + 2)

    sheet = _create_sheet_symbol(name, relative_path, pins, x_mm, y_mm)
    sheet.page_number = page_number

    commit = schematic.begin_commit()
    child_created = False
    try:
        created = schematic.create_items([sheet])
        if not created:
            raise RuntimeError("KiCad did not create the hierarchical sheet symbol.")
        sheet = created[0]
        sheet_uuid = _uuid_text(sheet.id)
        child_contents = build_child_schematic(
            block_name=name,
            pins=pins,
            root_uuid=root_uuid,
            sheet_uuid=sheet_uuid,
            page_number=page_number,
        )
        write_child_schematic(child_path, child_contents)
        child_created = True
        schematic.push_commit(commit, f"Create encapsulated block: {name}")
        schematic.save()
    except Exception:
        try:
            schematic.drop_commit(commit)
        except Exception:
            LOG.exception("Failed to roll back schematic transaction")
        if child_created:
            try:
                child_path.unlink(missing_ok=True)
            except OSError:
                LOG.exception("Could not remove incomplete child schematic %s", child_path)
        raise
    return child_path


def run() -> None:
    """Plugin action entrypoint called by KiCad."""
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError("No active schematic was found. Open a project schematic first.")
        user_input = _collect_user_input()
        if user_input is None:
            return
        name, pins, x_mm, y_mm = user_input
        child = create_encapsulated_block(schematic, name, pins, x_mm, y_mm)
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Encapsulated Block Created",
            f"Created '{name}' with {len(pins)} pins.\n\n"
            f"Implementation sheet:\n{child}\n\n"
            "Double-click the sheet-backed block to edit its internal circuit.",
            parent=root,
        )
        root.destroy()
    except Exception as exc:
        LOG.exception("Create Encapsulated Block failed")
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Encapsulated Blocks", str(exc), parent=root)
            root.destroy()
        except Exception:
            print(f"Encapsulated Blocks failed: {exc}")
