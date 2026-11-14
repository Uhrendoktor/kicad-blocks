"""Discover child blocks from ordinary facade symbols and schematic connectivity.

The implementation schematic is authoritative: a child dependency exists while
its KiCad Blocks facade symbol exists in the parent implementation. Pin mappings
come from KiCad's exported electrical netlist, never from matching pin names.
"""

from __future__ import annotations

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, SupportsFloat, TypedDict

if TYPE_CHECKING:
    from .repository import BlockRecord

class KiCadNetlist(TypedDict):
    """Schematic connectivity exported by KiCad CLI."""

    component_refs: set[str]
    pin_nets: dict[str, dict[str, str]]


class _SymbolText(Protocol):
    value: str


class _SymbolTextField(Protocol):
    text: _SymbolText


class _SymbolIdentifier(Protocol):
    library: str
    name: str


class _SymbolPin(Protocol):
    number: str


class _SymbolDefinition(Protocol):
    id: _SymbolIdentifier | None
    pins: Sequence[_SymbolPin]


class _SymbolTransform(Protocol):
    orientation: int


class _SymbolPosition(Protocol):
    x: SupportsFloat
    y: SupportsFloat


class _PlacedSymbol(Protocol):
    definition: _SymbolDefinition | None
    reference: str | None
    reference_field: _SymbolTextField | None
    transform: _SymbolTransform | None
    position: _SymbolPosition | None


class BlockDependency(TypedDict):
    """One placed child block and its electrical/placement mapping."""

    child: BlockRecord
    reference: str
    position_mm: tuple[float, float]
    orientation: int
    pin_map: dict[str, str | None]


def parse_kicad_xml_netlist(path: Path) -> KiCadNetlist:
    """Read component references and pin-to-net assignments from KiCad XML."""
    root = ET.parse(Path(path)).getroot()
    components = root.find("./components")
    if components is None:
        raise ValueError("KiCad's XML netlist has no components section.")

    component_refs = {
        str(component.get("ref", "")).strip()
        for component in components.findall("./comp")
        if str(component.get("ref", "")).strip()
    }
    pin_nets: dict[str, dict[str, str]] = {}
    nets = root.find("./nets")
    if nets is not None:
        for net in nets.findall("./net"):
            net_name = str(net.get("name", "")).strip()
            if not net_name:
                continue
            for node in net.findall("./node"):
                reference = str(node.get("ref", "")).strip()
                pin_number = str(node.get("pin", "")).strip()
                if not reference or not pin_number:
                    continue
                by_pin = pin_nets.setdefault(reference, {})
                previous = by_pin.get(pin_number)
                if previous is not None and previous != net_name:
                    raise ValueError(
                        f"KiCad netlist assigns {reference}.{pin_number} to both "
                        f"{previous!r} and {net_name!r}."
                    )
                by_pin[pin_number] = net_name

    return {"component_refs": component_refs, "pin_nets": pin_nets}


def _field_text(instance: _PlacedSymbol) -> str:
    field = instance.reference_field
    return str(field.text.value or "").strip() if field is not None else ""


def _millimetres(value: SupportsFloat) -> float:
    """Convert KiCad IPC coordinates, which are always nanometres, to millimetres."""
    return float(value) / 1_000_000.0


