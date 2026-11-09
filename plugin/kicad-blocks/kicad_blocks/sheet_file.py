"""Generate the small child schematic used by an encapsulated block.

The parent/child connectivity remains native KiCad hierarchy: each SheetPin on
its SheetSymbol is paired with a HierarchicalLabel of the same name here.
"""

from __future__ import annotations

from pathlib import Path
import uuid

from .model import BlockPin


def _q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_child_schematic(
    block_name: str,
    pins: list[BlockPin],
    root_uuid: str,
    sheet_uuid: str,
    page_number: str = "2",
) -> str:
    """Return a minimal native .kicad_sch child sheet with matching ports."""
    left_count = (len(pins) + 1) // 2
    lines = [
        "(kicad_sch",
        "  (version 20250114)",
        '  (generator "eeschema")',
        f"  (uuid {_q(sheet_uuid)})",
        '  (paper "A4")',
        "  (lib_symbols)",
        f'  (text {_q("Encapsulated block: " + block_name)} (at 101.6 35.56 0)',
        "    (effects (font (size 1.27 1.27)) (justify left bottom))",
        f'    (uuid {_q(str(uuid.uuid4()))})',
        "  )",
        '  (text "Add the internal circuit below. Match hierarchical labels to the parent pins." (at 101.6 40.64 0)',
        "    (effects (font (size 1.0 1.0)) (justify left bottom))",
        f'    (uuid {_q(str(uuid.uuid4()))})',
        "  )",
    ]

    for index, pin in enumerate(pins):
        if index < left_count:
            row = index
            x = 76.2
            y = 76.2 + row * 12.7
            angle = 0
            justify = "left"
        else:
            row = index - left_count
            x = 152.4
            y = 76.2 + row * 12.7
            angle = 180
            justify = "right"
        lines.extend(
            [
                f"  (hierarchical_label {_q(pin.name)} (shape {pin.electrical_type}) (at {x:.4f} {y:.4f} {angle})",
                f"    (effects (font (size 1.27 1.27)) (justify {justify}))",
                f'    (uuid {_q(str(uuid.uuid4()))})',
                "  )",
            ]
        )

    hierarchy_path = f"/{root_uuid}/{sheet_uuid}"
    lines.extend(
        [
            "  (sheet_instances",
            f"    (path {_q(hierarchy_path)} (page {_q(page_number)}))",
            "  )",
            "  (symbol_instances)",
            "  (embedded_fonts no)",
            ")",
            "",
        ]
    )
    return "\n".join(lines)


def write_child_schematic(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing block sheet: {path}")
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(contents, encoding="utf-8", newline="\n")
    temp_path.replace(path)
