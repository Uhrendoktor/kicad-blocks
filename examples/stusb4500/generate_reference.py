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
PROJECT = "STUSB4500 reference implementation"


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


def _label(name: str, x: float, y: float, *, hierarchical: bool = False, shape: str = "passive", key: str) -> list:
    effects = ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", "left", "bottom"]]
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
        ("U1", "Interface_USB", "STUSB4500QTR", "STUSB4500QTR", (190, 160), {
            "1": ("CC1", "local", ""), "2": ("CC1", "local", ""),
            "3": ("NC", "nc", ""), "4": ("CC2", "local", ""),
            "5": ("CC2", "local", ""), "6": ("RESET", "local", ""),
            "7": ("SCL", "local", ""), "8": ("SDA", "local", ""),
            "9": ("DISCH", "local", ""), "10": ("GND", "local", ""),
            "11": ("NC", "nc", ""), "12": ("ADDR0", "local", ""),
            "13": ("ADDR1", "local", ""), "14": ("NC", "nc", ""),
            "15": ("NC", "nc", ""), "16": ("VBUS_EN_SNK", "local", ""),
            "17": ("NC", "nc", ""), "18": ("VBUS_SENSE", "local", ""),
            "19": ("ALERT#", "local", ""), "20": ("POWER_OK2", "local", ""),
            "21": ("1V2", "local", ""), "22": ("GND", "local", ""),
            "23": ("2V7", "local", ""), "24": ("VBUS", "local", ""),
            "25": ("GND", "local", ""),
        }),
        ("J1", "Connector", "USB_C_Receptacle", "USB 3.1 Type-C receptacle", (55, 125), {
            "A1": ("GND", "hier", "passive"), "A4": ("VBUS", "hier", "input"),
            "A5": ("CC1", "hier", "bidirectional"), "A9": ("VBUS", "local", ""),
            "A12": ("GND", "local", ""), "B1": ("GND", "local", ""),
            "B4": ("VBUS", "local", ""), "B5": ("CC2", "hier", "bidirectional"),
            "B9": ("VBUS", "local", ""), "B12": ("GND", "local", ""),
        }),
        ("J10", "Connector_Generic", "Conn_01x02", "VSNK output", (370, 75), {
            "1": ("VSNK", "hier", "output"), "2": ("GND", "hier", "passive"),
        }),
        ("J2", "Connector_Generic", "Conn_01x06", "MCU interface", (360, 185), {
            "1": ("SCL", "hier", "input"), "2": ("SDA", "hier", "bidirectional"),
            "3": ("ALERT#", "hier", "output"), "4": ("RESET", "hier", "input"),
            "5": ("VMCU", "hier", "input"), "6": ("GND", "hier", "passive"),
        }),
        ("C50", "Device", "C", "4.7uF", (75, 210), {"1": ("VBUS", "local", ""), "2": ("GND", "local", "")}),
        ("C1", "Device", "C", "1uF", (145, 95), {"1": ("1V2", "local", ""), "2": ("GND", "local", "")}),
        ("C2", "Device", "C", "1uF", (170, 95), {"1": ("2V7", "local", ""), "2": ("GND", "local", "")}),
        ("C3", "Device", "C", "1uF", (195, 95), {"1": ("2V7", "local", ""), "2": ("GND", "local", "")}),
        ("C4", "Device", "C", "1uF", (115, 95), {"1": ("2V7", "local", ""), "2": ("GND", "local", "")}),
        ("C10", "Device", "C", "100nF", (330, 95), {"1": ("VSNK", "local", ""), "2": ("GND", "local", "")}),
        ("R1", "Device", "R", "470", (115, 135), {"1": ("VBUS", "local", ""), "2": ("VBUS_SENSE", "local", "")}),
        ("R2", "Device", "R", "1k", (260, 160), {"1": ("DISCH", "local", ""), "2": ("GND", "local", "")}),
        ("R3", "Device", "R", "4.7k", (315, 155), {"1": ("VMCU", "local", ""), "2": ("SCL", "local", "")}),
        ("R4", "Device", "R", "1k", (135, 135), {"1": ("2V7", "local", ""), "2": ("VBUS_SENSE", "local", "")}),
        ("R5", "Device", "R", "4.7k", (335, 155), {"1": ("VMCU", "local", ""), "2": ("SDA", "local", "")}),
        ("R6", "Device", "R", "4.7k", (355, 155), {"1": ("VMCU", "local", ""), "2": ("ALERT#", "local", "")}),
        ("R10", "Device", "R", "100k", (250, 85), {"1": ("VBUS", "local", ""), "2": ("VSNK_GATE", "local", "")}),
        ("R11", "Device", "R", "22k", (280, 85), {"1": ("VSNK_GATE", "local", ""), "2": ("GND", "local", "")}),
        ("R13", "Device", "R", "100", (315, 85), {"1": ("VSNK_GATE", "local", ""), "2": ("VBUS_EN_SNK", "local", "")}),
        ("R51", "Device", "R", "NC", (145, 225), {"1": ("2V7", "local", ""), "2": ("ADDR1", "local", "")}),
        ("R52", "Device", "R", "NC", (170, 225), {"1": ("2V7", "local", ""), "2": ("ADDR0", "local", "")}),
        ("R53", "Device", "R", "100k", (195, 225), {"1": ("ADDR1", "local", ""), "2": ("GND", "local", "")}),
        ("R54", "Device", "R", "100k", (220, 225), {"1": ("ADDR0", "local", ""), "2": ("GND", "local", "")}),
        ("R55", "Device", "R", "10k", (325, 225), {"1": ("RESET", "local", ""), "2": ("GND", "local", "")}),
        ("D1", "STUSB4500_Custom", "ESDA25W", "ESDA25W", (130, 190), {"1": ("CC1", "local", ""), "2": ("CC2", "local", ""), "3": ("GND", "local", "")}),
        ("D50", "Device", "D_TVS", "ESDA25P35-1U1M", (95, 190), {"1": ("VBUS", "local", ""), "2": ("GND", "local", "")}),
        ("D11", "Device", "D_TVS", "ESDA25P35-1U1M", (345, 110), {"1": ("VSNK", "local", ""), "2": ("GND", "local", "")}),
        ("T4", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", (260, 65), {"1": ("VBUS_EN_SNK", "local", ""), "2": ("VBUS", "local", ""), "3": ("VSNK_GATE", "local", "")}),
        ("T1", "Transistor_FET", "Q_PMOS_GSD", "STL6P3LLH6", (300, 65), {"1": ("VSNK_GATE", "local", ""), "2": ("VBUS", "local", ""), "3": ("VSNK", "local", "")}),
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
    label_points: dict[tuple[float, float], tuple[str, str, str | None]] = {}
    no_connect_points: set[tuple[float, float]] = set()
    net_points: dict[str, list[tuple[str, float, float]]] = {}
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
            field_x, ref_y, value_y = sx, sy - 27, sy - 24
        elif reference == "J1":
            field_x, ref_y, value_y = sx + 20, sy - 40, sy - 37
        elif reference in {"J2", "J10"}:
            field_x, ref_y, value_y = sx + 10, sy - 8, sy - 5
        else:
            field_x, ref_y, value_y = sx + 5, sy - 7, sy - 4
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
                    net_points.setdefault(net, []).append((reference, px, py))
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
                        # One label is sufficient for stacked connector pins.
                        # Prefer the hierarchical port when it shares a point
                        # with local labels for the same electrical net.
                        label_points[point] = (net, kind, shape_name)
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

    for (point, (net, kind, shape_name)) in sorted(label_points.items()):
        x, y = point
        labels.append(
            _label(
                net, x, y, hierarchical=kind == "hier", shape=shape_name,
                key=f"label:{kind}:{net}:{x:.3f}:{y:.3f}",
            )
        )
    for point in sorted(no_connect_points - set(label_points)):
        x, y = point
        no_connects.append([
            "no_connect", ["at", f"{x:.3f}", f"{y:.3f}"],
            ["uuid", _Quoted(_uid(f"nc:{x:.3f}:{y:.3f}"))],
        ])

    # Draw visible orthogonal connections for the main signal and power nets.
    # Local labels remain on each pin, so the topology is both readable and
    # unambiguous to KiCad's netlist exporter.
    wires = []
    for net in ("VBUS", "CC1", "CC2", "VSNK", "SCL", "SDA", "ALERT#", "RESET", "VMCU", "POWER_OK2", "VBUS_EN_SNK"):
        by_reference = {}
        for reference, x, y in net_points.get(net, []):
            by_reference.setdefault(reference, (x, y))
        points = sorted(
            ((reference, *position) for reference, position in by_reference.items()),
            key=lambda item: item[1],
        )
        for index, (left, right) in enumerate(zip(points, points[1:])):
            ref_a, x_a, y_a = left
            ref_b, x_b, y_b = right
            route = [["xy", f"{x_a:.3f}", f"{y_a:.3f}"]]
            if abs(x_a - x_b) > 0.001 and abs(y_a - y_b) > 0.001:
                route.append(["xy", f"{x_b:.3f}", f"{y_a:.3f}"])
            route.append(["xy", f"{x_b:.3f}", f"{y_b:.3f}"])
            wires.append([
                "wire", ["pts", *route],
                ["stroke", ["width", "0"], ["type", "default"]],
                ["uuid", _Quoted(_uid(f"wire:{net}:{index}:{ref_a}:{ref_b}"))],
            ])

    schematic = [
        "kicad_sch", ["version", "20250114"], ["generator", _Quoted("eeschema")],
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
        *instances, *wires, *labels, *no_connects,
        ["text", _Quoted("STUSB4500QTR — USB Type-C / USB PD sink controller"),
            ["at", "55", "35", "0"], ["effects", ["font", ["size", "2.0", "2.0"]]],
            ["uuid", _Quoted(_uid("text:title"))],
        ],
        ["text", _Quoted("USB-C input and CC protection"),
            ["at", "55", "55", "0"], ["effects", ["font", ["size", "1.27", "1.27"]]],
            ["uuid", _Quoted(_uid("text:input"))],
        ],
        ["text", _Quoted("Input filtering, controller decoupling and address straps"),
            ["at", "130", "50", "0"], ["effects", ["font", ["size", "1.27", "1.27"]]],
            ["uuid", _Quoted(_uid("text:filtering"))],
        ],
        ["text", _Quoted("Protected VBUS power path and sink output"),
            ["at", "235", "85", "0"], ["effects", ["font", ["size", "1.27", "1.27"]]],
            ["uuid", _Quoted(_uid("text:power-path"))],
        ],
        ["text", _Quoted("Optional MCU: I2C, interrupt and reset"),
            ["at", "285", "160", "0"], ["effects", ["font", ["size", "1.27", "1.27"]]],
            ["uuid", _Quoted(_uid("text:mcu"))],
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