def discover_block_dependencies(
    parent: BlockRecord,
    records: Sequence[BlockRecord],
    symbols: Sequence[_PlacedSymbol],
    netlist: KiCadNetlist,
) -> list[BlockDependency]:
    """Build dependency occurrences from placed facade symbols and netlist pins."""
    from .repository import LIBRARY_NICKNAME

    by_symbol: dict[tuple[str, str], BlockRecord] = {}
    for record in records:
        key = (
            str(record.manifest.get("library_nickname", "")),
            str(record.manifest.get("symbol_name", "")),
        )
        existing = by_symbol.get(key)
        if existing is not None:
            identity = (
                existing.manifest.get("block_id"),
                existing.manifest.get("revision"),
            )
            candidate = (
                record.manifest.get("block_id"),
                record.manifest.get("revision"),
            )
            if identity != candidate:
                raise RuntimeError(
                    f"Registered libraries contain conflicting definitions for "
                    f"{key[0]}:{key[1]}; resolve the duplicate before publishing."
                )
            continue
        by_symbol[key] = record

    component_refs = set(netlist["component_refs"])
    pin_nets = netlist["pin_nets"]
    occurrences: list[BlockDependency] = []
    seen_references: set[str] = set()

    for symbol in symbols:
        definition = symbol.definition
        identifier = definition.id if definition is not None else None
        library_nickname = str(getattr(identifier, "library", "") or "")
        if library_nickname != LIBRARY_NICKNAME:
            continue

        symbol_name = str(getattr(identifier, "name", "") or "")
        child = by_symbol.get((library_nickname, symbol_name))
        if child is None:
            raise FileNotFoundError(
                f"Placed block facade {library_nickname}:{symbol_name} is not present "
                "in the registered block libraries. Register its library before publishing."
            )
        if child.manifest.get("block_id") == parent.manifest.get("block_id"):
            raise ValueError(f"Block {parent.name!r} cannot contain itself.")

        reference = (
            str(getattr(symbol, "reference", "") or "").strip()
            or _field_text(symbol)
        )
        if not reference or "?" in reference:
            raise ValueError(
                f"Block facade {symbol_name!r} has no annotated reference. "
                "Annotate the parent implementation before publishing."
            )
        reference_key = reference.casefold()
        if reference_key in seen_references:
            raise ValueError(f"Block reference {reference!r} occurs more than once.")
        seen_references.add(reference_key)
        if reference not in component_refs:
            raise ValueError(
                f"KiCad's netlist does not contain placed block {reference!r}. "
                "Save and annotate the implementation, then publish again."
            )

        manifest_pins = child.manifest.get("pins", [])
        pin_names = [str(pin["name"]) for pin in manifest_pins]
        if len(pin_names) != len(set(pin_names)):
            raise ValueError(f"Block {child.name!r} has duplicate facade pin names.")

        symbol_pin_numbers = {
            str(pin.number).strip()
            for pin in (definition.pins if definition is not None else ())
        }
        missing_numbers = sorted(
            {
                str(pin.get("number", "")).strip()
                for pin in manifest_pins
                if str(pin.get("number", "")).strip()
            }
            - symbol_pin_numbers
        )
        if missing_numbers:
            raise ValueError(
                f"Placed facade {reference!r} no longer matches {child.name!r}: "
                f"manifest pin number(s) {', '.join(missing_numbers)} are absent. "
                "Refresh the facade from its library before publishing."
            )

        component_pin_nets = pin_nets.get(reference, {})
        pin_map = {
            str(pin["name"]): component_pin_nets.get(str(pin.get("number", "")).strip())
            for pin in manifest_pins
        }

        transform = symbol.transform
        orientation_value = int(transform.orientation if transform is not None else 1)
        orientations = {1: 0, 2: 90, 3: 180, 4: 270}
        if orientation_value not in orientations:
            raise ValueError(
                f"Block facade {reference!r} has unsupported orientation "
                f"{orientation_value}."
            )
        position = symbol.position
        if position is None:
            raise ValueError(f"Could not read the placement of block facade {reference!r}.")

        occurrences.append(
            {
                "child": child,
                "reference": reference,
                "position_mm": (
                    _millimetres(position.x),
                    _millimetres(position.y),
                ),
                "orientation": orientations[orientation_value],
                "pin_map": pin_map,
            }
        )

    return sorted(occurrences, key=lambda item: item["reference"].casefold())


def _all_sheet_symbols(schematic) -> list:
    """Collect facade symbols from every sheet in the implementation hierarchy."""
    try:
        sheets = list(schematic.iter_sheets())
        symbols = [
            symbol
            for sheet in sheets
            for symbol in schematic.for_sheet(sheet).get_symbols()
        ]
    except AttributeError:
        symbols = list(schematic.get_symbols())
    return symbols or list(schematic.get_symbols())


def synchronize_block_dependencies(parent, library_roots: list[Path], kicad_cli: str):
    """Rebuild a block's dependency manifest from its saved default implementation."""
    from kipy import KiCad

    from .repository import list_blocks, synchronize_dependencies

    records = [
        record
        for library_root in library_roots
        for record in list_blocks(library_root)
    ]
    implementation = parent.implementation
    with tempfile.TemporaryDirectory(prefix="kicad-block-netlist-") as temporary:
        netlist_path = Path(temporary) / "implementation.net.xml"
        command = [
            str(kicad_cli),
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            str(netlist_path),
            str(implementation),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"KiCad netlist export timed out for {parent.name!r}."
            ) from exc
        if result.returncode != 0 or not netlist_path.is_file():
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f"Could not read connectivity for {parent.name!r} from KiCad's netlist"
                + (f": {detail}" if detail else ".")
            )
        netlist = parse_kicad_xml_netlist(netlist_path)

    kicad = KiCad(
        headless=True,
        kicad_cli_path=str(kicad_cli),
        file_path=str(implementation),
        timeout_ms=30000,
    )
    try:
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError(
                f"KiCad could not open {parent.name!r} for dependency discovery."
            )
        occurrences = discover_block_dependencies(
            parent,
            records,
            _all_sheet_symbols(schematic),
            netlist,
        )
    finally:
        kicad.close()

    previous = parent.manifest.get("dependencies", [])
    updated = synchronize_dependencies(parent, occurrences)
    current = updated.manifest.get("dependencies", [])
    return {
        "block": updated,
        "changed": previous != current,
        "count": len(current),
    }
