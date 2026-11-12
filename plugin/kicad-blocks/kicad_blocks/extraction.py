"""Create opaque block assets from ordinary KiCad hierarchical sheets.

The hierarchical labels in the source subsheet are the interface declaration.
Their names become facade pin names and their shapes map to KiCad pin electrical
types. No separate manual pin list is accepted for this creation path.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from .model import BlockPin
from .pcb_layout import _Quoted, _all, _direct, _dump, _head, parse_sexpr

_LABEL_SHAPES = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "tri_state": "tristate",
    "passive": "passive",
}
_PIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_./+-]*\Z")


def _parse_selection(selection_fragment: str) -> list:
    """Parse the clipboard-format fragment returned by Schematic.get_selection_as_string."""
    fragment = str(selection_fragment or "").strip()
    if not fragment:
        raise ValueError(
            "Select exactly one hierarchical sheet symbol in the parent schematic."
        )
    try:
        wrapper = parse_sexpr(f"(selection {fragment})")
    except ValueError as exc:
        raise ValueError(f"KiCad returned an invalid selected-item fragment: {exc}") from exc
    if _head(wrapper) != "selection":
        raise ValueError("KiCad returned an unexpected selected-item format.")
    return wrapper[1:]


def selected_sheet_file(selection_fragment: str, project_directory: Path) -> Path:
    """Resolve the Sheetfile path from exactly one selected hierarchical sheet symbol."""
    nodes = _parse_selection(selection_fragment)
    if len(nodes) != 1 or _head(nodes[0]) != "sheet":
        raise ValueError(
            "Select exactly one hierarchical sheet symbol. The block will be built from "
            "that sheet's complete subschematic."
        )

    sheet_file = None
    for prop in _all(nodes[0], "property"):
        if len(prop) >= 3 and str(prop[1]) == "Sheetfile":
            sheet_file = str(prop[2]).strip()
            break
    if not sheet_file:
        raise ValueError("The selected hierarchical sheet has no Sheetfile property.")

    candidate = Path(sheet_file).expanduser()
    if not candidate.is_absolute():
        candidate = Path(project_directory).expanduser() / candidate
    candidate = candidate.resolve()
    if candidate.suffix.casefold() != ".kicad_sch" or not candidate.is_file():
        raise FileNotFoundError(
            f"The selected sheet schematic does not exist or is not a .kicad_sch file: {candidate}"
        )
    return candidate


def extract_hierarchical_pins(schematic_tree: list) -> list[BlockPin]:
    """Derive facade pins from all hierarchical labels in a parsed source subsheet.

    Identically named hierarchical labels represent the same sheet interface signal.
    They collapse to one facade pin when their shapes agree. Conflicting shapes are
    rejected rather than choosing an arbitrary electrical pin type.
    """
    if _head(schematic_tree) != "kicad_sch":
        raise ValueError("The selected source file is not a KiCad schematic document.")
    labels = _all(schematic_tree, "hierarchical_label")
    if not labels:
        raise ValueError(
            "The selected sheet has no hierarchical labels. Add hierarchical labels "
            "to every signal that should appear as a facade pin, then create the block."
        )

    pins: list[BlockPin] = []
    by_name: dict[str, BlockPin] = {}
    for label in labels:
        if len(label) < 2:
            raise ValueError("A hierarchical label has no name.")
        name = str(label[1]).strip()
        if not _PIN_NAME.fullmatch(name):
            raise ValueError(
                f"Hierarchical label {name!r} cannot be used as a facade pin name. "
                "Use letters, numbers, _, ., /, + or -."
            )

        shape_node = _direct(label, "shape")
        if shape_node is None or len(shape_node) < 2:
            raise ValueError(f"Hierarchical label {name!r} has no shape.")
        shape = str(shape_node[1])
        electrical_type = _LABEL_SHAPES.get(shape)
        if electrical_type is None:
            raise ValueError(
                f"Hierarchical label {name!r} uses unsupported shape {shape!r}. "
                f"Supported shapes: {', '.join(_LABEL_SHAPES)}."
            )

        key = name.casefold()
        previous = by_name.get(key)
        if previous is not None:
            if previous.name != name:
                raise ValueError(
                    f"Hierarchical labels {previous.name!r} and {name!r} differ only by "
                    "capitalization; facade pin names must be unique."
                )
            if previous.electrical_type != electrical_type:
                raise ValueError(
                    f"Hierarchical label {name!r} has conflicting shapes "
                    f"{previous.electrical_type!r} and {electrical_type!r}."
                )
            continue

        if len(pins) >= 64:
            raise ValueError("The MVP supports up to 64 facade pins per block.")
        pin = BlockPin(name, electrical_type, str(len(pins) + 1))
        by_name[key] = pin
        pins.append(pin)

    return pins


def _normalize_symbol_instances(symbol: list, project_name: str) -> None:
    """Detach a copied sheet's component instance paths from the original hierarchy."""
    instances = _direct(symbol, "instances")
    paths: list[list] = []
    if instances is not None:
        for project in instances[1:]:
            if _head(project) != "project":
                continue
            for path in project[1:]:
                if _head(path) == "path":
                    paths.append(path)

    if not paths:
        reference = None
        for prop in _all(symbol, "property"):
            if len(prop) >= 3 and str(prop[1]) == "Reference":
                reference = str(prop[2])
                break
        unit_node = _direct(symbol, "unit")
        unit = str(unit_node[1]) if unit_node is not None and len(unit_node) >= 2 else "1"
        if reference:
            paths = [["path", _Quoted("/"), ["reference", _Quoted(reference)], ["unit", unit]]]

    normalized_paths = []
    for path in paths:
        reference_node = _direct(path, "reference")
        unit_node = _direct(path, "unit")
        if reference_node is None or len(reference_node) < 2:
            continue
        new_path = [
            "path",
            _Quoted("/"),
            ["reference", _Quoted(str(reference_node[1]))],
        ]
        if unit_node is not None and len(unit_node) >= 2:
            new_path.append(["unit", str(unit_node[1])])
        # Preserve instance-level variant/property overrides on the normalized path.
        for child in path[1:]:
            if _head(child) not in {"reference", "unit"} and child != path[1]:
                new_path.append(child)
        normalized_paths.append(new_path)

    if normalized_paths:
        replacement = [
            "instances",
            ["project", _Quoted(project_name), *normalized_paths],
        ]
        if instances is None:
            symbol.append(replacement)
        else:
            symbol[symbol.index(instances)] = replacement


