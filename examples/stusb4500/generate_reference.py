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

# Declarative circuit groups.  Their page-level placement, frame bounds,
# titles and descriptions are derived from member pin geometry.
GROUP_GAP_MM = 2.54
GROUP_X_PADDING_MM = 12.70
GROUP_TOP_PADDING_MM = 17.78
GROUP_BOTTOM_PADDING_MM = 5.08
GROUP_DESCRIPTION_FONT_MM = 0.65
GROUP_EVIDENCE = {
    "datasheet": DATASHEET,
    "document": "ST DS12499 Rev. 8",
    "page": 26,
    "figure": "Figure 10",
    # Optional source crop; the group metadata can bind a page/figure image
    # once a reviewed asset is checked into the example.
    "screenshot_asset": None,
}
SCHEMATIC_GROUPS = (
    {
        "id": "usb-port", "title": "USB-C PORT / CC PROTECTION",
        "description": "Type-C receptacle and dual CC-line ESD protection.",
        "members": ("J1", "D1"), "lane": "main", "order": 0,
        "layout": {
            "anchor": "J1", "anchor_y_mm": 139.70,
            "constraints": (
                {"kind": "offset", "component": "D1", "to": "J1", "dx_mm": 33.02, "dy_mm": 0.0},
            ),
        },
    },
    {
        "id": "vbus-bulk", "title": "VBUS BULK PROTECTION",
        "description": "Input bulk capacitance and VBUS transient clamp.",
        "members": ("C50", "D50"), "lane": "main", "order": 1,
        "layout": {
            "anchor": "C50", "anchor_y_mm": 132.08,
            "constraints": (
                {"kind": "offset", "component": "D50", "to": "C50", "dx_mm": 20.32, "dy_mm": 0.0},
            ),
        },
    },
    {
        "id": "vbus-monitor", "title": "VBUS MONITOR / FILTER",
        "description": "VBUS filter, fast-charge path and VBUS_VS_DISCH divider. D4 part is unspecified; verify its rating.",
        "members": ("D4", "C4", "R1", "R4"), "lane": "main", "order": 2,
        "layout": {
            "anchor": "D4", "anchor_y_mm": 111.76,
            "constraints": (
                {"kind": "offset", "component": "C4", "to": "D4", "dx_mm": 12.70, "dy_mm": 3.81},
                {"kind": "offset", "component": "R1", "to": "C4", "dx_mm": 12.70, "dy_mm": 0.0},
                {"kind": "offset", "component": "R4", "to": "R1", "dx_mm": 5.08, "dy_mm": 0.0},
            ),
        },
    },
    {
        "id": "controller", "title": "STUSB4500 CONTROLLER",
        "description": "USB-PD controller with directly wired VREG_1V2 and VREG_2V7 bypass capacitors.",
        "members": ("U1", "C1", "C2", "C3", "R55"), "lane": "main", "order": 3,
        "layout": {
            "anchor": "U1", "anchor_y_mm": 137.16,
            "constraints": (
                {"kind": "offset", "component": "C1", "to": "U1", "dx_mm": -20.32, "dy_mm": -35.56},
                {"kind": "offset", "component": "C2", "to": "U1", "dx_mm": 0.0, "dy_mm": -35.56},
                {"kind": "offset", "component": "C3", "to": "C2", "dx_mm": 5.08, "dy_mm": 0.0},
                {"kind": "pin_offset", "component": "R55", "to": "U1", "pin": "6", "dx_mm": -7.62, "dy_mm": 7.62},
            ),
        },
    },
    {
        "id": "address-straps", "title": "I2C ADDRESS STRAPS",
        "description": "Selectable ADDR0/ADDR1 pull-up and pull-down networks.",
        "members": ("R51", "R52", "R53", "R54"), "lane": "secondary",
        "align_to": "controller", "y_offset_mm": 25.40,
        "layout": {
            "anchor": "R51", "anchor_y_mm": 177.80,
            "constraints": (
                {"kind": "offset", "component": "R52", "to": "R51", "dx_mm": 0.0, "dy_mm": 12.70},
                {"kind": "offset", "component": "R53", "to": "R51", "dx_mm": 20.32, "dy_mm": 0.0},
                {"kind": "offset", "component": "R54", "to": "R53", "dx_mm": 0.0, "dy_mm": 12.70},
            ),
        },
    },
    {
        "id": "power-switch", "title": "BACK-TO-BACK PMOS SWITCH",
        "description": "Back-to-back P-channel switches with a direct common-source control network.",
        "members": ("T4", "T1", "R10", "R11", "R13"), "lane": "main", "order": 4,
        "layout": {
            "anchor": "T4", "anchor_y_mm": 104.14,
            "constraints": (
                {"kind": "offset", "component": "T1", "to": "T4", "dx_mm": 40.64, "dy_mm": 0.0},
                {"kind": "midpoint_offset", "component": "R10", "between": ("T4", "T1"), "dx_mm": 0.0, "dy_mm": 27.94},
                {"kind": "offset", "component": "R11", "to": "R10", "dx_mm": 7.62, "dy_mm": 7.62},
                {"kind": "offset", "component": "R13", "to": "R11", "dx_mm": 20.32, "dy_mm": 0.0},
            ),
        },
    },
    {
        "id": "vsnk-output", "title": "VSNK OUTPUT",
        "description": "Protected sink output with local bypass and TVS components.",
        "members": ("R2", "C10", "D11"), "lane": "main", "order": 5,
        "layout": {
            "anchor": "C10", "anchor_y_mm": 99.06,
            "constraints": (
                {"kind": "offset", "component": "D11", "to": "C10", "dx_mm": 0.0, "dy_mm": 25.40},
                {"kind": "offset", "component": "R2", "to": "C10", "dx_mm": 0.0, "dy_mm": 46.99},
            ),
        },
    },
)
DIRECT_DECOUPLERS = (
    ("U1", "21", "C1", "1"),
    ("U1", "23", "C2", "1"),
    ("U1", "23", "C3", "1"),
)
GROUND_PINS_DOWN = ("D1.3",)
FORBIDDEN_LOCAL_LABELS = ("VREG_1V2", "FET_MID")
ADJACENT_COMPONENT_PAIRS = (
    {"left": "R1", "right": "R4", "body_width_mm": 2.54, "max_gap_mm": 2.54},
    {"left": "C2", "right": "C3", "body_width_mm": 2.54, "max_gap_mm": 2.54},
)
COMPONENT_PROPERTY_LIMITS_MM = {
    "resistor_reference": 4.00,
    "capacitor_reference": 6.00,
    "capacitor_value": 6.00,
}


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
    box_width: float | None = None,
) -> list:
    """Create a native KiCad text_box with measured size and uniform margins.

    Coordinates identify the top-left box corner. KiCad lays out/reflows text
    inside the native text_box; this helper supplies conservative dimensions
    and identical physical padding on all four sides. Callers may widen the
    box to a known tab or page region to prevent KiCad from wrapping a title.
    """
    measured_width, height = _measure_text_box(value, size, padding)
    width = measured_width if box_width is None else box_width
    if width + 0.001 < measured_width:
        raise ValueError(
            f"Text box width {width:.3f} mm is smaller than measured width "
            f"{measured_width:.3f} mm for {value!r}."
        )
    result = [
        "text_box", _Quoted(value),
        ["at", f"{x:.3f}", f"{y:.3f}", "0"],
        ["size", f"{width:.3f}", f"{height:.3f}"],
        ["margins", *(f"{padding:.3f}" for _ in range(4))],
        ["fill", ["type", "none"]],
        ["effects", ["font", ["size", f"{size:.3f}", f"{size:.3f}"]],
            ["justify", "left", "top"]],
        ["uuid", _Quoted(_uid(f"graphic:text:{key}"))],
    ]
    stroke_width = "0.1524" if border else "-1"
    result.insert(5, ["stroke", ["width", stroke_width], ["type", "default"]])
    return result


