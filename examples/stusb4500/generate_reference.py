"""Generate an editable KiCad reference circuit for the STUSB4500.

The component set and functional arrangement follow ST DS12499 Rev. 8,
Figure 10.  Symbols are loaded from the installed official KiCad libraries.
"""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))

from kicad_blocks.pcb_layout import (
    _Quoted,
    _all,
    _direct,
    _dump,
    _head,
    parse_sexpr,
)

DATASHEET = "https://www.st.com/resource/en/datasheet/stusb4500.pdf"
ROOT_UUID = str(uuid.uuid5(uuid.NAMESPACE_URL, "kicad-blocks:stusb4500-reference:root"))
PROJECT = "functional-block-implementation"


def _uid(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"kicad-blocks:stusb4500-reference:{key}"))


def _symbol_root() -> Path:
    configured = os.environ.get("KICAD_SYMBOL_DIR")
    candidates = [Path(configured)] if configured else []
    candidates += [
        Path("/usr/share/kicad/symbols"),
        Path("/usr/share/kicad-nightly/symbols"),
        Path("/usr/local/share/kicad/symbols"),
    ]
    candidates += list(Path("/usr/share").glob("kicad*/symbols"))
    for candidate in candidates:
        if candidate and (
            (candidate / "Interface_USB.kicad_sym").is_file()
            or (candidate / "Interface_USB.kicad_symdir").is_dir()
        ):
            return candidate
    found = subprocess.run(
        ["find", "/usr/share", "-name", "Interface_USB.kicad_sym", "-print", "-quit"],
        capture_output=True, text=True, check=False, timeout=15,
    )
    if found.stdout.strip():
        return Path(found.stdout.strip()).parent
    raise FileNotFoundError("Cannot locate the installed KiCad symbol libraries.")


def _custom_esda25w() -> list:
    """Three-terminal ESDA25W array: CC1, CC2 and common ground."""
    return [
        "symbol", _Quoted("STUSB4500_Custom:ESDA25W"),
        ["in_bom", "yes"], ["on_board", "yes"],
        ["property", _Quoted("Reference"), _Quoted("D"),
            ["id", "0"], ["at", "0", "6.35", "0"],
            ["effects", ["font", ["size", "1.27", "1.27"]]],
        ],
        ["property", _Quoted("Value"), _Quoted("ESDA25W"),
            ["id", "1"], ["at", "0", "-6.35", "0"],
            ["effects", ["font", ["size", "1.27", "1.27"]]],
        ],
        ["property", _Quoted("Footprint"), _Quoted("Package_TO_SOT_SMD:SOT-323_SC-70"),
            ["id", "2"], ["at", "0", "0", "0"],
            ["effects", ["font", ["size", "1.27", "1.27"]], "hide"],
        ],
        ["property", _Quoted("Datasheet"), _Quoted(""),
            ["id", "3"], ["at", "0", "0", "0"],
            ["effects", ["font", ["size", "1.27", "1.27"]], "hide"],
        ],
        ["symbol", _Quoted("ESDA25W_0_1"),
            ["rectangle", ["start", "-5.08", "3.81"], ["end", "5.08", "-3.81"],
                ["stroke", ["width", "0.254"]], ["fill", ["type", "background"]],
            ],
        ],
        ["symbol", _Quoted("ESDA25W_1_1"),
            ["pin", "passive", "line", ["at", "-7.62", "2.54", "0"], ["length", "2.54"],
                ["name", _Quoted("CC1"), ["effects", ["font", ["size", "1.27", "1.27"]]]],
                ["number", _Quoted("1"), ["effects", ["font", ["size", "1.27", "1.27"]]]],
            ],
            ["pin", "passive", "line", ["at", "-7.62", "-2.54", "0"], ["length", "2.54"],
                ["name", _Quoted("CC2"), ["effects", ["font", ["size", "1.27", "1.27"]]]],
                ["number", _Quoted("2"), ["effects", ["font", ["size", "1.27", "1.27"]]]],
            ],
            ["pin", "passive", "line", ["at", "7.62", "0", "180"], ["length", "2.54"],
                ["name", _Quoted("GND"), ["effects", ["font", ["size", "1.27", "1.27"]]]],
                ["number", _Quoted("3"), ["effects", ["font", ["size", "1.27", "1.27"]]]],
            ],
        ],
    ]


def _strip_embedded_font_flags(node: list) -> None:
    """Remove per-library font flags; the schematic carries one root-level flag."""
    node[:] = [
        child for child in node
        if not (isinstance(child, list) and _head(child) == "embedded_fonts")
    ]
    for child in node:
        if isinstance(child, list):
            _strip_embedded_font_flags(child)


def _load_symbol(root: Path, library: str, name: str) -> list:
    if library == "STUSB4500_Custom" and name == "ESDA25W":
        return _custom_esda25w()
    packed = root / f"{library}.kicad_sym"
    if packed.is_file():
        path = packed
    else:
        path = root / f"{library}.kicad_symdir" / f"{name}.kicad_sym"
    if not path.is_file():
        raise FileNotFoundError(f"KiCad symbol definition is missing: {path}")
    tree = parse_sexpr(path.read_text(encoding="utf-8"))
    for node in _all(tree, "symbol"):
        if str(node[1]) == name:
            result = copy.deepcopy(node)
            _strip_embedded_font_flags(result)
            result[1] = _Quoted(f"{library}:{name}")
            return result
    raise LookupError(f"Symbol {library}:{name} is not in {path}.")


def _walk(node):
    if isinstance(node, list):
        if _head(node) == "pin":
            yield node
        for child in node[1:]:
            yield from _walk(child)


def _pin_data(symbol: list) -> list[dict]:
    result = []
    seen = set()
    for pin in _walk(symbol):
        number_node = _direct(pin, "number")
        name_node = _direct(pin, "name")
        at_node = _direct(pin, "at")
        if not number_node or not at_node or len(at_node) < 4:
            continue
        number = str(number_node[1])
        if number in seen:
            continue
        seen.add(number)
        result.append({
            "number": number,
            "name": str(name_node[1]) if name_node else "",
            "x": float(at_node[1]),
            "y": float(at_node[2]),
            "angle": int(float(at_node[3])),
        })
    return result