def prepare_block_implementation(
    source_schematic: Path,
    display_name: str,
    block_id: str,
    project_name: str,
) -> tuple[list[BlockPin], str]:
    """Read a sheet, derive its interface and return a detached implementation document.

    The original file is never modified. Hierarchical labels remain hierarchical labels
    in the library implementation; they are the ongoing source of truth for the facade
    pin list and types. Nested sheet symbols are rejected, matching KiCad Design Blocks'
    current no-nested-sheet constraint.
    """
    source_schematic = Path(source_schematic).expanduser().resolve()
    try:
        tree = parse_sexpr(source_schematic.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read source sheet {source_schematic}: {exc}") from exc
    if _head(tree) != "kicad_sch":
        raise ValueError(f"Source file is not a KiCad schematic: {source_schematic}")

    nested_sheets = _all(tree, "sheet")
    if nested_sheets:
        raise ValueError(
            "The selected sheet contains nested hierarchical sheets. Extract those as "
            "separate blocks first, then compose them with the Nest Block action."
        )

    pins = extract_hierarchical_pins(tree)

    # Give the library document its own root identity and root sheet instance.
    root_uuid = _direct(tree, "uuid")
    if root_uuid is not None and len(root_uuid) >= 2:
        root_uuid[1] = _Quoted(str(uuid.uuid4()))

    title_block = _direct(tree, "title_block")
    if title_block is not None:
        title = _direct(title_block, "title")
        if title is None:
            title_block.append(["title", _Quoted(display_name)])
        elif len(title) >= 2:
            title[1] = _Quoted(display_name)
        comment = next(
            (item for item in title_block[1:] if _head(item) == "comment" and len(item) >= 3 and str(item[1]) == "1"),
            None,
        )
        if comment is None:
            title_block.append(["comment", "1", _Quoted("Block ID: " + block_id)])
        else:
            comment[2] = _Quoted("Block ID: " + block_id)

    for symbol in _all(tree, "symbol"):
        _normalize_symbol_instances(symbol, project_name)

    sheet_instances = _direct(tree, "sheet_instances")
    root_instance = ["sheet_instances", ["path", _Quoted("/"), ["page", _Quoted("1")]]]
    if sheet_instances is None:
        tree.append(root_instance)
    else:
        tree[tree.index(sheet_instances)] = root_instance

    return pins, _dump(tree) + "\n"