def _note_box(
    x: float, y: float, lines: tuple[str, ...], key: str,
) -> list[list]:
    """Create one native note text_box sized from the supplied text."""
    if not lines or any(not line.strip() for line in lines):
        raise ValueError(f"Note {key!r} must contain non-empty lines.")
    return [_graphic_text(
        "\n".join(lines), x, y, 0.90, key,
        padding=1.27, border=True, box_width=40.64,
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
        _graphic_text(
            title, x, title_y, 3.20, f"header:{key}:title",
            box_width=rule_right - x, border=False,
        ),
        _graphic_text(
            subtitle, x, title_y + 7.62, 1.05, f"header:{key}:subtitle",
            box_width=rule_right - x, border=False,
        ),
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
            box_width=tab_width - 2.54,
        ),
    ]


def _functional_zone(
    x: float, y: float, right: float, bottom: float,
    title: str, tab_width: float, key: str,
) -> list[list]:
    """Backward-compatible alias for the reusable functional-block helper."""
    return _functional_block(x, y, right, bottom, title, tab_width, key)


def _symbol_rotation(reference: str) -> int:
    """Return the canonical rotation used by the reference layout."""
    if reference in {"T1", "D4"}:
        return 180
    if reference == "D1":
        return 270
    if reference in {"R51", "R52", "R53", "R54"}:
        return 90
    return 0


