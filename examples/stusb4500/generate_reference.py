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
    shape: str = "passive", justify: str = "left", key: str,
) -> list:
    effects = ["effects", ["font", ["size", "1.0", "1.0"]], ["justify", justify, "bottom"]]
    node = ["hierarchical_label" if hierarchical else "label", _Quoted(name)]
    if hierarchical:
        node.append(["shape", shape])
    node.extend([
        ["at", f"{x:.3f}", f"{y:.3f}", "0"],
        effects,
        ["uuid", _Quoted(_uid(key))],
    ])
    return node


def generate_reference_schematic(destination: Path) -> Path:
    """Write the editable, datasheet-derived STUSB4500 implementation sheet."""
    root = _symbol_root()
    specs = [
        # ref, library, symbol, value, position, pin-number -> (net, kind, shape)
        ("U1", "Interface_USB", "STUSB4500QTR", "STUSB4500QTR", (205, 160), {
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
        ("J1", "Connector", "USB_C_Receptacle", "USB 3.1 Type-C receptacle", (55, 125), {
            "A1": ("GND", "hier", "passive"), "A4": ("VBUS", "hier", "input"),
            "A5": ("CC1", "hier", "bidirectional"), "A9": ("VBUS", "local", ""),
            "A12": ("GND", "local", ""), "B1": ("GND", "local", ""),
            "B4": ("VBUS", "local", ""), "B5": ("CC2", "hier", "bidirectional"),
            "B9": ("VBUS", "local", ""), "B12": ("GND", "local", ""),
        }),
        ("J10", "Connector_Generic", "Conn_01x02", "MPT_0.5_2-2.54", (395, 85), {
            "1": ("VSNK", "hier", "output"), "2": ("GND", "hier", "passive"),
        }),
        ("J2", "Connector_Generic", "Conn_01x06", "MCU interface", (375, 205), {
            "1": ("SCL", "hier", "input"), "2": ("SDA", "hier", "bidirectional"),
            "3": ("ALERT#", "hier", "output"), "4": ("RESET", "hier", "input"),
            "5": ("VMCU", "hier", "input"), "6": ("GND", "hier", "passive"),
        }),
        ("C50", "Device", "C", "4.7uF", (70, 205), {"1": ("VBUS", "local", ""), "2": ("GND", "local", "")}),
        ("C1", "Device", "C", "1uF", (215, 95), {"1": ("VREG_1V2", "local", ""), "2": ("GND", "local", "")}),
        ("C2", "Device", "C", "1uF", (240, 95), {"1": ("VREG_2V7", "local", ""), "2": ("GND", "local", "")}),
        ("C3", "Device", "C", "1uF", (265, 95), {"1": ("VREG_2V7", "local", ""), "2": ("GND", "local", "")}),
        ("C4", "Device", "C", "1uF", (125, 85), {"1": ("VBUS_FILTERED", "local", ""), "2": ("GND", "local", "")}),
        ("D4", "Device", "D", "D4 (part not specified)", (100, 85), {"1": ("VBUS_FILTERED", "local", ""), "2": ("VBUS", "local", "")}),
        ("C10", "Device", "C", "100nF", (355, 95), {"1": ("VSNK", "local", ""), "2": ("GND", "local", "")}),
        ("R1", "Device", "R", "470", (145, 130), {"1": ("VBUS_FILTERED", "local", ""), "2": ("VBUS_VS_DISCH", "local", "")}),
        ("R2", "Device", "R", "1k", (255, 165), {"1": ("DISCH", "local", ""), "2": ("VSNK", "local", "")}),
        ("R3", "Device", "R", "4.7k", (300, 205), {"1": ("VMCU", "local", ""), "2": ("SCL", "local", "")}),
        ("R4", "Device", "R", "1k", (165, 130), {"1": ("VREG_2V7", "local", ""), "2": ("VBUS_VS_DISCH", "local", "")}),
        ("R5", "Device", "R", "4.7k", (325, 205), {"1": ("VMCU", "local", ""), "2": ("SDA", "local", "")}),
        ("R6", "Device", "R", "4.7k", (350, 205), {"1": ("VMCU", "local", ""), "2": ("ALERT#", "local", "")}),
        ("R10", "Device", "R", "100k", (270, 120), {"1": ("FET_MID", "local", ""), "2": ("VSNK_GATE", "local", "")}),
        ("R11", "Device", "R", "22k", (300, 120), {"1": ("VSNK_GATE", "local", ""), "2": ("GND", "local", "")}),
        ("R13", "Device", "R", "100", (330, 120), {"1": ("VSNK_GATE", "local", ""), "2": ("VBUS_EN_SNK", "local", "")}),
        ("R51", "Device", "R", "NC", (150, 230), {"1": ("VREG_2V7", "local", ""), "2": ("ADDR1", "local", "")}),
        ("R52", "Device", "R", "NC", (175, 230), {"1": ("VREG_2V7", "local", ""), "2": ("ADDR0", "local", "")}),
        ("R53", "Device", "R", "100k", (200, 230), {"1": ("ADDR1", "local", ""), "2": ("GND", "local", "")}),
        ("R54", "Device", "R", "100k", (225, 230), {"1": ("ADDR0", "local", ""), "2": ("GND", "local", "")}),
        ("R55", "Device", "R", "10k", (260, 235), {"1": ("RESET", "local", ""), "2": ("GND", "local", "")}),
        ("D1", "STUSB4500_Custom", "ESDA25W", "ESDA25W", (135, 180), {"1": ("CC1", "local", ""), "2": ("CC2", "local", ""), "3": ("GND", "local", "")}),
        ("D50", "Device", "D_TVS", "ESDA25P35-1U1M", (90, 205), {"1": ("VBUS", "local", ""), "2": ("GND", "local", "")}),
        ("D11", "Device", "D_TVS", "ESDA25P35-1U1M", (370, 130), {"1": ("VSNK", "local", ""), "2": ("GND", "local", "")}),
        ("T4", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", (275, 75), {"1": ("VBUS_EN_SNK", "local", ""), "2": ("VBUS", "local", ""), "3": ("FET_MID", "local", "")}),
        ("T1", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", (325, 75), {"1": ("VSNK_GATE", "local", ""), "2": ("FET_MID", "local", ""), "3": ("VSNK", "local", "")}),
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
        tuple[str, str, str | None, float, float, str],
    ] = {}
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
            field_x, ref_y, value_y = sx + 12, sy - 44, sy - 41
        elif reference == "J10":
            # The connector's two pins face left; place fields above, not over ports.
            field_x, ref_y, value_y = sx + 12, sy - 19, sy - 16
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
                        # One label is sufficient for stacked connector pins.
                        # Prefer the hierarchical port when it shares a point
                        # with local labels for the same electrical net.
                        label_points[point] = (
                            net, kind, shape_name, label_x, label_y, justify,
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
    for point, (net, kind, shape_name, x, y, justify) in sorted(label_points.items()):
        px, py = point
        labels.append(
            _label(
                net, x, y, hierarchical=kind == "hier", shape=shape_name,
                justify=justify, key=f"label:{kind}:{net}:{px:.3f}:{py:.3f}",
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

    # Long connections use matching local net labels. The former coordinate-
    # sort router joined successive pin coordinates without knowing the
    # circuit topology, producing crossings and misleading visual connections.
    # Only short pin-local stubs are drawn; KiCad's exported netlist is checked.
    wires = []

    schematic = [
        "kicad_sch", ["version", "20250114"], ["generator", _Quoted("kicad-blocks")],
        ["uuid", _Quoted(ROOT_UUID)], ["paper", _Quoted("A3")],
        ["title_block",
            ["title", _Quoted("STUSB4500 USB Power Delivery Sink")],
            ["date", _Quoted("2026-11-12")],
            ["rev", _Quoted("Reference design")],
            ["company", _Quoted("KiCad Blocks example")],
            ["comment", "1", _Quoted("Based on ST DS12499 Rev. 8, Figure 10")],
            ["comment", "2", _Quoted("Review component ratings, layout and protection before production")],
        ],
        ["lib_symbols", *library_symbols],
        *instances, *wires, *label_stubs, *labels, *no_connects,
        ["text", _Quoted("STUSB4500QTR — USB Type-C / USB PD sink controller"),
            ["at", "50", "30", "0"], ["effects", ["font", ["size", "2.0", "2.0"]]],
            ["uuid", _Quoted(_uid("text:title"))],
        ],
        ["text", _Quoted("USB TYPE-C INPUT / CC PROTECTION"),
            ["at", "50", "63", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:input"))],
        ],
        ["text", _Quoted("VBUS FILTER / MONITOR"),
            ["at", "115", "63", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:filtering"))],
        ],
        ["text", _Quoted("STUSB4500 / LOCAL DECOUPLING"),
            ["at", "190", "63", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:controller"))],
        ],
        ["text", _Quoted("OPTIONAL MCU / I2C INTERFACE"),
            ["at", "305", "165", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:mcu"))],
        ],
        ["text", _Quoted("BACK-TO-BACK VBUS POWER PATH"),
            ["at", "270", "48", "0"], ["effects", ["font", ["size", "1.4", "1.4"]]],
            ["uuid", _Quoted(_uid("text:power-path"))],
        ],
        ["text", _Quoted("I2C ADDRESS STRAPS"),
            ["at", "140", "210", "0"], ["effects", ["font", ["size", "1.2", "1.2"]]],
            ["uuid", _Quoted(_uid("text:address"))],
        ],
        ["text", _Quoted("Pin-local labels keep long nets legible; netlist validated in CI."),
            ["at", "50", "270", "0"], ["effects", ["font", ["size", "1.0", "1.0"]]],
            ["uuid", _Quoted(_uid("text:note"))],
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
