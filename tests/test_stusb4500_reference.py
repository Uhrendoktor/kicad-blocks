"""Checks the STUSB4500 example's native KiCad labels and schematic structure."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from generate_reference import generate_reference_schematic
from kicad_blocks.pcb_layout import _all, _direct, parse_sexpr


class STUSB4500ReferenceTests(unittest.TestCase):
    def test_generates_real_component_schematic_with_hierarchical_ports(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-reference-") as temporary:
            content = generate_reference_schematic(
                Path(temporary) / "reference.kicad_sch"
            ).read_text(encoding="utf-8")

        for token in (
            '(symbol "Interface_USB:STUSB4500QTR"',
            '(symbol "Connector:USB_C_Receptacle"',
            '(symbol "STUSB4500_Custom:ESDA25W"',
            '(symbol "Device:D"',
            '"MPT_0.5_2-2.54"',
            '"STL6P3LLH6"',
            '"ESDA25P35-1U1M"',
            '"D4 (part not specified)"',
            '"4.7uF"',
            '"100nF"',
            '"4.7k"',
            '"100k"',
            '"22k"',
            '"NC"',
            '(label "VBUS_VS_DISCH"',
            '(label "VREG_1V2"',
            '(label "VREG_2V7"',
            '(hierarchical_label "VBUS"',
            '(hierarchical_label "CC1"',
            '(hierarchical_label "CC2"',
            '(hierarchical_label "VSNK"',
            '(hierarchical_label "SCL"',
            '(hierarchical_label "SDA"',
            '(hierarchical_label "ALERT#"',
            '(hierarchical_label "RESET"',
            '(hierarchical_label "VMCU"',
        ):
            self.assertIn(token, content)

        self.assertEqual(content.count("(embedded_fonts no)"), 1)
        wire_nodes = re.findall(r"\(wire \(pts(.*?)\) \(stroke", content)
        self.assertTrue(wire_nodes)
        self.assertTrue(
            all(len(re.findall(r"\(xy ", wire)) == 2 for wire in wire_nodes),
            "Each pin-local stub must be a two-point KiCad wire",
        )
        self.assertLess(
            len(wire_nodes),
            145,
            "Long nets should use local labels; short functional relationships should be routed visibly",
        )
        self.assertNotIn("(global_label ", content)
        self.assertNotIn("VBUS_SENSE", content)
        self.assertNotIn('(label "1V2"', content)
        self.assertIn('(label "VREG_1V2"', content)
        self.assertIn('(label "VREG_2V7"', content)
        # Instance-to-net assignments are verified from KiCad's exported
        # netlist in validate_kicad_cli.py, not as generator-source fragments.

    def test_external_ports_use_standard_kicad_electrical_shapes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-reference-") as temporary:
            content = generate_reference_schematic(
                Path(temporary) / "reference.kicad_sch"
            ).read_text(encoding="utf-8")

        expected = (
            ('"VBUS"', "input"),
            ('"GND"', "passive"),
            ('"CC1"', "bidirectional"),
            ('"CC2"', "bidirectional"),
            ('"VSNK"', "output"),
            ('"SCL"', "input"),
            ('"SDA"', "bidirectional"),
            ('"ALERT#"', "output"),
            ('"RESET"', "input"),
            ('"VMCU"', "input"),
        )
        for name, shape in expected:
            self.assertIn(
                f'(hierarchical_label {name} (shape {shape})',
                content,
            )


    def test_layout_is_grid_aligned_and_functionally_zoned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-layout-") as temporary:
            schematic = generate_reference_schematic(
                Path(temporary) / "reference.kicad_sch"
            )
            content = schematic.read_text(encoding="utf-8")

        for token in (
            '(paper "A3")',
            '(text "USB-C INPUT / CC ESD"',
            '(text "VBUS FILTER / SENSE"',
            '(text "PD CONTROLLER / BYPASS"',
            '(text "BACK-TO-BACK POWER SWITCH"',
            '(text "PROTECTED VSNK OUTPUT"',
            '(text "OPTIONAL HOST / I2C INTERFACE"',
            '(text "I2C ADDRESS / RESET STRAPS"',
            '(polyline',
            '"Layout revision 2"',
            'D4 order code is not specified by ST',
            'REFERENCE ONLY',
        ):
            self.assertIn(token, content)

        tree = parse_sexpr(content)
        placements = {}
        for instance in _all(tree, "symbol"):
            if _direct(instance, "lib_id") is None:
                continue
            reference = next(
                (
                    str(prop[2])
                    for prop in _all(instance, "property")
                    if len(prop) >= 3 and str(prop[1]) == "Reference"
                ),
                None,
            )
            at = _direct(instance, "at")
            if reference is None or at is None:
                continue
            x, y = float(at[1]), float(at[2])
            placements[reference] = (x, y)
            self.assertAlmostEqual(
                x / 2.54, round(x / 2.54), places=3,
                msg=f"{reference} X coordinate is off the 2.54 mm grid",
            )
            self.assertAlmostEqual(
                y / 2.54, round(y / 2.54), places=3,
                msg=f"{reference} Y coordinate is off the 2.54 mm grid",
            )

        expected = {
            "J1": (50.8, 142.24),
            "D1": (88.9, 139.7),
            "C4": (78.74, 96.52),
            "D4": (93.98, 88.9),
            "C50": (88.9, 190.5),
            "D50": (106.68, 190.5),
            "U1": (213.36, 157.48),
            "T4": (284.48, 76.2),
            "T1": (337.82, 76.2),
            "R10": (297.18, 116.84),
            "R11": (325.12, 124.46),
            "R13": (353.06, 124.46),
            "C10": (360.68, 93.98),
            "D11": (373.38, 124.46),
            "J10": (388.62, 93.98),
            "J2": (373.38, 213.36),
        }
        for reference, position in expected.items():
            self.assertEqual(placements.get(reference), position)



if __name__ == "__main__":
    unittest.main()