def _world_pin(sx: float, sy: float, rotation: int, pin: dict) -> tuple[float, float]:
    """Transform a library pin coordinate into sheet coordinates."""
    x, y = float(pin["x"]), float(pin["y"])
    if rotation == 0:
        return round(sx + x, 3), round(sy - y, 3)
    if rotation == 90:
        return round(sx - y, 3), round(sy - x, 3)
    if rotation == 180:
        return round(sx - x, 3), round(sy + y, 3)
    if rotation == 270:
        return round(sx + y, 3), round(sy + x, 3)
    raise ValueError(f"Unsupported symbol rotation: {rotation}.")


def _group_evidence_line(group: dict) -> str:
    evidence = {**GROUP_EVIDENCE, **group.get("evidence", {})}
    return (
        f"{str(evidence['document']).upper()} | "
        f"PAGE {evidence['page']} | {str(evidence['figure']).upper()}"
    )


def _group_description(group: dict) -> str:
    return f"{group['description']}\n{_group_evidence_line(group)}"


def _group_text_width(group: dict) -> float:
    title_width, _ = _measure_text_box(str(group["title"]), 1.27, 1.27)
    description_width, _ = _measure_text_box(
        _group_description(group), GROUP_DESCRIPTION_FONT_MM, 1.27,
    )
    return max(title_width + 2.54, description_width + 2.54)


def _resolve_group_component_positions(
    specs: list[tuple], loaded: dict[tuple[str, str], list],
) -> dict[str, list[float]]:
    """Resolve component origins from group anchors and relative constraints.

    Layout metadata uses offsets, midpoint relations and library-pin-relative
    relations. Sheet coordinates are deliberately absent from symbol specs.
    """
    by_ref = {spec[0]: spec for spec in specs}
    positions: dict[str, list[float]] = {}
    for group in SCHEMATIC_GROUPS:
        layout = group.get("layout")
        if not isinstance(layout, dict):
            raise ValueError(f"Group {group['id']} has no component layout metadata.")
        anchor = str(layout.get("anchor", ""))
        if anchor not in group["members"] or anchor not in by_ref:
            raise ValueError(f"Group {group['id']} has an invalid layout anchor {anchor!r}.")
        positions[anchor] = [0.0, float(layout["anchor_y_mm"])]
        pending = list(layout.get("constraints", ()))
        while pending:
            unresolved = []
            progressed = False
            for rule in pending:
                kind = str(rule["kind"])
                component = str(rule["component"])
                if component not in group["members"] or component in positions:
                    raise ValueError(
                        f"Invalid or duplicate layout target {component!r} in {group['id']}."
                    )
                if kind == "offset":
                    reference = str(rule["to"])
                    if reference not in positions:
                        unresolved.append(rule)
                        continue
                    base = positions[reference]
                    point = (
                        base[0] + float(rule["dx_mm"]),
                        base[1] + float(rule["dy_mm"]),
                    )
                elif kind == "midpoint_offset":
                    first_ref, second_ref = (str(item) for item in rule["between"])
                    if first_ref not in positions or second_ref not in positions:
                        unresolved.append(rule)
                        continue
                    first, second = positions[first_ref], positions[second_ref]
                    point = (
                        (first[0] + second[0]) / 2 + float(rule.get("dx_mm", 0.0)),
                        (first[1] + second[1]) / 2 + float(rule.get("dy_mm", 0.0)),
                    )
                elif kind == "pin_offset":
                    reference = str(rule["to"])
                    if reference not in positions:
                        unresolved.append(rule)
                        continue
                    reference_spec = by_ref[reference]
                    definition = loaded[(reference_spec[1], reference_spec[2])]
                    pin_number = str(rule["pin"])
                    pin = next(
                        (
                            item for item in _pin_data(definition)
                            if item["number"] == pin_number
                        ),
                        None,
                    )
                    if pin is None:
                        raise ValueError(
                            f"Layout constraint for {component} references "
                            f"missing pin {reference}.{pin_number}."
                        )
                    reference_rotation = _symbol_rotation(reference)
                    pin_point = _world_pin(
                        *positions[reference], reference_rotation, pin,
                    )
                    point = (
                        pin_point[0] + float(rule["dx_mm"]),
                        pin_point[1] + float(rule["dy_mm"]),
                    )
                else:
                    raise ValueError(
                        f"Unsupported layout relation {kind!r} in group {group['id']}."
                    )
                positions[component] = [round(point[0], 3), round(point[1], 3)]
                progressed = True
            if not progressed and unresolved:
                raise ValueError(
                    f"Unresolved component layout constraints in {group['id']}: "
                    + ", ".join(str(rule["component"]) for rule in unresolved)
                )
            pending = unresolved
        unresolved_members = set(group["members"]) - set(positions)
        if unresolved_members:
            raise ValueError(
                f"Group {group['id']} has no layout constraints for "
                + ", ".join(sorted(unresolved_members))
            )

    expected = {spec[0] for spec in specs}
    if set(positions) != expected:
        raise ValueError("Every component must resolve from exactly one group layout.")
    return positions


