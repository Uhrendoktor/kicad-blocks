from pathlib import Path
import sys

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import block_geometry, parse_pins, slugify  # noqa: E402
from kicad_blocks.sheet_file import build_child_schematic  # noqa: E402


def test_parse_pins_and_aliases():
    pins = parse_pins("VIN:input, GND, SDA:bidir, ALERT:tristate")
    assert [pin.shape for pin in pins] == [0, 4, 2, 3]
    assert [(pin.name, pin.electrical_type) for pin in pins] == [
        ("VIN", "input"), ("GND", "passive"),
        ("SDA", "bidirectional"), ("ALERT", "tristate")
    ]


def test_pin_validation():
    for bad in ("", "A:unknown", "VIN:input,vin:output", "bad name:input"):
        try:
            parse_pins(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid pin list: {bad}")


def test_slugify():
    assert slugify("DC/DC Converter 3.3V") == "dc_dc_converter_3_3v"


def test_block_geometry():
    height, left, right = block_geometry(5)
    assert height >= 20.32
    assert (left, right) == (3, 2)



def test_child_file_contains_all_hierarchical_ports():
    pins = parse_pins("VIN:input, GND:passive, VOUT:output")
    content = build_child_schematic("Power block", pins, "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
    assert '(hierarchical_label "VIN" (shape input)' in content
    assert '(hierarchical_label "GND" (shape passive)' in content
    assert '(hierarchical_label "VOUT" (shape output)' in content
    assert '(path "/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222"' in content