def _wire(start: tuple[float, float], end: tuple[float, float], key: str) -> list:
    """Create one orthogonal two-point KiCad wire segment."""
    if start == end:
        raise ValueError(f"Zero-length wire segment for {key}.")
    if abs(start[0] - end[0]) > 0.001 and abs(start[1] - end[1]) > 0.001:
        raise ValueError(f"Non-orthogonal wire segment for {key}: {start} -> {end}.")
    return [
        "wire",
        ["pts",
            ["xy", f"{start[0]:.3f}", f"{start[1]:.3f}"],
            ["xy", f"{end[0]:.3f}", f"{end[1]:.3f}"],
        ],
        ["stroke", ["width", "0"], ["type", "default"]],
        ["uuid", _Quoted(_uid(f"wire:{key}:{start[0]:.3f}:{start[1]:.3f}:{end[0]:.3f}:{end[1]:.3f}"))],
    ]


def _prop(
    name: str, value: str, number: int, x: float, y: float,
    hidden: bool = False, font_size: float = 1.27, justify: str | None = None,
    angle: int = 0,
) -> list:
    effects = ["effects", ["font", ["size", f"{font_size:.3f}", f"{font_size:.3f}"]]]
    if justify:
        if justify not in {"left", "right"}:
            raise ValueError(
                f"Unsupported property justification {justify!r}; "
                "KiCad centers property text when horizontal justification is omitted."
            )
        effects.append(["justify", justify])
    if hidden:
        effects.append("hide")
    return [
        "property", _Quoted(name), _Quoted(value),
        ["id", str(number)],
        ["at", f"{x:.3f}", f"{y:.3f}", str(angle)],
        effects,
    ]


def _label(
    name: str, x: float, y: float, *, hierarchical: bool = False,
    shape: str = "passive", justify: str | None = "left", angle: int = 0, key: str,
) -> list:
    effects = ["effects", ["font", ["size", "1.0", "1.0"]]]
    if justify is not None:
        if justify not in {"left", "right"}:
            raise ValueError(
                f"Unsupported label justification {justify!r}; "
                "KiCad centers label text when horizontal justification is omitted."
            )
        effects.append(["justify", justify, "bottom"])
    if hierarchical:
        node = ["hierarchical_label", _Quoted(name), ["shape", shape]]
    else:
        node = ["label", _Quoted(name)]
    node.extend([
        ["at", f"{x:.3f}", f"{y:.3f}", str(angle)],
        effects,
        ["uuid", _Quoted(_uid(key))],
    ])
    return node


def _graphic_rectangle(
    x1: float, y1: float, x2: float, y2: float, key: str,
) -> list:
    """Draw a light, unfilled functional-zone frame."""
    return [
        "rectangle",
        ["start", f"{x1:.3f}", f"{y1:.3f}"],
        ["end", f"{x2:.3f}", f"{y2:.3f}"],
        ["stroke", ["width", "0.254"], ["type", "default"]],
        ["fill", ["type", "none"]],
        ["uuid", _Quoted(_uid(f"graphic:rectangle:{key}"))],
    ]


def _graphic_tab(x: float, y: float, width: float, key: str) -> list:
    """Draw three tab edges; the frame top serves as the fourth edge."""
    top = y - 5.08
    right = x + width
    return [
        "polyline",
        ["pts",
            ["xy", f"{x:.3f}", f"{y:.3f}"],
            ["xy", f"{x:.3f}", f"{top:.3f}"],
            ["xy", f"{right:.3f}", f"{top:.3f}"],
            ["xy", f"{right:.3f}", f"{y:.3f}"],
        ],
        ["stroke", ["width", "0.254"], ["type", "default"]],
        ["fill", ["type", "none"]],
        ["uuid", _Quoted(_uid(f"graphic:tab:{key}"))],
    ]


def _glyph_advance(character: str) -> float:
    """Estimate the relative advance of KiCad's stroke font."""
    if character in "ilI.,:;!|'":
        return 0.30
    if character in "MW@%&":
        return 0.90
    if character.isspace():
        return 0.34
    if character.isupper() or character.isdigit():
        return 0.64
    return 0.55


def _measure_text_box(
    value: str, size: float, padding: float = 1.27,
) -> tuple[float, float]:
    """Measure text conservatively and add equal physical padding on every side."""
    if size <= 0 or padding < 0:
        raise ValueError("Text size must be positive and padding cannot be negative.")
    lines = value.splitlines() or [""]
    line_width = max(
        (sum(_glyph_advance(character) for character in line) for line in lines),
        default=0.0,
    )
    width = 2 * padding + max(line_width * size * 1.12, size)
    height = 2 * padding + len(lines) * size * 1.55
    return round(width, 3), round(height, 3)


def _graphic_text(
    value: str, x: float, y: float, size: float, key: str,
    *, padding: float = 1.27, border: bool = True,
) -> list:
    """Create a native KiCad text_box with measured size and uniform margins.

    Coordinates identify the top-left box corner. KiCad lays out/reflows text
    inside the native text_box; this helper supplies conservative dimensions
    and identical physical padding on all four sides.
    """
    width, height = _measure_text_box(value, size, padding)
    stroke = (
        ["stroke", ["width", "0.1524"], ["type", "default"]]
        if border else ["stroke", ["width", "0"], ["type", "default"]]
    )
    return [
        "text_box", _Quoted(value),
        ["at", f"{x:.3f}", f"{y:.3f}", "0"],
        ["size", f"{width:.3f}", f"{height:.3f}"],
        ["margins", *(f"{padding:.3f}" for _ in range(4))],
        stroke,
        ["fill", ["type", "none"]],
        ["effects", ["font", ["size", f"{size:.3f}", f"{size:.3f}"]],
            ["justify", "left", "top"]],
        ["uuid", _Quoted(_uid(f"graphic:text:{key}"))],
    ]


def _note_box(
    x: float, y: float, lines: tuple[str, ...], key: str,
) -> list[list]:
    """Create one native note text_box sized from the supplied text."""
    if not lines or any(not line.strip() for line in lines):
        raise ValueError(f"Note {key!r} must contain non-empty lines.")
    return [_graphic_text(
        "\n".join(lines), x, y, 0.90, key, padding=1.27, border=True,
    )]


