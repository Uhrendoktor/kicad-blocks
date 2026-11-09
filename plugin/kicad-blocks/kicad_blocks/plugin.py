"""KiCad 11 schematic IPC action for creating a sheet-backed block."""

from __future__ import annotations

import logging
from pathlib import Path
from .model import BlockPin, block_geometry, parse_pins, slugify

LOG = logging.getLogger("kicad_blocks")

_SHAPE_NAMES = {
    "input": "SLSH_INPUT",
    "output": "SLSH_OUTPUT",
    "bidirectional": "SLSH_BIDI",
    "tristate": "SLSH_TRISTATE",
    "passive": "SLSH_PASSIVE",
}


def _project_directory(schematic) -> Path:
    """Return the current KiCad project directory."""
    project = schematic.get_project()
    raw_path = Path(project.path).expanduser()
    if raw_path.is_file():
        raw_path = raw_path.parent
    if raw_path.suffix.lower() == ".kicad_pro":
        raw_path = raw_path.parent
    project_dir = raw_path.resolve()
    if not project_dir.is_dir():
        raise RuntimeError(
            f"KiCad did not report an existing project directory: {project_dir}. "
            "Save the project before creating a block."
        )
    return project_dir


def _next_child_filename(project_dir: Path, name: str) -> str:
    """Find a child filename that does not collide with an existing project file."""
    stem = slugify(name)
    index = 1
    while True:
        filename = f"{stem}_block_{index:02d}.kicad_sch"
        if not (project_dir / filename).exists():
            return filename
        index += 1


def _shape_enum(electrical_type: str):
    from kipy.proto.schematic.schematic_types_pb2 import SchematicLabelShape

    return getattr(SchematicLabelShape, _SHAPE_NAMES[electrical_type])


def _build_sheet_symbol(
    name: str,
    absolute_filename: str,
    x_mm: float,
    y_mm: float,
    pins: list[BlockPin],
):
    """Build a sheet symbol, its visible fields and its electrical pins."""
    from kipy.geometry import Vector2
    from kipy.proto.common.types import HorizontalAlignment, VerticalAlignment
    from kipy.proto.schematic.schematic_types_pb2 import (
        SchematicLabelSpinStyle,
        SheetSide,
    )
    from kipy.schematic_types import SheetPin, SheetSymbol

    height_mm, left_count, _right_count = block_geometry(len(pins))
    width_mm = 38.1
    sheet = SheetSymbol()

    # Explicit field metadata and positions are important: uninitialized sheet
    # fields can be invisible or rendered at the origin.
    sheet.name_field.name = "Sheetname"
    sheet.name_field.text.value = name
    sheet.name_field.text.position = Vector2.from_xy_mm(x_mm, y_mm - 1.27)
    sheet.name_field.text.attributes.size = Vector2.from_xy_mm(1.27, 1.27)
    sheet.name_field.text.attributes.horizontal_alignment = HorizontalAlignment.HA_LEFT
    sheet.name_field.text.attributes.vertical_alignment = VerticalAlignment.VA_BOTTOM
    sheet.name_field.visible = True

    sheet.filename_field.name = "Sheetfile"
    sheet.filename_field.text.value = absolute_filename
    sheet.filename_field.text.position = Vector2.from_xy_mm(
        x_mm, y_mm + height_mm + 1.27
    )
    sheet.filename_field.text.attributes.size = Vector2.from_xy_mm(1.27, 1.27)
    sheet.filename_field.text.attributes.horizontal_alignment = HorizontalAlignment.HA_LEFT
    sheet.filename_field.text.attributes.vertical_alignment = VerticalAlignment.VA_TOP
    sheet.filename_field.visible = False

    sheet.position = Vector2.from_xy_mm(x_mm, y_mm)
    sheet.size = Vector2.from_xy_mm(width_mm, height_mm)

    for index, pin_definition in enumerate(pins):
        if index < left_count:
            row = index
            pin_x = x_mm
            side = SheetSide.SHS_LEFT
            spin = SchematicLabelSpinStyle.SLSS_RIGHT
        else:
            row = index - left_count
            pin_x = x_mm + width_mm
            side = SheetSide.SHS_RIGHT
            spin = SchematicLabelSpinStyle.SLSS_LEFT

        pin_y = y_mm + 5.08 + row * 5.08
        position = Vector2.from_xy_mm(pin_x, pin_y)
        pin = SheetPin()
        pin.text.value = pin_definition.name
        pin.position = position
        pin.text.position = position
        pin.side = side
        pin.spin_style = spin
        pin.shape = _shape_enum(pin_definition.electrical_type)
        sheet.pins.append(pin)

    return sheet


def _build_hierarchical_label(pin: BlockPin, x_mm: float, y_mm: float):
    """Create the child-sheet port paired with one sheet pin."""
    from kipy.geometry import Vector2
    from kipy.proto.schematic.schematic_types_pb2 import SchematicLabelSpinStyle
    from kipy.schematic_types import HierarchicalLabel

    position = Vector2.from_xy_mm(x_mm, y_mm)
    label = HierarchicalLabel()
    label.text.value = pin.name
    label.position = position
    label.text.position = position
    label.spin_style = SchematicLabelSpinStyle.SLSS_LEFT
    label.shape = _shape_enum(pin.electrical_type)
    return label


