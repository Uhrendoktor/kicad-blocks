"""Extract opaque block implementations from selected KiCad schematic circuitry.

KiCad's Schematic SaveSelectionToString command serializes the selected drawing
items in schematic S-expression form. Hierarchical labels within that selection
are the authoritative external interface: their name and shape become facade
pin name and electrical type, and the labels are converted to global labels in
the detached implementation document.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

from .model import BlockPin
from .pcb_layout import _Quoted, _direct, _dump, _head, parse_sexpr

_LABEL_SHAPES = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "tri_state": "tristate",
    "passive": "passive",
}
_PIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_./+\\-]*\\Z")


def _selection_nodes(selection_fragment: str) -> list:
    """Parse KiCad's clipboard-format fragment, which contains sibling S-expressions."""
    fragment = str(selection_fragment or "").strip()
    if not fragment:
        raise ValueError("Select the circuit items to extract before running this action.")
    try:
        wrapper = parse_sexpr(f"(selection {fragment})")
    except ValueError as exc:
        raise ValueError(f"KiCad returned an invalid selected-schematic fragment: {exc}") from exc
    if _head(wrapper) != "selection":
        raise ValueError("KiCad returned an unexpected selected-schematic format.")
    return wrapper[1:]


def extract_hierarchical_pins(selection_fragment: str) -> list[BlockPin]:
    """Derive unique facade pins from every hierarchical label in the selection.

    Repeated labels with the same name and shape connect the same net and therefore
    produce one facade pin. A repeated name with a different shape is rejected.
    """
    nodes = _selection_nodes(selection_fragment)
    if any(_head(node) == "sheet" for node in nodes):
        raise ValueError(
            "Select the circuitry inside the sheet, not the hierarchical sheet symbol."
        )
    if not any(_head(node) == "symbol" for node in nodes):
        raise ValueError(
            "The selection contains no schematic symbols. Select the circuitry and its "
            "hierarchical labels together."
        )

    pins: list[BlockPin] = []
    by_name: dict[str, BlockPin] = {}
    labels = [node for node in nodes if _head(node) == "hierarchical_label"]
    if not labels:
        raise ValueError(
            "The selected circuitry has no hierarchical labels. Add hierarchical labels "
            "for every external connection; their names and shapes define the facade pins."
        )

    for label in labels:
        if len(label) < 2:
            raise ValueError("A selected hierarchical label has no name.")
        name = str(label[1]).strip()
        if not _PIN_NAME.fullmatch(name):
            raise ValueError(
                f"Hierarchical label {name!r} cannot be used as a facade pin name. "
                "Use letters, numbers, underscore, dot, slash, plus or minus."
            )

        shape_node = _direct(label, "shape")
        if shape_node is None or len(shape_node) < 2:
            raise ValueError(f"Hierarchical label {name!r} has no electrical shape.")
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
                    "capitalization; KiCad block pin names must be unique."
                )
            if previous.electrical_type != electrical_type:
                raise ValueError(
                    f"Hierarchical label {name!r} is used with conflicting shapes "
                    f"{previous.electrical_type!r} and {electrical_type!r}."
                )
            continue

        if len(pins) >= 64:
            raise ValueError("The MVP supports up to 64 external pins per block.")
        pin = BlockPin(name, electrical_type, str(len(pins) + 1))
        by_name[key] = pin
        pins.append(pin)

    return pins


def _symbol_reference(symbol: list) -> tuple[str, str]:
    reference = None
    for child in symbol[1:]:
        if _head(child) == "property" and len(child) >= 3 and str(child[1]) == "Reference":
            reference = str(child[2])
            break
    if not reference:
        raise ValueError("A selected symbol is missing its Reference property.")
    unit_node = _direct(symbol, "unit")
    unit = str(unit_node[1]) if unit_node is not None and len(unit_node) >= 2 else "1"
    return reference, unit


def render_extracted_implementation_schematic(
    display_name: str,
    block_id: str,
    pins: Iterable[BlockPin],
    selection_fragment: str,
) -> str:
    """Wrap selected KiCad objects into a standalone schematic with a detached interface.

    All selected hierarchical labels are converted to global labels in-place. Their
    connection coordinates, orientation, shape, formatting and UUID are preserved.
    Symbol instance paths are normalized to the new root schematic while retaining
    each selected symbol's current reference and unit.
    """
    pins = list(pins)
    derived_pins = extract_hierarchical_pins(selection_fragment)
    if [(p.name, p.electrical_type) for p in pins] != [
        (p.name, p.electrical_type) for p in derived_pins
    ]:
        raise ValueError(
            "The supplied facade pins do not match the hierarchical labels in the selection."
        )

    nodes = _selection_nodes(selection_fragment)
    converted = []
    for original in nodes:
        node = list(original)
        kind = _head(node)
        if kind == "hierarchical_label":
            # Hierarchical and global labels share the same name/shape/position/effects/UUID
            # representation. A global label keeps the extracted circuit connected internally
            # after it is detached from its former parent sheet.
            node[0] = "global_label"
        elif kind == "symbol":
            reference, unit = _symbol_reference(node)
            instances_index = next(
                (i for i, child in enumerate(node[1:], start=1) if _head(child) == "instances"),
                None,
            )
            normalized_instances = [
                "instances",
                [
                    "project",
                    _Quoted(display_name),
                    [
                        "path",
                        _Quoted("/"),
                        ["reference", _Quoted(reference)],
                        ["unit", unit],
                    ],
                ],
            ]
            if instances_index is None:
                node.append(normalized_instances)
            else:
                node[instances_index] = normalized_instances
        converted.append(node)

    document = [
        "kicad_sch",
        ["version", "20250114"],
        ["generator", _Quoted("kicad_blocks")],
        ["uuid", _Quoted(str(uuid.uuid4()))],
        ["paper", _Quoted("A4")],
        [
            "title_block",
            ["title", _Quoted(display_name)],
            ["comment", "1", _Quoted("Block ID: " + block_id)],
        ],
        *converted,
        ["sheet_instances", ["path", _Quoted("/"), ["page", _Quoted("1")]]],
        ["embedded_fonts", "no"],
    ]
    return _dump(document) + "\\n"