def _graphic_rule(x1: float, y1: float, x2: float, y2: float, key: str) -> list:
    """Draw a restrained page-level divider."""
    if abs(x1 - x2) > 0.001 and abs(y1 - y2) > 0.001:
        raise ValueError(f"Non-orthogonal graphic rule: {(x1, y1)} -> {(x2, y2)}")
    return [
        "polyline",
        ["pts", ["xy", f"{x1:.3f}", f"{y1:.3f}"], ["xy", f"{x2:.3f}", f"{y2:.3f}"]],
        ["stroke", ["width", "0.254"], ["type", "default"]],
        ["fill", ["type", "none"]],
        ["uuid", _Quoted(_uid(f"graphic:rule:{key}"))],
    ]


def _sheet_header(
    title: str, subtitle: str, *, x: float = 25.40,
    title_y: float = 48.26, rule_right: float = 411.48, key: str = "sheet",
) -> list[list]:
    """Place a compact title/subtitle in the reserved top margin."""
    if title_y + 17.78 >= 81.28:
        raise ValueError("Sheet header must remain above the functional-block tabs.")
    return [
        _graphic_text(title, x, title_y, 3.20, f"header:{key}:title"),
        _graphic_text(subtitle, x, title_y + 7.62, 1.05, f"header:{key}:subtitle"),
        _graphic_rule(x, title_y + 13.97, rule_right, title_y + 13.97, f"header:{key}:rule"),
    ]


def _functional_block(
    x: float, y: float, right: float, bottom: float,
    title: str, tab_width: float, key: str,
) -> list[list]:
    """Generate one grid-aligned, thin-frame module with an attached title tab."""
    if not (x < right and y < bottom):
        raise ValueError(f"Invalid functional-block bounds for {key}.")
    if tab_width <= 5.08 or tab_width > right - x:
        raise ValueError(f"Functional-block tab does not fit its frame: {key}.")
    measured_title_width, _ = _measure_text_box(title, 1.27, padding=1.27)
    if measured_title_width > tab_width - 2.54:
        raise ValueError(
            f"Functional-block title {title!r} exceeds its tab width "
            f"({measured_title_width + 2.54:.2f} mm > {tab_width:.2f} mm)."
        )
    return [
        _graphic_rectangle(x, y, right, bottom, f"zone:{key}"),
        _graphic_tab(x, y, tab_width, key),
        _graphic_text(
            title, x + 1.27, y - 4.83, 1.27,
            f"zone-title:{key}", padding=1.27, border=False,
        ),
    ]


def _functional_zone(
    x: float, y: float, right: float, bottom: float,
    title: str, tab_width: float, key: str,
) -> list[list]:
    """Backward-compatible alias for the reusable functional-block helper."""
    return _functional_block(x, y, right, bottom, title, tab_width, key)