def _pack_component_groups(
    specs: list[tuple], loaded: dict[tuple[str, str], list],
) -> dict[str, tuple[float, float]]:
    """Resolve local constraints, then pack derived group bounds left-to-right."""
    by_ref = {spec[0]: spec for spec in specs}
    positions = _resolve_group_component_positions(specs, loaded)

    def raw_bounds(group: dict) -> tuple[float, float, float, float]:
        points = []
        for reference in group["members"]:
            spec = by_ref[reference]
            rotation = _symbol_rotation(reference)
            definition = loaded[(spec[1], spec[2])]
            points.extend(
                _world_pin(*positions[reference], rotation, pin)
                for pin in _pin_data(definition)
            )
        if not points:
            raise ValueError(f"Group {group['id']} contains no symbol pins.")
        return (
            min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points),
        )

    placed: dict[str, tuple[float, float, float, float]] = {}
    cursor = 25.40
    primary = sorted(
        (item for item in SCHEMATIC_GROUPS if item["lane"] == "main"),
        key=lambda item: int(item["order"]),
    )
    for group in primary:
        left, top, right, bottom = raw_bounds(group)
        width = max(
            right - left + 2 * GROUP_X_PADDING_MM,
            _group_text_width(group),
        )
        shift_x = cursor + GROUP_X_PADDING_MM - left
        for reference in group["members"]:
            positions[reference][0] += shift_x
        placed[group["id"]] = (cursor, top, cursor + width, bottom)
        cursor += width + GROUP_GAP_MM

    for group in (item for item in SCHEMATIC_GROUPS if item["lane"] != "main"):
        target_id = str(group.get("align_to", ""))
        if target_id not in placed:
            raise ValueError(f"Group {group['id']} has an unknown alignment target.")
        left, top, right, bottom = raw_bounds(group)
        width = max(
            right - left + 2 * GROUP_X_PADDING_MM,
            _group_text_width(group),
        )
        target_left, _, target_right, _ = placed[target_id]
        shift_x = (target_left + target_right - left - right) / 2
        shift_y = float(group.get("y_offset_mm", 0.0))
        for reference in group["members"]:
            positions[reference][0] += shift_x
            positions[reference][1] += shift_y
        placed[group["id"]] = (
            left + shift_x - GROUP_X_PADDING_MM, top + shift_y,
            left + shift_x - GROUP_X_PADDING_MM + width, bottom + shift_y,
        )

    assigned = [reference for group in SCHEMATIC_GROUPS for reference in group["members"]]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(by_ref):
        raise ValueError("Every component must belong to exactly one schematic group.")
    return {reference: (point[0], point[1]) for reference, point in positions.items()}


