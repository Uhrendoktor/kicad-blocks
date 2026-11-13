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


def _prop(name: str, value: str, number: int, x: float, y: float, hidden: bool = False) -> list:
    effects = ["effects", ["font", ["size", "1.27", "1.27"]]]
    if hidden:
        effects.append("hide")
    return [
        "property", _Quoted(name), _Quoted(value),
        ["id", str(number)],
        ["at", f"{x:.3f}", f"{y:.3f}", "0"],
        effects,
    ]


def _label(
    name: str, x: float, y: float, *, hierarchical: bool = False,
    shape: str = "passive", justify: str = "left", angle: int = 0, key: str,
) -> list:
    effects = ["effects", ["font", ["size", "1.0", "1.0"]], ["justify", justify, "bottom"]]
    node = ["hierarchical_label" if hierarchical else "label", _Quoted(name)]
    if hierarchical:
        node.append(["shape", shape])
    node.extend([
        ["at", f"{x:.3f}", f"{y:.3f}", str(angle)],
        effects,
        ["uuid", _Quoted(_uid(key))],
    ])
    return node


def generate_reference_schematic(destination: Path) -> Path:
    """Write the editable, datasheet-derived STUSB4500 implementation sheet."""
    root = _symbol_root()
    specs = [
        # ref, library, symbol, value, position, pin-number -> (net, kind, shape)
        ("U1", "Interface_USB", "STUSB4500QTR", "STUSB4500QTR", (213.36, 157.48), {
            "1": ("CC1", "local", ""), "2": ("CC1", "local", ""),
            "3": ("NC", "nc", ""), "4": ("CC2", "local", ""),
            "5": ("CC2", "local", ""), "6": ("RESET", "local", ""),
            "7": ("SCL", "local", ""), "8": ("SDA", "local", ""),
            "9": ("DISCH", "local", ""), "10": ("GND", "local", ""),
            "11": ("NC", "nc", ""), "12": ("ADDR0", "local", ""),
            "13": ("ADDR1", "local", ""), "14": ("NC", "nc", ""),
            "15": ("NC", "nc", ""), "16": ("VBUS_EN_SNK", "local", ""),
            "17": ("NC", "nc", ""), "18": ("VBUS_VS_DISCH", "local", ""),
            "19": ("ALERT#", "local", ""), "20": ("NC", "nc", ""),
            "21": ("VREG_1V2", "local", ""), "22": ("GND", "local", ""),
            "23": ("VREG_2V7", "local", ""), "24": ("VBUS", "local", ""),
            "25": ("GND", "local", ""),
        }),
        ("J1", "Connector", "USB_C_Receptacle", "USB 3.1 Type-C receptacle", (50.8, 142.24), {
            "A1": ("GND", "hier", "passive"), "A4": ("VBUS", "hier", "input"),
            "A5": ("CC1", "hier", "bidirectional"), "A9": ("VBUS", "local", ""),
            "A12": ("GND", "local", ""), "B1": ("GND", "local", ""),
            "B4": ("VBUS", "local", ""), "B5": ("CC2", "hier", "bidirectional"),
            "B9": ("VBUS", "local", ""), "B12": ("GND", "local", ""),
        }),
        ("J10", "Connector_Generic", "Conn_01x02", "MPT_0.5_2-2.54", (388.62, 93.98), {
            "1": ("VSNK", "hier", "output"), "2": ("GND", "hier", "passive"),
        }),
        ("J2", "Connector_Generic", "Conn_01x06", "MCU interface", (373.38, 213.36), {
            "1": ("SCL", "hier", "input"), "2": ("SDA", "hier", "bidirectional"),
            "3": ("ALERT#", "hier", "output"), "4": ("RESET", "hier", "input"),
            "5": ("VMCU", "hier", "input"), "6": ("GND", "hier", "passive"),
        }),
        ("C50", "Device", "C", "4.7uF", (88.9, 190.5), {"1": ("VBUS", "local", ""), "2": ("GND", "local", "")}),
        ("C1", "Device", "C", "1uF", (190.5, 96.52), {"1": ("VREG_1V2", "local", ""), "2": ("GND", "local", "")}),
        ("C2", "Device", "C", "1uF", (213.36, 96.52), {"1": ("VREG_2V7", "local", ""), "2": ("GND", "local", "")}),
        ("C3", "Device", "C", "1uF", (236.22, 96.52), {"1": ("VREG_2V7", "local", ""), "2": ("GND", "local", "")}),
        ("C4", "Device", "C", "1uF", (78.74, 96.52), {"1": ("VBUS_FILTERED", "local", ""), "2": ("GND", "local", "")}),
        ("D4", "Device", "D", "D4 (part not specified)", (93.98, 88.9), {"1": ("VBUS_FILTERED", "local", ""), "2": ("VBUS", "local", "")}),
        ("C10", "Device", "C", "100nF", (360.68, 92.71), {"1": ("VSNK", "local", ""), "2": ("GND", "local", "")}),
        ("R1", "Device", "R", "470", (144.78, 127), {"1": ("VBUS_FILTERED", "local", ""), "2": ("VBUS_VS_DISCH", "local", "")}),
        ("R2", "Device", "R", "1k", (274.32, 175.26), {"1": ("DISCH", "local", ""), "2": ("VSNK", "local", "")}),
        ("R3", "Device", "R", "4.7k", (309.88, 213.36), {"1": ("VMCU", "local", ""), "2": ("SCL", "local", "")}),
        ("R4", "Device", "R", "1k", (167.64, 127), {"1": ("VREG_2V7", "local", ""), "2": ("VBUS_VS_DISCH", "local", "")}),
        ("R5", "Device", "R", "4.7k", (332.74, 213.36), {"1": ("VMCU", "local", ""), "2": ("SDA", "local", "")}),
        ("R6", "Device", "R", "4.7k", (355.6, 213.36), {"1": ("VMCU", "local", ""), "2": ("ALERT#", "local", "")}),
        ("R10", "Device", "R", "100k", (297.18, 116.84), {"1": ("FET_MID", "local", ""), "2": ("VSNK_GATE", "local", "")}),
        ("R11", "Device", "R", "22k", (325.12, 124.46), {"1": ("VSNK_GATE", "local", ""), "2": ("GND", "local", "")}),
        ("R13", "Device", "R", "100", (353.06, 124.46), {"1": ("VSNK_GATE", "local", ""), "2": ("VBUS_EN_SNK", "local", "")}),
        ("R51", "Device", "R", "NC", (147.32, 228.6), {"1": ("VREG_2V7", "local", ""), "2": ("ADDR1", "local", "")}),
        ("R52", "Device", "R", "NC", (172.72, 228.6), {"1": ("VREG_2V7", "local", ""), "2": ("ADDR0", "local", "")}),
        ("R53", "Device", "R", "100k", (198.12, 228.6), {"1": ("ADDR1", "local", ""), "2": ("GND", "local", "")}),
        ("R54", "Device", "R", "100k", (223.52, 228.6), {"1": ("ADDR0", "local", ""), "2": ("GND", "local", "")}),
        ("R55", "Device", "R", "10k", (261.62, 228.6), {"1": ("RESET", "local", ""), "2": ("GND", "local", "")}),
        ("D1", "STUSB4500_Custom", "ESDA25W", "ESDA25W", (88.9, 139.7), {"1": ("CC1", "local", ""), "2": ("CC2", "local", ""), "3": ("GND", "local", "")}),
        ("D50", "Device", "D_TVS", "ESDA25P35-1U1M", (106.68, 190.5), {"1": ("VBUS", "local", ""), "2": ("GND", "local", "")}),
        ("D11", "Device", "D_TVS", "ESDA25P35-1U1M", (371.22, 123.19), {"1": ("VSNK", "local", ""), "2": ("GND", "local", "")}),
        ("T4", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", (284.48, 76.2), {"1": ("VBUS_EN_SNK", "local", ""), "2": ("VBUS", "local", ""), "3": ("FET_MID", "local", "")}),
        ("T1", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", (337.82, 76.2), {"1": ("VSNK_GATE", "local", ""), "2": ("FET_MID", "local", ""), "3": ("VSNK", "local", "")}),
    ]

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
        if reference == "U1":
            # Keep text well above the four top-side supply pins and their labels.
            field_x, ref_y, value_y = sx, sy - 44, sy - 41
        elif reference == "J1":
            field_x, ref_y, value_y = sx - 10, sy - 44, sy - 41
        elif reference == "J10":
            # Keep the long connector value clear of the page boundary.
            field_x, ref_y, value_y = sx - 12, sy - 19, sy - 16
        elif reference == "J2":
            field_x, ref_y, value_y = sx + 12, sy - 10, sy - 7
        elif reference in {"T4", "T1"}:
            field_x, ref_y, value_y = sx + 12, sy + 11, sy + 14
        elif reference == "D1":
            field_x, ref_y, value_y = sx, sy - 15, sy - 12
        elif reference.startswith("D"):
            field_x, ref_y, value_y = sx, sy - 12, sy - 9
        elif reference.startswith(("R", "C")):
            field_x, ref_y, value_y = sx - 8, sy - 1.5, sy + 1.5
        else:
            field_x, ref_y, value_y = sx, sy - 8, sy - 5
        instance = [
            "symbol", ["lib_id", _Quoted(lib_id)], ["at", f"{sx:.3f}", f"{sy:.3f}", "0"],
            ["unit", "1"], ["in_bom", "yes"], ["on_board", "yes"], ["dnp", "no"],
            ["uuid", _Quoted(symbol_uuid)],
            _prop("Reference", reference, 0, field_x, ref_y),
            _prop("Value", value, 1, field_x, value_y),
            _prop("Footprint", footprint, 2, sx, sy, True),
            _prop("Datasheet", datasheet, 3, sx, sy, True),
        ]
        for pin in pins:
            instance.append(["pin", _Quoted(pin["number"]), ["uuid", _Quoted(_uid(f"pin:{reference}:{pin['number']}"))]])
            net_spec = pin_map.get(pin["number"])
            if net_spec:
                net, kind, *shape = net_spec
                px = sx + pin["x"]
                py = sy - pin["y"]
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
                        angle = pin["angle"] % 360
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
                        label_x = px + dx * 5.08
                        label_y = py + dy * 5.08
                        justify = "right" if dx < 0 else "left"
                        # The QFN regulator pins occupy one 2.54 mm pitch row.
                        # Rotate only their rail labels to prevent horizontal
                        # text collisions while keeping other pin labels normal.
                        label_angle = 90 if reference == "U1" and dy != 0 else 0
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

    label_stubs = []
    for point, (net, kind, shape_name, x, y, justify, angle) in sorted(label_points.items()):
        px, py = point
        labels.append(
            _label(
                net, x, y, hierarchical=kind == "hier", shape=shape_name,
                justify=justify, angle=angle, key=f"label:{kind}:{net}:{px:.3f}:{py:.3f}",
            )
        )
        if (round(x, 3), round(y, 3)) != point:
            label_stubs.append([
                "wire",
                ["pts", ["xy", f"{px:.3f}", f"{py:.3f}"], ["xy", f"{x:.3f}", f"{y:.3f}"]],
                ["stroke", ["width", "0"], ["type", "default"]],
                ["uuid", _Quoted(_uid(f"label-stub:{kind}:{net}:{px:.3f}:{py:.3f}"))],
            ])
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
        ("VREG_2V7", [("C2", "1"), ("C3", "1")], ("C2", "1")),
        ("GND", [("C1", "2"), ("C2", "2"), ("C3", "2")], ("C1", "2")),
        ("VBUS_VS_DISCH", [("R1", "2"), ("R4", "2")], ("R1", "2")),
        ("VREG_2V7", [("R51", "1"), ("R52", "1")], ("R51", "1")),
        ("GND", [("R53", "2"), ("R54", "2")], ("R53", "2")),
        ("VMCU", [("R3", "1"), ("R5", "1"), ("R6", "1")], ("R3", "1")),
        ("VSNK_GATE", [("R10", "2"), ("R11", "1"), ("R13", "1")], ("R11", "1")),
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

    # The FET common node is a meaningful part of the power path. Route it as
    # one visible, orthogonal trunk with a branch to R10 rather than three
    # unrelated text labels.
    fet_t4_drain = pin_world_points[("T4", "3")]
    fet_t1_source = pin_world_points[("T1", "2")]
    fet_r10_node = pin_world_points[("R10", "1")]
    if fet_t1_source[1] <= fet_t4_drain[1] or fet_r10_node[1] <= fet_t1_source[1]:
        raise ValueError("FET_MID route corridor no longer fits between the FETs and R10.")
    fet_out = (fet_t4_drain[0], round(fet_t4_drain[1] - 5.08, 3))
    fet_lane_x = fet_r10_node[0]
    fet_track_y = round(fet_t1_source[1] + 10.16, 3)
    fet_tee = (fet_r10_node[0], fet_track_y)
    fet_end_track = (fet_t1_source[0], fet_track_y)
    fet_route = [
        fet_t4_drain,
        fet_out,
        (fet_lane_x, fet_out[1]),
        (fet_lane_x, fet_track_y),
        fet_end_track,
        fet_t1_source,
    ]
    for index, (start, end) in enumerate(zip(fet_route, fet_route[1:])):
        wires.append(_wire(start, end, f"FET_MID:power-trunk:{index}"))
    wires.append(_wire(fet_tee, fet_r10_node, "FET_MID:R10-branch"))
    junctions.append([
        "junction",
        ["at", f"{fet_tee[0]:.3f}", f"{fet_tee[1]:.3f}"],
        ["diameter", "0"],
        ["color", "0", "0", "0", "0"],
        ["uuid", _Quoted(_uid(f"junction:FET_MID:{fet_tee[0]:.3f}:{fet_tee[1]:.3f}"))],
    ])
    # Keep one label on the now-wired island so the exported net retains the
    # stable FET_MID name used by downstream netlist checks.
    for ref, pin_number in (("T4", "3"), ("T1", "2")):
        point = pin_world_points[(ref, pin_number)]
        label = label_points.get(point)
        if label is None or label[0] != "FET_MID" or label[1] != "local":
            raise ValueError(f"Expected local FET_MID label is missing at {ref}.{pin_number}.")
        label_points.pop(point)
    anchor_label = label_points.get(fet_r10_node)
    if anchor_label is None or anchor_label[0] != "FET_MID":
        raise ValueError("The FET_MID label anchor at R10.1 is missing.")

    # Route the gate-control lead around the common-source FET node and the
    # VSNK output rail. The vertical leg uses the clear corridor between the
    # VBUS spine (ending at T4) and the FET_MID trunk.
    fet_gate = pin_world_points[("T1", "1")]
    gate_target = pin_world_points[("R10", "2")]
    gate_out = (292.1, fet_gate[1])
    if gate_out[0] >= gate_target[0] or gate_target[1] <= fet_gate[1]:
        raise ValueError("T1 gate route corridor no longer fits its divider branch.")
    gate_route = [
        fet_gate,
        gate_out,
        (gate_out[0], gate_target[1]),
        gate_target,
    ]
    for index, (start, end) in enumerate(zip(gate_route, gate_route[1:])):
        wires.append(_wire(start, end, f"VSNK_GATE:T1-to-R10:{index}"))
    gate_label = label_points.get(fet_gate)
    if gate_label is None or gate_label[0] != "VSNK_GATE" or gate_label[1] != "local":
        raise ValueError("Expected local VSNK_GATE label is missing at T1 gate.")
    label_points.pop(fet_gate)

    # Core input path: the USB-C VBUS contact feeds a clear top rail to the
    # first PMOS switch. A separate short branch serves the input capacitor
    # and TVS at the receptacle; the routes deliberately avoid the CC pair.
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

    # J1 VBUS -> top VBUS spine -> T4 source.
    usb_vbus = pin_world_points[("J1", "A4")]
    t4_vbus = pin_world_points[("T4", "2")]
    add_route("VBUS", [
        usb_vbus, (63.5, usb_vbus[1]), (63.5, 81.28), t4_vbus,
    ], "usb-input-to-power-spine")
    remove_local_label("T4", "2", "VBUS")

    # D4 is the VBUS feed for the filter capacitor; bring its input up to the
    # main rail with a short branch. The crossing is an intentional T-junction.
    d4_vbus = pin_world_points[("D4", "2")]
    d4_rail = (d4_vbus[0], 81.28)
    add_route("VBUS", [d4_vbus, d4_rail], "d4-vbus-tap")
    remove_local_label("D4", "2", "VBUS")
    add_junction(d4_rail, "VBUS:D4-tap")

    # Input bulk capacitor and TVS sit together beside the receptacle. This
    # short local wire joins their VBUS terminals; the named VBUS port carries
    # the longer connection back to J1 and the top power spine.
    c50_vbus = pin_world_points[("C50", "1")]
    d50_vbus = pin_world_points[("D50", "1")]
    add_route("VBUS", [
        c50_vbus, (d50_vbus[0], c50_vbus[1]), d50_vbus,
    ], "input-cap-tvss-feed")
    remove_local_label("D50", "1", "VBUS")

    # A single local ground route joins the input capacitor and the TVS cathode.
    c50_gnd = pin_world_points[("C50", "2")]
    d50_gnd = pin_world_points[("D50", "2")]
    add_route("GND", [
        c50_gnd, (d50_gnd[0], c50_gnd[1]), d50_gnd,
    ], "input-protection-ground")
    remove_local_label("D50", "2", "GND")

    # CC1 and CC2 are routed independently from the receptacle to the
    # protection array. The hierarchical port labels remain at the connector.
    cc1_j1 = pin_world_points[("J1", "A5")]
    cc2_j1 = pin_world_points[("J1", "B5")]
    cc1_d1 = pin_world_points[("D1", "1")]
    cc2_d1 = pin_world_points[("D1", "2")]
    add_route("CC1", [
        cc1_j1, (73.66, cc1_j1[1]), (73.66, cc1_d1[1]), cc1_d1,
    ], "usb-cc1-to-esd")
    add_route("CC2", [
        cc2_j1, (78.74, cc2_j1[1]), (78.74, cc2_d1[1]), cc2_d1,
    ], "usb-cc2-to-esd")
    remove_local_label("D1", "1", "CC1")
    remove_local_label("D1", "2", "CC2")

    # D4's filtered-side terminal faces left. C4 is placed on that side so
    # the bypass connection is a short, direct orthogonal wire; the longer
    # R1/U1 monitor net uses one matching local label at each functional end.
    d4_filtered = pin_world_points[("D4", "1")]
    c4_filtered = pin_world_points[("C4", "1")]
    add_route("VBUS_FILTERED", [
        d4_filtered, (c4_filtered[0], d4_filtered[1]), c4_filtered,
    ], "filter-capacitor-connection")
    remove_local_label("D4", "1", "VBUS_FILTERED")

    # Protected VSNK runs above the switches to J10. C10 and D11 branch from
    # the connector-side output node, keeping the power path visually distinct
    # from the lower FET gate-control wiring.
    t1_vsnk = pin_world_points[("T1", "3")]
    j10_vsnk = pin_world_points[("J10", "1")]
    c10_vsnk = pin_world_points[("C10", "1")]
    d11_vsnk = pin_world_points[("D11", "1")]
    output_rail_y = c10_vsnk[1]
    add_route("VSNK", [
        t1_vsnk, (t1_vsnk[0], output_rail_y),
        c10_vsnk, (j10_vsnk[0], output_rail_y), j10_vsnk,
    ], "protected-output-spine")
    remove_local_label("T1", "3", "VSNK")
    remove_local_label("C10", "1", "VSNK")
    output_tap = (d11_vsnk[0], output_rail_y)
    add_route("VSNK", [output_tap, d11_vsnk], "d11-output-tap")
    remove_local_label("D11", "1", "VSNK")
    add_junction(output_tap, "VSNK:D11-tap")

    # Functional-zone separator; avoid decorative boxes around the circuitry.
    rules = [
        ["polyline",
            ["pts", ["xy", "44.45", "38.1"], ["xy", "398.78", "38.1"]],
            ["stroke", ["width", "0.254"], ["type", "default"]],
            ["fill", ["type", "none"]],
            ["uuid", _Quoted(_uid("graphic:title-rule"))],
        ],
    ]

    schematic = [
        "kicad_sch", ["version", "20250114"], ["generator", _Quoted("kicad-blocks")],
        ["uuid", _Quoted(ROOT_UUID)], ["paper", _Quoted("A3")],
        ["title_block",
            ["title", _Quoted("STUSB4500 USB Type-C / USB Power Delivery Sink")],
            ["date", _Quoted("2026-11-13")],
            ["rev", _Quoted("Layout revision 2")],
            ["company", _Quoted("KiCad Blocks — Reference design")],
            ["comment", "1", _Quoted("Source: ST DS12499 Rev. 8, Figure 10")],
            ["comment", "2", _Quoted("Reference only: verify component ratings, footprints and thermal design before production.")],
        ],
        ["lib_symbols", *library_symbols],
        *instances, *wires, *label_stubs, *labels, *no_connects, *junctions, *rules,
        ["text", _Quoted("STUSB4500QTR — USB Type-C / USB PD sink controller"),
            ["at", "50.8", "30.48", "0"], ["effects", ["font", ["size", "2.0", "2.0"]]],
            ["uuid", _Quoted(_uid("text:title"))],
        ],
        ["text", _Quoted("USB-C INPUT / CC ESD"),
            ["at", "50.8", "63.5", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:input"))],
        ],
        ["text", _Quoted("VBUS FILTER / SENSE"),
            ["at", "114.3", "63.5", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:filtering"))],
        ],
        ["text", _Quoted("PD CONTROLLER / BYPASS"),
            ["at", "190.5", "63.5", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:controller"))],
        ],
        ["text", _Quoted("OPTIONAL HOST / I2C INTERFACE"),
            ["at", "304.8", "180.34", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:mcu"))],
        ],
        ["text", _Quoted("BACK-TO-BACK POWER SWITCH"),
            ["at", "281.94", "58.42", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:power-path"))],
        ],
        ["text", _Quoted("PROTECTED VSNK OUTPUT"),
            ["at", "355.6", "58.42", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:output"))],
        ],
        ["text", _Quoted("I2C ADDRESS / RESET STRAPS"),
            ["at", "147.32", "198.12", "0"], ["effects", ["font", ["size", "1.2", "1.2"]]],
            ["uuid", _Quoted(_uid("text:address"))],
        ],
        ["text", _Quoted("D4 order code is not specified by ST; select a part rated for the final VBUS range."),
            ["at", "50.8", "264.16", "0"], ["effects", ["font", ["size", "1.0", "1.0"]]],
            ["uuid", _Quoted(_uid("text:note"))],
        ],
        ["text", _Quoted("REFERENCE ONLY — complete component, protection, USB-C compliance and thermal review before release."),
            ["at", "50.8", "270.51", "0"], ["effects", ["font", ["size", "1.0", "1.0"]]],
            ["uuid", _Quoted(_uid("text:production-note"))],
        ],
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
