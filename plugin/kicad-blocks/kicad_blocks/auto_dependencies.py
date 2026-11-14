"""Discover child blocks from ordinary facade symbols and schematic connectivity.

The implementation schematic is authoritative: a child dependency exists while
its KiCad Blocks facade symbol exists in the parent implementation. Pin mappings
come from KiCad's exported electrical netlist, never from matching pin names.
"""

from __future__ import annotations

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, TypedDict

from .json_types import JsonObject


class Netlist(TypedDict):
    """Component and electrical connectivity data exported by KiCad."""

    component_refs: set[str]
    pin_nets: dict[str, dict[str, str]]


class ManifestPin(TypedDict):
    """Name and stable facade pin number read from a block manifest."""

    name: str
    number: str


class BlockRecordLike(Protocol):
    """Repository block fields used during child-dependency discovery."""

    manifest: JsonObject
    name: str
    path: Path
    implementation: Path


class _TextValue(Protocol):
    value: str


class _Field(Protocol):
    text: _TextValue


class _ReferenceFieldSymbol(Protocol):
    reference_field: _Field
    reference: str


class _SymbolId(Protocol):
    library: str
    name: str


class _SymbolPin(Protocol):
    number: str


class _SymbolDefinition(Protocol):
    id: _SymbolId
    pins: Sequence[_SymbolPin]


class _SymbolPosition(Protocol):
    x: int | float
    y: int | float


class _SymbolTransform(Protocol):
    orientation: int


class FacadeSymbol(Protocol):
    """Small structural view of the KiCad symbol IPC object used here."""

    definition: _SymbolDefinition
    transform: _SymbolTransform
    position: _SymbolPosition
    reference: str
    reference_field: _Field


class _SheetLike(Protocol):
    """One KiCad schematic sheet handle."""

    ...


class _SheetSchematic(Protocol):
    def get_symbols(self) -> Sequence[FacadeSymbol]: ...


class SchematicLike(Protocol):
    """Schematic IPC methods required for hierarchy-wide symbol discovery."""

    def iter_sheets(self) -> Sequence[_SheetLike]: ...
    def for_sheet(self, sheet: _SheetLike) -> _SheetSchematic: ...
    def get_symbols(self) -> Sequence[FacadeSymbol]: ...


class DependencyOccurrence(TypedDict):
    """One child block placement and its resolved electrical pin mapping."""

    child: BlockRecordLike
    reference: str
    position_mm: tuple[float, float]
    orientation: int
    pin_map: dict[str, str | None]


class DependencySyncResult(TypedDict):
    """Summary returned after dependency metadata is reconciled."""

    block: BlockRecordLike
    changed: bool
    count: int


def parse_kicad_xml_netlist(path: Path) -> Netlist:
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


def _field_text(instance: _ReferenceFieldSymbol) -> str:
    """Read KiCad's displayed reference value from a placed symbol."""
    return str(getattr(getattr(instance.reference_field, "text", None), "value", "") or "").strip()


def _millimetres(value: int | float) -> float:
    """Convert KiCad IPC coordinates, which are always nanometres, to millimetres."""
    return float(value) / 1_000_000.0


def _manifest_pins(record: BlockRecordLike) -> list[ManifestPin]:
    """Validate and normalize the named pin definitions from a block manifest."""
    raw_pins = record.manifest.get("pins", [])
    if not isinstance(raw_pins, Sequence) or isinstance(raw_pins, (str, bytes)):
        raise ValueError(f"Block {record.name!r} has an invalid pin manifest.")
    pins: list[ManifestPin] = []
    for item in raw_pins:
        if not isinstance(item, Mapping):
            raise ValueError(f"Block {record.name!r} has an invalid pin manifest entry.")
        name = item.get("name")
        number = item.get("number", "")
        if not isinstance(name, str) or not isinstance(number, str):
            raise ValueError(f"Block {record.name!r} has a pin without a text name or number.")
        pins.append({"name": name, "number": number})
    return pins


def discover_block_dependencies(
    parent: BlockRecordLike,
    records: Sequence[BlockRecordLike],
    symbols: Sequence[FacadeSymbol],
    netlist: Netlist,
) -> list[DependencyOccurrence]:
    """Build dependency occurrences from placed facade symbols and netlist pins."""
    from .repository import LIBRARY_NICKNAME

    by_symbol: dict[tuple[str, str], BlockRecordLike] = {}
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
    occurrences: list[DependencyOccurrence] = []
    seen_references: set[str] = set()

    for symbol in symbols:
        identifier = getattr(getattr(symbol, "definition", None), "id", None)
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
            or _field_text(symbol, "reference_field")
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

        manifest_pins = _manifest_pins(child)
        pin_names = [pin["name"] for pin in manifest_pins]
        if len(pin_names) != len(set(pin_names)):
            raise ValueError(f"Block {child.name!r} has duplicate facade pin names.")

        symbol_pin_numbers = {
            str(getattr(pin, "number", "")).strip()
            for pin in getattr(symbol.definition, "pins", ())
        }
        missing_numbers = sorted(
            {
                pin["number"].strip()
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
            pin["name"]: component_pin_nets.get(pin["number"].strip())
            for pin in manifest_pins
        }

        orientation_value = int(symbol.transform.orientation)
        orientations = {1: 0, 2: 90, 3: 180, 4: 270}
        if orientation_value not in orientations:
            raise ValueError(
                f"Block facade {reference!r} has unsupported orientation "
                f"{orientation_value}."
            )
        position = symbol.position

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


def _all_sheet_symbols(schematic: SchematicLike) -> list[FacadeSymbol]:
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


def synchronize_block_dependencies(
    parent: BlockRecordLike, library_roots: list[Path], kicad_cli: str
) -> DependencySyncResult:
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