def _functional_group(group: dict, bounds: tuple[float, float, float, float]) -> list[list]:
    """Generate a frame, title tab and descriptive source panel from bounds."""
    left, top, right, bottom = bounds
    title = str(group["title"])
    width = right - left
    tab_width = min(
        width, max(12.70, _measure_text_box(title, 1.27, 1.27)[0] + 2.54),
    )
    description = _group_description(group)
    return [
        _graphic_rectangle(left, top, right, bottom, f"zone:{group['id']}"),
        _graphic_tab(left, top, tab_width, group["id"]),
        _graphic_text(
            title, left + 1.27, top - 4.83, 1.27,
            f"zone-title:{group['id']}", padding=1.27, border=False,
            box_width=tab_width - 2.54,
        ),
        _graphic_text(
            description, left + 1.27, top + 1.27, GROUP_DESCRIPTION_FONT_MM,
            f"group-description:{group['id']}", padding=1.27, border=False,
            box_width=width - 2.54,
        ),
    ]


def generate_reference_schematic(destination: Path) -> Path:
    """Write the editable, datasheet-derived STUSB4500 implementation sheet."""
    root = _symbol_root()
    specs = [
        # ref, library, symbol, value, unused position slot, pin-number -> (net, kind, shape).
        # Component origins are resolved exclusively from SCHEMATIC_GROUPS layout constraints.
        ("U1", "Interface_USB", "STUSB4500QTR", "STUSB4500QTR", None, {
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
        ("J1", "Connector", "USB_C_Receptacle", "USB 3.1 Type-C receptacle", None, {
            "A1": ("GND", "local", ""), "A4": ("VBUS", "local", ""),
            "A5": ("CC1", "local", ""), "A9": ("VBUS", "local", ""),
            "A12": ("GND", "local", ""), "B1": ("GND", "local", ""),
            "B4": ("VBUS", "local", ""), "B5": ("CC2", "local", ""),
            "B9": ("VBUS", "local", ""), "B12": ("GND", "local", ""),
        }),
        ("C50", "Device", "C", "4.7uF", None, {
            "1": ("VBUS", "local", ""), "2": ("GND", "local", ""),
        }),
        ("D50", "Device", "D_TVS", "ESDA25P35-1U1M", None, {
            "1": ("VBUS", "local", ""), "2": ("GND", "local", ""),
        }),
        ("D1", "STUSB4500_Custom", "ESDA25W", "ESDA25W", None, {
            "1": ("CC1", "local", ""), "2": ("CC2", "local", ""),
            "3": ("GND", "local", ""),
        }),
        ("D4", "Device", "D", "D4 (part not specified)", None, {
            "1": ("VBUS_FILTERED", "local", ""), "2": ("VBUS", "local", ""),
        }),
        ("C4", "Device", "C", "1uF", None, {
            "1": ("VBUS_FILTERED", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R1", "Device", "R", "470", None, {
            "1": ("VBUS_FILTERED", "local", ""), "2": ("VBUS_VS_DISCH", "local", ""),
        }),
        ("R4", "Device", "R", "1k", None, {
            "1": ("VREG_2V7", "local", ""), "2": ("VBUS_VS_DISCH", "local", ""),
        }),
        ("C1", "Device", "C", "1uF", None, {
            "1": ("VREG_1V2", "local", ""), "2": ("GND", "local", ""),
        }),
        ("C2", "Device", "C", "1uF", None, {
            "1": ("VREG_2V7", "local", ""), "2": ("GND", "local", ""),
        }),
        ("C3", "Device", "C", "1uF", None, {
            "1": ("VREG_2V7", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R51", "Device", "R", "100k", None, {
            "1": ("VREG_2V7", "local", ""), "2": ("ADDR1", "local", ""),
        }),
        ("R52", "Device", "R", "100k", None, {
            "1": ("VREG_2V7", "local", ""), "2": ("ADDR0", "local", ""),
        }),
        ("R53", "Device", "R", "100k", None, {
            "1": ("ADDR1", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R54", "Device", "R", "100k", None, {
            "1": ("ADDR0", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R55", "Device", "R", "10k", None, {
            "1": ("RESET", "local", ""), "2": ("GND", "local", ""),
        }),
        ("T4", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", None, {
            "1": ("VBUS_EN_SNK", "local", ""), "2": ("VBUS", "local", ""),
            "3": ("FET_MID", "local", ""),
        }),
        ("T1", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", None, {
            "1": ("VSNK_GATE", "local", ""), "2": ("FET_MID", "local", ""),
            "3": ("VSNK", "hier", "output"),
        }),
        ("R10", "Device", "R", "100k", None, {
            "1": ("FET_MID", "local", ""), "2": ("VSNK_GATE", "local", ""),
        }),
        ("R11", "Device", "R", "22k", None, {
            "1": ("VSNK_GATE", "local", ""), "2": ("GND", "local", ""),
        }),
        ("R13", "Device", "R", "100", None, {
            "1": ("VSNK_GATE", "local", ""), "2": ("VBUS_EN_SNK", "local", ""),
        }),
        ("R2", "Device", "R", "1k", None, {
            "1": ("DISCH", "local", ""), "2": ("VSNK", "local", ""),
        }),
        ("C10", "Device", "C", "100nF", None, {
            "1": ("VSNK", "local", ""), "2": ("GND", "local", ""),
        }),
        ("D11", "Device", "D_TVS", "ESDA25P35-1U1M", None, {
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

    positions = _pack_component_groups(specs, loaded)
    instances = []
    labels = []
    no_connects = []
    label_points: dict[
        tuple[float, float],
        tuple[str, str, str | None, float, float, str, int],
    ] = {}
    pin_world_points: dict[tuple[str, str], tuple[float, float]] = {}
    no_connect_points: set[tuple[float, float]] = set()
    for reference, library, symbol_name, value, _position, pin_map in specs:
        sx, sy = positions[reference]
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
            # Keep the fields close to the right side of the IC while
            # right-aligning them inside the automatically sized group frame.
            reference_x, reference_y = sx + 22.86, sy - 41.91
            value_x, value_y = sx + 22.86, sy - 39.37
            reference_justify = "right"
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
                    "R1": -3.81, "R2": 3.81, "R4": 3.81,
                    "R10": -3.81, "R11": 3.81, "R13": -3.81, "R55": -2.54,
                }
                dx = ref_offsets[reference]
                reference_x, reference_y = sx + dx, sy
                reference_justify = "left" if dx > 0 else "right"
        elif reference.startswith("D"):
            # Keep diode identifiers close to the symbol, not floating above it.
            reference_x, reference_y = sx, sy - 6.35
            value_x, value_y = sx, sy - 3.81
        elif reference.startswith("C"):
            reference_x, reference_y = sx - 5.08, sy - 2.54
            value_x, value_y = sx - 5.08, sy + 2.54
        else:
            reference_x, reference_y = sx, sy - 8
            value_x, value_y = sx, sy - 5
        rotation = _symbol_rotation(reference)
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
                            # D1's ground pin points down; fan CC labels out
                            # above the ESD array so they do not cross wires.
                            label_x = px + 5.08 if pin["number"] == "1" else px - 5.08
                            label_y = py - 5.08
                            justify = "left" if pin["number"] == "1" else "right"
                        if reference == "U1" and pin["number"] == "6":
                            # RESET is physically wired to R55. Put the
                            # hierarchical port on its own perpendicular twig,
                            # not inline on the U1-to-pull-down wire.
                            label_x = px
                            label_y = py - 2.54
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
                px, py = _world_pin(sx, sy, rotation, pin)
                point = (round(px, 3), round(py, 3))
                pin_world_points[(reference, pin["number"])] = point
                no_connect_points.add(point)
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
    # Connect the PMOS common-source midpoint directly to the resistor
    # network. This is one compact physical net and needs no FET_MID label.
    t4_fet_mid = pin_world_points[("T4", "3")]
    t1_fet_mid = pin_world_points[("T1", "2")]
    r10_fet_mid = pin_world_points[("R10", "1")]
    remove_local_label("T4", "3", "FET_MID")
    remove_local_label("T1", "2", "FET_MID")
    remove_local_label("R10", "1", "FET_MID")
    if abs(t4_fet_mid[1] - t1_fet_mid[1]) > 0.001:
        raise ValueError(f"PMOS common-node pins are not aligned: {t4_fet_mid}, {t1_fet_mid}")
    fet_mid_branch = (
        round((t4_fet_mid[0] + t1_fet_mid[0]) / 2, 3),
        t4_fet_mid[1],
    )
    if abs(fet_mid_branch[0] - r10_fet_mid[0]) > 0.001:
        raise ValueError("R10.1 must align vertically below the FET midpoint.")
    add_route("FET_MID", [t4_fet_mid, t1_fet_mid], "back-to-back-pmos-common-source")
    add_junction(fet_mid_branch, "FET_MID:common-source-midpoint")
    add_route(
        "FET_MID", [fet_mid_branch, r10_fet_mid],
        "common-source-midpoint-to-r10",
    )

    # The VSNK_GATE control bus is physically wired across R10/R11/R13.
    # Keep its one label at R11.1; T1.1 is a separate, matching label island.
    # Do not bridge the two wire islands with another routed segment.

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

    # All regulator bypass capacitors connect directly to IC supply pins.
    # VREG_1V2 uses a dedicated upper corridor; C2/C3 remain adjacent.
    u1_vreg_1v2 = pin_world_points[("U1", "21")]
    c1_vreg_1v2 = pin_world_points[("C1", "1")]
    remove_local_label("U1", "21", "VREG_1V2")
    remove_local_label("C1", "1", "VREG_1V2")
    vreg_1v2_guard_x = u1_vreg_1v2[0] - 2.54
    vreg_1v2_escape_x = c1_vreg_1v2[0] + 6.35
    add_route("VREG_1V2", [
        u1_vreg_1v2,
        (u1_vreg_1v2[0], 113.03),
        (vreg_1v2_guard_x, 113.03),
        (vreg_1v2_guard_x, 115.57),
        (vreg_1v2_escape_x, 115.57),
        (vreg_1v2_escape_x, 88.90),
        (c1_vreg_1v2[0], 88.90),
        c1_vreg_1v2,
    ], "u1-vreg-1v2-to-c1-around-controller-pins")

    u1_vreg_2v7 = pin_world_points[("U1", "23")]
    c2_vreg_2v7 = pin_world_points[("C2", "1")]
    c3_vreg_2v7 = pin_world_points[("C3", "1")]
    vreg_2v7_branch = (u1_vreg_2v7[0], 90.17)
    c3_vreg_2v7_branch = (c3_vreg_2v7[0], 90.17)
    add_route("VREG_2V7", [
        u1_vreg_2v7, vreg_2v7_branch,
        (c2_vreg_2v7[0], 90.17), c2_vreg_2v7,
    ], "u1-vreg-2v7-to-c2")
    # C3 joins the interior of the shared rail. One main segment plus one
    # branch and an explicit junction avoids duplicated overlapping wires.
    add_route(
        "VREG_2V7", [c3_vreg_2v7_branch, c3_vreg_2v7],
        "vreg-2v7-rail-to-c3",
    )
    add_junction(c3_vreg_2v7_branch, "VREG_2V7:C3-bypass-branch")
    remove_local_label("C2", "1", "VREG_2V7")
    remove_local_label("C3", "1", "VREG_2V7")

    # Keep the reusable VREG_2V7 label on the branch wire, not on a
    # decoupler pin. Decoupler pins themselves remain label-free.
    remove_local_label("U1", "23", "VREG_2V7")
    vreg_2v7_label_tap = (u1_vreg_2v7[0], 95.250)
    label_points[vreg_2v7_label_tap] = (
        "VREG_2V7", "local", None,
        vreg_2v7_label_tap[0], vreg_2v7_label_tap[1], "left", 0,
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
    add_route("VBUS_FILTERED", [d4_filtered, c4_filtered], "d4-to-c4-filtered-rail")
    # The C4.1 wire island and R1.1 terminal island retain one matching
    # local label each. They are electrically joined by the label pair.
    remove_local_label("D4", "1", "VBUS_FILTERED")

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

    # The header is page-level. Group frames and descriptions are generated
    # from member pin geometry after the routed schematic has been assembled.
    rules = _sheet_header(
        "STUSB4500 USB-C / POWER DELIVERY SINK",
        "DATASHEET-BASED REFERENCE  |  ST DS12499 REV. 8, FIGURE 10",
    )

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

    # Derive frames from the measured pins and pack them using the same fixed
    # horizontal gap used by the group placement pass.
    for group in SCHEMATIC_GROUPS:
        members = set(group["members"])
        points = [
            point for (reference, _number), point in pin_world_points.items()
            if reference in members
        ]
        if not points:
            raise ValueError(f"Cannot derive bounds for empty group {group['id']}.")
        left = min(point[0] for point in points) - GROUP_X_PADDING_MM
        right = max(point[0] for point in points) + GROUP_X_PADDING_MM
        top = min(point[1] for point in points) - GROUP_TOP_PADDING_MM
        bottom = max(point[1] for point in points) + GROUP_BOTTOM_PADDING_MM
        right = max(right, left + _group_text_width(group))
        rules.extend(_functional_group(group, (left, top, right, bottom)))

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
