"""Checks the real STUSB4500 reference schematic and its KiCad-native ports."""

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
            path = generate_reference_schematic(Path(temporary) / "reference.kicad_sch")
            content = path.read_text(encoding="utf-8")

        self.assertIn('(symbol "Interface_USB:STUSB4500QTR"', content)
        self.assertIn('(symbol "Connector:USB_C_Receptacle"', content)
        self.assertIn('(symbol "STUSB4500_Custom:ESDA25W"', content)
        self.assertIn('"STL6P3LLH6"', content)
        self.assertIn('"ESDA25P35-1U1M"', content)
        self.assertIn('"4.7uF"', content)
        self.assertIn('"100nF"', content)
        self.assertIn('"4.7k"', content)
        self.assertIn('"100k"', content)
        self.assertIn('"22k"', content)
        self.assertIn('"NC"', content)
        self.assertIn('(hierarchical_label "VBUS"', content)
        self.assertIn('(hierarchical_label "CC1"', content)
        self.assertIn('(hierarchical_label "CC2"', content)
        self.assertIn('(hierarchical_label "VSNK"', content)
        self.assertIn('(hierarchical_label "SCL"', content)
        self.assertIn('(hierarchical_label "SDA"', content)
        self.assertEqual(content.count("(embedded_fonts no)"), 1)
        wire_nodes = re.findall(r"\(wire \(pts(.*?)\) \(stroke", content)
        self.assertTrue(wire_nodes)
        self.assertTrue(
            all(len(re.findall(r"\(xy ", wire)) == 2 for wire in wire_nodes),
            "KiCad wire objects must contain exactly two points",
        )
        self.assertIn('(hierarchical_label "ALERT#"', content)
        self.assertIn('(hierarchical_label "RESET"', content)
        self.assertIn('(hierarchical_label "VMCU"', content)
        self.assertNotIn("(global_label ", content)

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