def _commit(document, message: str, operation):
    """Run one API transaction and roll it back if an operation fails."""
    commit = document.begin_commit()
    try:
        result = operation()
        document.push_commit(commit, message)
        return result
    except Exception:
        document.drop_commit(commit)
        raise


def _find_child_instance(schematic, filename: str, name: str):
    for instance in schematic.get_hierarchy():
        if instance.filename.replace("\\", "/") == filename:
            return instance
    raise RuntimeError(
        f"KiCad created the sheet symbol but did not add its implementation "
        f"({filename}) to the hierarchy."
    )


def create_encapsulated_block(
    schematic,
    name: str,
    pins: list[BlockPin],
    x_mm: float,
    y_mm: float,
) -> Path:
    """Create a native hierarchical sheet symbol and matching child-sheet ports.

    KiCad's own document model must create the child screen. The API's current
    sheet-creation path requires an absolute filename for the initial create,
    followed by an update to the project-relative Sheetfile path.
    """
    project_dir = _project_directory(schematic)
    existing_names = {
        sheet.name_field.text.value.casefold()
        for sheet in schematic.get_sheet_symbols()
    }
    if name.casefold() in existing_names:
        raise ValueError(f"A sheet named {name!r} already exists on this sheet.")

    relative_filename = _next_child_filename(project_dir, name)
    child_path = project_dir / relative_filename
    absolute_filename = str(child_path)
    sheet = _build_sheet_symbol(
        name, absolute_filename, x_mm, y_mm, pins
    )

    sheet_created = False
    try:
        created = _commit(
            schematic,
            f"Create encapsulated block: {name}",
            lambda: schematic.create_items([sheet]),
        )
        if not created:
            raise RuntimeError("KiCad did not create the hierarchical sheet symbol.")
        sheet = created[0]
        sheet_created = True

        # Change the Sheetfile path only after KiCad has resolved the absolute
        # path and created its internal child-screen object.
        sheet.filename_field.text.value = relative_filename
        updated = _commit(
            schematic,
            f"Set implementation filename and pins: {name}",
            lambda: schematic.update_items([sheet]),
        )
        if updated:
            sheet = updated[0]

        child_instance = _find_child_instance(
            schematic, relative_filename, name
        )
        child_schematic = schematic.for_sheet(child_instance)

        _height, left_count, _right_count = block_geometry(len(pins))
        labels = []
        for index, pin in enumerate(pins):
            if index < left_count:
                row = index
                label_x = 76.2
            else:
                row = index - left_count
                label_x = 152.4
            label_y = 76.2 + row * 7.62
            labels.append(_build_hierarchical_label(pin, label_x, label_y))

        _commit(
            child_schematic,
            f"Create interface ports: {name}",
            lambda: child_schematic.create_items(labels),
        )

        schematic.save()
        if not child_path.is_file():
            raise RuntimeError(
                "KiCad accepted the block but did not save its child schematic. "
                "Check the KiCad API log and project write permissions."
            )
        return child_path

    except Exception:
        # Do not leave a dangling parent sheet when a child-screen operation
        # fails. Preserve the user's other unsaved schematic edits.
        if sheet_created:
            try:
                _commit(
                    schematic,
                    f"Remove incomplete block: {name}",
                    lambda: schematic.remove_items([sheet]),
                )
                schematic.save()
            except Exception:
                LOG.exception("Could not remove the incomplete sheet symbol.")
            try:
                child_path.unlink(missing_ok=True)
            except OSError:
                LOG.exception("Could not remove incomplete child file %s", child_path)
        raise


def _collect_user_input() -> tuple[str, list[BlockPin], float, float] | None:
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    try:
        name = simpledialog.askstring(
            "Create Encapsulated Block",
            "Block name:",
            initialvalue="DC_DC_Converter",
            parent=root,
        )
        if not name or not name.strip():
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


def run() -> None:
    """Plugin action entrypoint called by KiCad."""
    try:
        from kipy import KiCad

        schematic = KiCad().get_schematic()
        if schematic is None:
            raise RuntimeError("No active schematic was found. Open a project first.")

        values = _collect_user_input()
        if values is None:
            return
        name, pins, x_mm, y_mm = values
        child = create_encapsulated_block(schematic, name, pins, x_mm, y_mm)

        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Encapsulated Block Created",
            f"Created '{name}' with {len(pins)} pins.\n\n"
            f"Implementation sheet:\n{child}\n\n"
            "Double-click the sheet symbol to edit its internal circuit.",
            parent=root,
        )
        root.destroy()
    except Exception as exc:
        LOG.exception("Create Encapsulated Block failed")
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Encapsulated Blocks", str(exc), parent=root)
            root.destroy()
        except Exception:
            print(f"Encapsulated Blocks failed: {exc}")
