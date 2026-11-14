"""Regression tests for automatic block dependency discovery."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.auto_dependencies import (
    discover_block_dependencies,
    parse_kicad_xml_netlist,
)
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import (
    add_dependency,
    create_block,
    read_manifest,
    synchronize_dependencies,
)


def _symbol(record, reference="B1", *, orientation=2, position=(100.0, 120.0)):
    return SimpleNamespace(
        reference=reference,
        reference_field=SimpleNamespace(
            text=SimpleNamespace(value=reference)
        ),
        definition=SimpleNamespace(
            id=SimpleNamespace(
                library=record.manifest["library_nickname"],
                name=record.manifest["symbol_name"],
            ),
            pins=[
                SimpleNamespace(number=str(pin["number"]))
                for pin in record.manifest["pins"]
            ],
        ),
        transform=SimpleNamespace(orientation=orientation),
        position=SimpleNamespace(
            x=position[0] * 1_000_000,
            y=position[1] * 1_000_000,
        ),
    )


class AutomaticDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "library"
        self.parent = create_block(
            self.root,
            "Controller",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
        )
        self.child = create_block(
            self.root,
            "Power Stage",
            parse_pins("IN:input, GND:passive, OUT:output"),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_parse_kicad_xml_netlist_maps_reference_pin_numbers_to_nets(self):
        netlist = Path(self.temp.name) / "implementation.xml"
        netlist.write_text(
            """<?xml version="1.0"?>
            <export version="E">
              <components>
                <comp ref="B1"><value>Power Stage</value></comp>
                <comp ref="R1"><value>10k</value></comp>
              </components>
              <nets>
                <net code="1" name="VIN">
                  <node ref="B1" pin="1"/>
                  <node ref="R1" pin="1"/>
                </net>
                <net code="2" name="GND">
                  <node ref="B1" pin="2"/>
                </net>
              </nets>
            </export>""",
            encoding="utf-8",
        )

        result = parse_kicad_xml_netlist(netlist)

        self.assertEqual(result["component_refs"], {"B1", "R1"})
        self.assertEqual(
            result["pin_nets"],
            {"B1": {"1": "VIN", "2": "GND"}, "R1": {"1": "VIN"}},
        )

    def test_discovery_uses_pin_numbers_and_preserves_unconnected_pins(self):
        symbol = _symbol(self.child)
        netlist = {
            "component_refs": {"B1"},
            "pin_nets": {"B1": {"1": "VIN_PARENT", "2": "GND_PARENT"}},
        }

        dependencies = discover_block_dependencies(
            self.parent, [self.child], [symbol], netlist
        )

        self.assertEqual(len(dependencies), 1)
        dependency = dependencies[0]
        self.assertIs(dependency["child"], self.child)
        self.assertEqual(dependency["reference"], "B1")
        self.assertEqual(dependency["position_mm"], (100.0, 120.0))
        self.assertEqual(dependency["orientation"], 90)
        self.assertEqual(
            dependency["pin_map"],
            {"IN": "VIN_PARENT", "GND": "GND_PARENT", "OUT": None},
        )

    def test_discovery_ignores_non_block_symbols(self):
        ordinary_symbol = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(library="Device", name="R")
            )
        )

        self.assertEqual(
            discover_block_dependencies(
                self.parent, [], [ordinary_symbol], {"component_refs": set(), "pin_nets": {}}
            ),
            [],
        )

    def test_missing_registered_facade_is_not_silently_ignored(self):
        unknown = SimpleNamespace(
            reference="B1",
            definition=SimpleNamespace(
                id=SimpleNamespace(library="KBlocks", name="missing_facade"),
                pins=[],
            ),
        )

        with self.assertRaisesRegex(FileNotFoundError, "not present"):
            discover_block_dependencies(
                self.parent, [], [unknown], {"component_refs": {"B1"}, "pin_nets": {}}
            )

    def test_reconciliation_preserves_generated_kicad_net_names(self):
        occurrence = {
            "child": self.child,
            "reference": "B1",
            "position_mm": (100.0, 120.0),
            "orientation": 0,
            "pin_map": {
                "IN": "Net-(R1-Pad2)",
                "GND": "GND",
                "OUT": None,
            },
        }

        result = synchronize_dependencies(self.parent, [occurrence])

        self.assertEqual(
            result.manifest["dependencies"][0]["pin_map"]["IN"],
            "Net-(R1-Pad2)",
        )

    def test_reconciliation_rejects_a_dependency_cycle(self):
        add_dependency(self.child, self.parent)
        occurrence = {
            "child": self.child,
            "reference": "B1",
            "position_mm": (100.0, 120.0),
            "orientation": 0,
            "pin_map": {"IN": "VIN", "GND": "GND", "OUT": "VOUT"},
        }

        with self.assertRaisesRegex(ValueError, "cycle"):
            synchronize_dependencies(self.parent, [occurrence])

    def test_reconciliation_preserves_instance_id_and_removes_deleted_facades(self):
        occurrence = {
            "child": self.child,
            "reference": "B1",
            "position_mm": (100.0, 120.0),
            "orientation": 90,
            "pin_map": {"IN": "VIN_PARENT", "GND": "GND_PARENT", "OUT": None},
        }

        first = synchronize_dependencies(self.parent, [occurrence])
        original_id = first.manifest["dependencies"][0]["instance_id"]
        second = synchronize_dependencies(first, [occurrence])
        self.assertEqual(second.manifest["dependencies"][0]["instance_id"], original_id)
        self.assertIsNone(second.manifest["dependencies"][0]["pin_map"]["OUT"])

        removed = synchronize_dependencies(second, [])
        self.assertEqual(removed.manifest["dependencies"], [])
        self.assertEqual(read_manifest(self.parent.path)["dependencies"], [])


if __name__ == "__main__":
    unittest.main()