def generate_reference_schematic(destination: Path) -> Path:
    """Write the editable, datasheet-derived STUSB4500 implementation sheet."""
    root = _symbol_root()
    specs = [
        # ref, library, symbol, value, position, pin-number -> (net, kind, shape)
        ("U1", "Interface_USB", "STUSB4500QTR", "STUSB4500QTR", (246.38, 137.16), {
            "1": ("CC1", "local", ""), "2": ("CC1", "local", ""),
            "3": ("NC", "nc", ""), "4": ("CC2", "local", ""),
            "5": ("CC2", "local", ""), "6": ("RESET", "hier", "input"),
            "7": ("SCL", "hier", "input"), "8": ("SDA", "hier", "bidirectional"),
            "9": ("DISCH", "local", ""), "10": ("GND", "hier", "passive"),
            "11": ("NC", "nc", ""), "12": ("ADDR0", "local", ""),
            "13": ("ADDR1", "local", ""), "14": ("NC", "nc", ""),
            "15": ("NC", "nc", ""), "16": ("VBUS_EN_SNK", "local", ""),
            "17": ("NC", "nc", ""), "18": ("VBUS_VS_DISCH", "local", ""),
            "19": ("ALERT#", "hier", "output"), "20": ("NC", "nc", ""),
            "21": ("VREG_1V2", "local", ""), "22": ("VSYS", "hier", "input"),
            "23": ("VREG_2V7", "local", ""), "24": ("VBUS", "local", ""),
            "25": ("GND", "local", ""),
        }),
        ("J1", "Connector", "USB_C_Receptacle", "USB 3.1 Type-C receptacle", (45.72, 139.70), {
            "A1": ("GND", "local", ""), "A4": ("VBUS", "local", ""),
            "A5": ("CC1", "local", ""), "A9": ("VBUS", "local", ""),
            "A12": ("GND", "local", ""), "B1": ("GND", "local", ""),
            "B4": ("VBUS", "local", ""), "B5": ("CC2", "local", ""),
            "B9": ("VBUS", "local", ""), "B12": ("GND", "local", ""),
        }),
        ("C50", "Device", "C", "4.7uF", (97.79, 132.08), {
            "1": ("VBUS", "local", ""), "2": ("GND", "local", ""),
        }),
        ("D50", "Device", "D_TVS", "ESDA25P35-1U1M", (118.11, 132.08), {
            "1": ("VBUS", "local", ""), "2": ("GND", "local", ""),
        }),
        ("D1", "STUSB4500_Custom", "ESDA25W", "ESDA25W", (78.74, 139.70), {
            "1": ("CC1", "local", ""), "2": ("CC2", "local", ""),
            "3": ("GND", "local", ""),
        }),
        ("D4", "Device", "D", "D4 (part not specified)", (160.02, 111.76), {
            "1": ("VBUS_FILTERED", "local", ""), "2": ("VBUS", "local", ""),
        }),
        ("C4", "Device", "C", "1uF", (172.72, 115.57), {
            "1": ("VBUS_FILTERED", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R1", "Device", "R", "470", (185.42, 115.57), {
            "1": ("VBUS_FILTERED", "local", ""), "2": ("VBUS_VS_DISCH", "local", ""),
        }),
        ("R4", "Device", "R", "1k", (203.20, 115.57), {
            "1": ("VREG_2V7", "local", ""), "2": ("VBUS_VS_DISCH", "local", ""),
        }),
        ("C1", "Device", "C", "1uF", (226.06, 101.60), {
            "1": ("VREG_1V2", "local", ""), "2": ("GND", "local", ""),
        }),
        ("C2", "Device", "C", "1uF", (246.38, 101.60), {
            "1": ("VREG_2V7", "local", ""), "2": ("GND", "local", ""),
        }),
        ("C3", "Device", "C", "1uF", (266.70, 101.60), {
            "1": ("VREG_2V7", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R51", "Device", "R", "100k", (236.22, 177.80), {
            "1": ("VREG_2V7", "local", ""), "2": ("ADDR1", "local", ""),
        }),
        ("R52", "Device", "R", "100k", (236.22, 190.50), {
            "1": ("VREG_2V7", "local", ""), "2": ("ADDR0", "local", ""),
        }),
        ("R53", "Device", "R", "100k", (256.54, 177.80), {
            "1": ("ADDR1", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R54", "Device", "R", "100k", (256.54, 190.50), {
            "1": ("ADDR0", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R55", "Device", "R", "10k", (279.40, 165.10), {
            "1": ("RESET", "local", ""), "2": ("GND", "local", ""),
        }),
        ("T4", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", (317.50, 104.14), {
            "1": ("VBUS_EN_SNK", "local", ""), "2": ("VBUS", "local", ""),
            "3": ("FET_MID", "local", ""),
        }),
        ("T1", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", (358.14, 104.14), {
            "1": ("VSNK_GATE", "local", ""), "2": ("FET_MID", "local", ""),
            "3": ("VSNK", "hier", "output"),
        }),
        ("R10", "Device", "R", "100k", (337.82, 132.08), {
            "1": ("FET_MID", "local", ""), "2": ("VSNK_GATE", "local", ""),
        }),
        ("R11", "Device", "R", "22k", (345.44, 139.70), {
            "1": ("VSNK_GATE", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R13", "Device", "R", "100", (365.76, 139.70), {
            "1": ("VSNK_GATE", "local", ""), "2": ("VBUS_EN_SNK", "local", ""),
        }),
        ("R2", "Device", "R", "1k", (391.16, 146.05), {
            "1": ("DISCH", "local", ""), "2": ("VSNK", "local", ""),
        }),
        ("C10", "Device", "C", "100nF", (391.16, 99.06), {
            "1": ("VSNK", "local", ""), "2": ("GND", "local", ""),
        }),
        ("D11", "Device", "D_TVS", "ESDA25P35-1U1M", (391.16, 124.46), {
            "1": ("VSNK", "local", ""), "2": ("GND", "local", ""),
        }),
    ]

    # All ground endpoints intentionally use the same hierarchical parent
    # interface. Do not create sheet-local GND labels inside this block.
    for spec in specs:
        pin_map = spec[-1]
        for pin_number, mapping in tuple(pin_map.items()):
            if mapping[0] == "GND":
                pin_map[pin_number] = ("GND", "hier", "passive")

    library_symbols = []
    loaded = {}
    for _, library, symbol_name, *_ in specs:
        key = (library, symbol_name)
        if key not in loaded:
            loaded[key] = _load_symbol(root, library, symbol_name)
            library_symbols.append(loaded[key])

    instances = []
    labels = []
    no_connects = []
    label_points: dict[
        tuple[float, float],
        tuple[str, str, str | None, float, float, str, int],
    ] = {}
    pin_world_points: dict[tuple[str, str], tuple[float, float]] = {}
    no_connect_points: set[tuple[float, float]] = set()
    for reference, library, symbol_name, value, (sx, sy), pin_map in specs:
        if reference == "R55":
            reset_pin = pin_world_points.get(("U1", "6"))
            if reset_pin is None:
                raise ValueError("U1.6 must be placed before the RESET pull-down.")
            # Device:R pin 1 is 5.08 mm above its origin; leave a 2.54 mm
            # physical segment between the RESET pin and the resistor terminal.
            # Move the vertical pull-down left of U1's CC pins. Its top
            # terminal is reached by a short two-segment RESET route.
            sx, sy = reset_pin[0] - 7.62, reset_pin[1] + 7.62
        definition = loaded[(library, symbol_name)]
        pins = _pin_data(definition)
        numbers = {pin["number"] for pin in pins}
        unknown = set(pin_map) - numbers
        if unknown:
            raise ValueError(f"{reference} maps unknown {library}:{symbol_name} pins: {sorted(unknown)}")
        lib_id = f"{library}:{symbol_name}"
        symbol_uuid = _uid(f"symbol:{reference}")
        footprint = ""
        datasheet = DATASHEET if reference == "U1" else ""
        for prop in _all(definition, "property"):
            if len(prop) >= 3 and str(prop[1]) == "Footprint":
                footprint = str(prop[2])
            elif len(prop) >= 3 and str(prop[1]) == "Datasheet" and str(prop[2]):
                datasheet = str(prop[2])
        reference_justify = None
        if reference == "U1":
            # Keep U1's fields adjacent to the right edge, vertically centered
            # beside the IC rather than detached below the symbol.
            reference_x, reference_y = sx + 30.48, sy - 41.91
            value_x, value_y = sx + 30.48, sy - 39.37
            reference_justify = "left"
        elif reference == "J1":
            # Put the connector fields immediately above its tall body.
            reference_x, reference_y = sx, sy - 33.02
            value_x, value_y = sx, sy - 30.48
        elif reference in {"T4", "T1"}:
            # Keep the device fields above the common-source wire.
            reference_x, reference_y = sx, sy - 13.97
            value_x, value_y = sx, sy - 11.43
        elif reference.startswith("R"):
            value_x, value_y = sx, sy
            if reference in {"R51", "R52", "R53", "R54"}:
                # Address strap resistors are rotated horizontal; keep their
                # references above the body and values centered inside it.
                reference_x, reference_y = sx, sy - 3.81
            else:
                # Vertical resistors have a terminal directly above the body.
                # Put the reference beside the body instead of on that pin.
                ref_offsets = {
                    "R1": -5.08, "R2": 5.08, "R4": 5.08,
                    "R10": -5.08, "R11": 5.08, "R13": -5.08, "R55": -2.54,
                }
                dx = ref_offsets[reference]
                reference_x, reference_y = sx + dx, sy
                reference_justify = "left" if dx > 0 else "right"
        elif reference.startswith("D"):
            # Keep diode identifiers close to the symbol, not floating above it.
            reference_x, reference_y = sx, sy - 6.35
            value_x, value_y = sx, sy - 3.81
        elif reference.startswith("C"):
            reference_x, reference_y = sx - 7.62, sy - 2.54
            value_x, value_y = sx - 7.62, sy + 2.54
        else:
            reference_x, reference_y = sx, sy - 8
            value_x, value_y = sx, sy - 5
        rotation = (
            180 if reference in {"T1", "D4"}
            else 90 if reference in {"R51", "R52", "R53", "R54"}
            else 0
        )
        # Vertical resistor fields follow the body; fields on symbol-rotated
        # address resistors are counter-rotated so that their text stays legible.
        field_angle = 90 if reference.startswith("R") else 0
        instance = [
            "symbol", ["lib_id", _Quoted(lib_id)], ["at", f"{sx:.3f}", f"{sy:.3f}", str(rotation)],
            ["unit", "1"], ["in_bom", "yes"], ["on_board", "yes"],
            ["dnp", "yes" if reference in {"R51", "R52"} else "no"],
            ["uuid", _Quoted(symbol_uuid)],
            _prop(
                "Reference", reference, 0, reference_x, reference_y,
                font_size=0.95 if reference.startswith("R") else 1.10,
                justify=reference_justify,
                angle=field_angle,
            ),
            _prop(
                "Value", value, 1, value_x, value_y,
                font_size=0.72 if reference.startswith("R") else 1.10,
                angle=field_angle,
            ),
            _prop("Footprint", footprint, 2, sx, sy, True),
            _prop("Datasheet", datasheet, 3, sx, sy, True),
        ]
        for pin in pins:
            instance.append(["pin", _Quoted(pin["number"]), ["uuid", _Quoted(_uid(f"pin:{reference}:{pin['number']}"))]])
            net_spec = pin_map.get(pin["number"])
            if net_spec:
                net, kind, *shape = net_spec
                if rotation == 0:
                    px, py = sx + pin["x"], sy - pin["y"]
                elif rotation == 90:
                    px, py = sx - pin["y"], sy - pin["x"]
                elif rotation == 180:
                    px, py = sx - pin["x"], sy + pin["y"]
                elif rotation == 270:
                    px, py = sx + pin["y"], sy + pin["x"]
                else:
                    raise ValueError(f"Unsupported symbol rotation {rotation} for {reference}.")
                point = (round(px, 3), round(py, 3))
                pin_world_points[(reference, pin["number"])] = point
                if kind in {"hier", "local"}:
                    existing = label_points.get(point)
                    shape_name = shape[0] if shape else None
                    if existing and existing[0] != net:
                        raise ValueError(
                            f"Conflicting nets {existing[0]} and {net} share pin coordinate {point}."
                        )
                    if existing and existing[1] == "hier" and kind == "local":
                        pass
                    elif existing and existing[1] == "hier" and kind == "hier":
                        if existing[2] != shape_name:
                            raise ValueError(f"Conflicting hierarchical label shapes at {point}.")
                    else:
                        # Move labels off pin text along the actual pin axis.
                        # Coordinate-quadrant guesses are unsafe for stacked
                        # USB-C connector pins: they can route CC1/CC2 stubs
                        # vertically across adjacent VBUS pins and short nets.
                        angle = (pin["angle"] + rotation) % 360
                        outward = {
                            0: (-1, 0),
                            90: (0, 1),
                            180: (1, 0),
                            270: (0, -1),
                        }
                        if angle not in outward:
                            raise ValueError(
                                f"Unsupported pin angle {pin['angle']} for "
                                f"{reference}.{pin['number']} ({pin['name']})."
                            )
                        dx, dy = outward[angle]
                        label_x = px + dx * 2.54
                        label_y = py + dy * 2.54
                        justify = "right" if dx < 0 else "left"
                        label_angle = 0
                        if reference == "D1" and pin["number"] in {"1", "2"}:
                            # Put CC labels perpendicular to the short
                            # connector-to-ESD wires, clear of the symbol's
                            # own CC1/CC2 pin names.
                            label_x = px
                            label_y = py - 5.08 if pin["number"] == "1" else py + 5.08
                            justify = "right"
                        if reference == "C1" and pin["number"] == "1":
                            # The label branches sideways while C1.1 is wired
                            # directly upward to U1.21.
                            label_x = px + 2.54
                            label_y = py
                            justify = "left"
                            label_angle = 0
                        if reference == "R55" and pin["number"] == "2":
                            # Keep R55's GND label below the body and away
                            # from U1.4/CC2; the short vertical stub stays in
                            # the controller's left margin.
                            label_x = px
                            label_y = py + 2.54
                            justify = "right"
                            label_angle = 0
                        if reference == "U1":
                            # Stagger the four tightly pitched top-side supply
                            # labels horizontally above the QFN, keeping their
                            # text clear of the vertical pin names.
                            u1_top_labels = {
                                "24": (0.00, -2.54, "right", 0),
                                # Move VSYS left of the vertical pin rail and
                                # align it with VBUS; this separates it from
                                # the nearby C2 GND label.
                                "22": (-2.54, -5.08, "right", 0),
                                "25": (0.00, 5.08, "left", 0),
                            }
                            override = u1_top_labels.get(pin["number"])
                            if override is not None:
                                offset_x, offset_y, justify, label_angle = override
                                label_x = px + offset_x
                                label_y = py + offset_y
                        # One label is sufficient for stacked connector pins.
                        # Prefer the hierarchical port when it shares a point
                        # with local labels for the same electrical net.
                        label_points[point] = (
                            net, kind, shape_name, label_x, label_y, justify, label_angle,
                        )
                elif kind == "nc":
                    no_connect_points.add(point)
            elif reference == "J1":
                px = sx + pin["x"]
                py = sy - pin["y"]
                no_connect_points.add((round(px, 3), round(py, 3)))
        instance.append([
            "instances",
            ["project", _Quoted(PROJECT),
                ["path", _Quoted(f"/{ROOT_UUID}"),
                    ["reference", _Quoted(reference)], ["unit", "1"],
                ],
            ],
        ])
        instances.append(instance)

    # Generate label glyphs after wiring so consumed endpoints do not retain
    # duplicate flags on top of their visible wire connections.
    label_stubs = []
    for point in sorted(no_connect_points - set(label_points)):
        x, y = point
        no_connects.append([
            "no_connect", ["at", f"{x:.3f}", f"{y:.3f}"],
            ["uuid", _Quoted(_uid(f"nc:{x:.3f}:{y:.3f}"))],
        ])

    # Draw short, local relationships as real wires. Long connections remain
    # named labels so the sheet reads like a circuit rather than a cable map.
    # These groups are deliberately collinear; generation fails if a later
    # placement change breaks the intended geometry.
    wire_groups = [
        ("VBUS_VS_DISCH", [("R1", "2"), ("R4", "2")], ("R1", "2")),
        ("VREG_2V7", [("R51", "1"), ("R52", "1")], ("R51", "1")),
        ("VSNK_GATE", [("R10", "2"), ("R11", "1"), ("R13", "1")], ("R11", "1")),
        ("ADDR1", [("R51", "2"), ("R53", "1")], ("R51", "2")),
        ("ADDR0", [("R52", "2"), ("R54", "1")], ("R52", "2")),
    ]
    wires = []
    junctions = []
    for net_name, pin_keys, label_anchor in wire_groups:
        missing = [key for key in pin_keys if key not in pin_world_points]
        if missing:
            raise ValueError(f"{net_name} wire group references unmapped pins: {missing}")
        points = [(key, pin_world_points[key]) for key in pin_keys]
        x_values = {point[0] for _, point in points}
        y_values = {point[1] for _, point in points}
        if len(y_values) == 1:
            ordered = sorted(points, key=lambda item: item[1][0])
        elif len(x_values) == 1:
            ordered = sorted(points, key=lambda item: item[1][1])
        else:
            raise ValueError(f"{net_name} wire group is not collinear: {points}")
        for key, point in ordered:
            label = label_points.get(point)
            if label is None or label[0] != net_name or label[1] != "local":
                raise ValueError(
                    f"{net_name} wire endpoint {key} at {point} does not have the expected local label."
                )
        anchors = [point for key, point in ordered if key == label_anchor]
        if len(anchors) != 1:
            raise ValueError(f"{net_name} wire group must contain one label anchor: {label_anchor}")
        anchor_point = anchors[0]
        for (key_a, point_a), (key_b, point_b) in zip(ordered, ordered[1:]):
            wires.append(_wire(point_a, point_b, f"{net_name}:{key_a[0]}.{key_a[1]}-{key_b[0]}.{key_b[1]}"))
        for _, point in ordered[1:-1]:
            junctions.append([
                "junction",
                ["at", f"{point[0]:.3f}", f"{point[1]:.3f}"],
                ["diameter", "0"],
                ["color", "0", "0", "0", "0"],
                ["uuid", _Quoted(_uid(f"junction:{net_name}:{point[0]:.3f}:{point[1]:.3f}"))],
            ])
        for key, point in ordered:
            if point != anchor_point:
                label_points.pop(point, None)

    # Local helpers for short, visible orthogonal connections.
    def add_route(net: str, points: list[tuple[float, float]], key: str) -> None:
        if len(points) < 2:
            raise ValueError(f"{net} route {key} needs at least two points.")
        for index, (start, end) in enumerate(zip(points, points[1:])):
            wires.append(_wire(start, end, f"{net}:{key}:{index}"))

    def add_junction(point: tuple[float, float], key: str) -> None:
        junctions.append([
            "junction",
            ["at", f"{point[0]:.3f}", f"{point[1]:.3f}"],
            ["diameter", "0"],
            ["color", "0", "0", "0", "0"],
            ["uuid", _Quoted(_uid(f"junction:{key}:{point[0]:.3f}:{point[1]:.3f}"))],
        ])

    def remove_local_label(reference: str, pin_number: str, net: str) -> None:
        point = pin_world_points[(reference, pin_number)]
        label = label_points.get(point)
        if label is None or label[0] != net or label[1] != "local":
            raise ValueError(f"Expected {net} local label is missing at {reference}.{pin_number}.")
        label_points.pop(point)

    # R1.2 and R4.2 share one physical wire network. Keep one local label on
    # that island; U1.18 carries the matching label on a disconnected island.
    # The two PMOS devices face one another so their common FET_MID node is
    # an actual continuous wire, not two detached same-name flags.
    t4_fet_mid = pin_world_points[("T4", "3")]
    t1_fet_mid = pin_world_points[("T1", "2")]
    r10_fet_mid = pin_world_points[("R10", "1")]
    remove_local_label("T4", "3", "FET_MID")
    remove_local_label("T1", "2", "FET_MID")
    remove_local_label("R10", "1", "FET_MID")
    if abs(t4_fet_mid[1] - t1_fet_mid[1]) > 0.001:
        raise ValueError(f"PMOS common-node pins are not aligned: {t4_fet_mid}, {t1_fet_mid}")
    add_route("FET_MID", [t4_fet_mid, t1_fet_mid], "back-to-back-pmos-common-node")
    fet_mid_branch = (round((t4_fet_mid[0] + t1_fet_mid[0]) / 2, 3), t4_fet_mid[1])
    add_junction(fet_mid_branch, "FET_MID:R10-branch")
    add_route("FET_MID", [fet_mid_branch, r10_fet_mid], "common-node-to-r10")
    # FET_MID is one continuous physical network and needs no local labels.
    # T1's gate is on its outer right side. Drop it straight into the middle
    # of the VSNK_GATE resistor bus, between R11 and R13.
    t1_gate = pin_world_points[("T1", "1")]
    gate_bus_y = pin_world_points[("R10", "2")][1]
    gate_lane_x = 365.76
    add_route("VSNK_GATE", [
        t1_gate, (gate_lane_x, t1_gate[1]), (gate_lane_x, gate_bus_y),
    ], "t1-gate-to-bias-bus")
    gate_bus_tap = (gate_lane_x, gate_bus_y)
    add_junction(gate_bus_tap, "VSNK_GATE:T1-gate-tap")
    # T1.1 and R11.1 now sit on the same physical wire island as the other
    # gate-bus pins. Remove the two endpoint labels instead of duplicating
    # their net name along one connected network.
    remove_local_label("T1", "1", "VSNK_GATE")
    remove_local_label("R11", "1", "VSNK_GATE")

    # CC1 and CC2 use repeated local labels at the receptacle, ESD array,
    # and controller. Avoid physical traces across the dense USB-C pin field;
    # the labels remain close to the connector terminals and are reused.

    # The input bulk capacitor and TVS share a short local VBUS entry rail.
    c50_vbus = pin_world_points[("C50", "1")]
    d50_vbus = pin_world_points[("D50", "1")]
    add_route("VBUS", [
        c50_vbus, (d50_vbus[0], c50_vbus[1]), d50_vbus,
    ], "input-cap-tvs-feed")
    remove_local_label("D50", "1", "VBUS")

    # Decoupling capacitors connect directly to the exact U1 regulator pins.
    u1_vreg_1v2 = pin_world_points[("U1", "21")]
    c1_vreg_1v2 = pin_world_points[("C1", "1")]
    add_route("VREG_1V2", [
        u1_vreg_1v2, (u1_vreg_1v2[0], 92.71),
        (c1_vreg_1v2[0], 92.71), c1_vreg_1v2,
    ], "u1-vreg-1v2-to-c1")
    u1_vreg_2v7 = pin_world_points[("U1", "23")]
    c2_vreg_2v7 = pin_world_points[("C2", "1")]
    c3_vreg_2v7 = pin_world_points[("C3", "1")]
    vreg_2v7_branch = (u1_vreg_2v7[0], 90.17)
    add_route("VREG_2V7", [
        u1_vreg_2v7, vreg_2v7_branch,
        (c2_vreg_2v7[0], 90.17), c2_vreg_2v7,
    ], "u1-vreg-2v7-to-c2")
    add_route("VREG_2V7", [
        vreg_2v7_branch, (c3_vreg_2v7[0], 90.17), c3_vreg_2v7,
    ], "u1-vreg-2v7-to-c3")
    add_junction(vreg_2v7_branch, "VREG_2V7:C2-C3-bypass-branch")
    remove_local_label("C2", "1", "VREG_2V7")
    remove_local_label("C3", "1", "VREG_2V7")

    # VREG_1V2 is one direct U1.21-to-C1 physical network, so it needs no
    # local labels at either connected endpoint.
    remove_local_label("U1", "21", "VREG_1V2")
    remove_local_label("C1", "1", "VREG_1V2")

    # U1.23 is directly wired to C2 and C3. Put the reused VREG_2V7 label
    # on the vertical pin-to-branch wire, in the clear gap above the capacitors.
    remove_local_label("U1", "23", "VREG_2V7")
    vreg_2v7_label_tap = (254.000, 95.250)
    label_points[vreg_2v7_label_tap] = (
        "VREG_2V7", "local", None,
        254.000, 95.250, "left", 0,
    )

    # R55.1 is physically wired to U1.6; RESET remains a hierarchical parent port.
    reset_pin = pin_world_points[("U1", "6")]
    reset_pull_down = pin_world_points[("R55", "1")]
    remove_local_label("R55", "1", "RESET")
    add_route("RESET", [
        reset_pin,
        (reset_pull_down[0], reset_pin[1]),
        reset_pull_down,
    ], "u1-reset-to-r55")

    # D4 is reversed so VBUS enters from the connector side and filtered VBUS
    # exits to the right. The local rail ties its output to C4.1 and R1.1.
    d4_filtered = pin_world_points[("D4", "1")]
    c4_filtered = pin_world_points[("C4", "1")]
    r1_filtered = pin_world_points[("R1", "1")]
    add_route("VBUS_FILTERED", [
        d4_filtered, c4_filtered, r1_filtered,
    ], "d4-c4-r1-filtered-rail")
    # D4.1, C4.1 and R1.1 are one physical network. Remove duplicate labels
    # from the connected island; the exported netlist proves continuity.
    remove_local_label("D4", "1", "VBUS_FILTERED")
    remove_local_label("C4", "1", "VBUS_FILTERED")
    remove_local_label("R1", "1", "VBUS_FILTERED")

    # Each address strap is a left-to-right resistor chain: VREG_2V7,
    # pull-up, ADDR node, pull-down, GND. Put the ADDR label above the wire
    # midpoint rather than over either resistor body or value field.
    for net_name, first_pin, second_pin in (
        ("ADDR1", ("R51", "2"), ("R53", "1")),
        ("ADDR0", ("R52", "2"), ("R54", "1")),
    ):
        first = pin_world_points[first_pin]
        second = pin_world_points[second_pin]
        if abs(first[1] - second[1]) > 0.001:
            raise ValueError(f"{net_name} strap resistors are not on the same row: {first}, {second}")
        anchor = first
        label = label_points.get(anchor)
        if label is None or label[0] != net_name:
            raise ValueError(f"Missing {net_name} strap junction label at {first_pin}.")
        label_points.pop(anchor)
        midpoint = (round((first[0] + second[0]) / 2, 3), first[1])
        add_junction(midpoint, f"{net_name}:label-tap")
        label_points[midpoint] = (
            net_name, "local", None, midpoint[0], midpoint[1] - 5.08, None, 0,
        )

    # VSNK exits at the right side of the switch block as an explicit parent
    # interface. C10, D11 and the discharge resistor use local VSNK labels.
    # No output connector is embedded in the block.

    # RESET is exposed at U1 and pulled down locally by R55 beside the controller.
    # The MCU, I2C pull-ups and their connector intentionally belong to the parent.

    # Keep the title in the reserved top margin and create tabbed functional
    # blocks through the shared layout helper.
    rules = []
    rules.extend(_sheet_header(
        "STUSB4500 USB-C / POWER DELIVERY SINK",
        "DATASHEET-BASED REFERENCE  |  ST DS12499 REV. 8, FIGURE 10",
    ))
    rules.extend(_functional_block(
        25.40, 86.36, 86.36, 190.50,
        "USB-C PORT / CC PROTECTION", 55.88, "usb-port",
    ))
    rules.extend(_functional_block(
        88.90, 116.84, 134.62, 147.32,
        "VBUS BULK PROTECTION", 43.18, "vbus-bulk",
    ))
    rules.extend(_functional_block(
        137.16, 86.36, 213.36, 175.26,
        "VBUS MONITOR / FILTER", 55.88, "vbus-monitor",
    ))
    rules.extend(_functional_block(
        215.90, 86.36, 292.10, 160.02,
        "STUSB4500 CONTROLLER", 53.34, "controller",
    ))
    rules.extend(_functional_block(
        220.98, 167.64, 292.10, 205.74,
        "I2C ADDRESS STRAPS", 50.80, "address-straps",
    ))
    # Keep the two-PMOS switch and its gate-bias network in their own
    # module. The protected VSNK output and discharge parts form the next
    # left-to-right stage in a compact, adjacent frame.
    rules.extend(_functional_zone(
        294.64, 86.36, 372.11, 175.26,
        "BACK-TO-BACK PMOS SWITCH", 58.42, "power-switch",
    ))
    rules.extend(_functional_zone(
        374.65, 86.36, 411.48, 175.26,
        "VSNK OUTPUT / DISCHARGE", 33.02, "vsnk-output",
    ))
    rules.extend(_note_box(
        151.13, 137.16,
        ("D4 is unspecified; verify its VBUS rating.",),
        "d4-selection",
    ))
    rules.append(_graphic_text(
        "VBUS CLAMP + FILTER", 153.035, 96.52, 0.85, "vbus-clamp-caption",
    ))
    rules.append(_graphic_text(
        "VBUS_VS_DISCH SENSE DIVIDER", 153.035, 132.08, 0.85, "vbus-divider-caption",
    ))

    # Every local label must be intentionally reused; use a short physical
    # wire for one-off connections and reserve single-use ports for hierarchy.
    local_label_uses: dict[str, int] = {}
    for net, kind, *_ in label_points.values():
        if kind == "local":
            local_label_uses[net] = local_label_uses.get(net, 0) + 1
    single_use = {net: count for net, count in local_label_uses.items() if count < 2}
    if single_use:
        raise ValueError(
            "Local labels must connect at least two disconnected wire networks: "
            f"{single_use}"
        )

    def label_position_is_on_wire(x: float, y: float) -> bool:
        """Return true when a label can attach directly to an existing wire."""
        for wire in wires:
            points_node = _direct(wire, "pts")
            if points_node is None:
                continue
            points = [
                (round(float(point[1]), 3), round(float(point[2]), 3))
                for point in _all(points_node, "xy")
            ]
            for start, end in zip(points, points[1:]):
                if abs(start[1] - end[1]) < 0.001:
                    if abs(y - start[1]) < 0.001 and min(start[0], end[0]) - 0.001 <= x <= max(start[0], end[0]) + 0.001:
                        return True
                elif abs(start[0] - end[0]) < 0.001:
                    if abs(x - start[0]) < 0.001 and min(start[1], end[1]) - 0.001 <= y <= max(start[1], end[1]) + 0.001:
                        return True
        return False

    def segment_is_already_wired(
        start: tuple[float, float], end: tuple[float, float],
    ) -> bool:
        """Avoid duplicating a route segment when a label branches from it."""
        target = frozenset((start, end))
        for existing in (*wires, *label_stubs):
            points_node = _direct(existing, "pts")
            if points_node is None:
                continue
            points = [
                (round(float(point[1]), 3), round(float(point[2]), 3))
                for point in _all(points_node, "xy")
            ]
            if any(frozenset((a, b)) == target for a, b in zip(points, points[1:])):
                return True
        return False

    # Emit only surviving labels after local wires have consumed their
    # intermediate endpoint labels. This avoids duplicate flags on wire ends.
    # Offset labels use orthogonal two-point segments. Prefer a vertical-first
    # bend so an existing pin-to-rail wire can be reused without duplication.
    for point, (net, kind, shape_name, x, y, justify, angle) in sorted(label_points.items()):
        px, py = point
        labels.append(
            _label(
                net, x, y, hierarchical=kind == "hier", shape=shape_name,
                justify=justify, angle=angle, key=f"label:{kind}:{net}:{px:.3f}:{py:.3f}",
            )
        )
        target = (round(x, 3), round(y, 3))
        start = (round(px, 3), round(py, 3))
        if target != start and not label_position_is_on_wire(*target):
            path = [start]
            if start[0] != target[0] and start[1] != target[1]:
                path.append((start[0], target[1]))
            path.append(target)
            for segment_index, (segment_start, segment_end) in enumerate(zip(path, path[1:])):
                if segment_start == segment_end or segment_is_already_wired(segment_start, segment_end):
                    continue
                label_stubs.append(
                    _wire(
                        segment_start,
                        segment_end,
                        f"label-stub:{kind}:{net}:{px:.3f}:{py:.3f}:{segment_index}",
                    )
                )

    schematic = [
        "kicad_sch", ["version", "20250114"], ["generator", _Quoted("kicad-blocks")],
        ["uuid", _Quoted(ROOT_UUID)], ["paper", _Quoted("A3")],
        ["title_block",
            ["title", _Quoted("STUSB4500 USB Type-C / USB Power Delivery Sink")],
            ["date", _Quoted("2026-11-14")],
            ["rev", _Quoted("Electrical and layout refinement 5")],
            ["company", _Quoted("KiCad Blocks — Reference design")],
            ["comment", "1", _Quoted("Source: ST DS12499 Rev. 8, Figure 10")],
            ["comment", "2", _Quoted("Reference only: verify component ratings, footprints and thermal design before production.")],
        ],
        ["lib_symbols", *library_symbols],
        *instances, *wires, *label_stubs, *labels, *no_connects, *junctions, *rules,
        ["sheet_instances", ["path", _Quoted("/"), ["page", _Quoted("1")]]],
        ["embedded_fonts", "no"],
    ]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_dump(schematic) + "\n", encoding="utf-8")
    return destination


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("implementation.kicad_sch"),
    )
    arguments = parser.parse_args()
    print(generate_reference_schematic(arguments.output))
