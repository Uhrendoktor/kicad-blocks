"""Checks the STUSB4500 example's native KiCad labels and schematic structure."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "examples" / "stusb4500"))

from generate_reference import generate_reference_schematic


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
            100,
            "Long nets should use local labels, not coordinate-sorted wire guesses",
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


if __name__ == "__main__":
    unittest.main()
