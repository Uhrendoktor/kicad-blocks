"""Unit tests for KiCad PCB net-reference serialization rules."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
from kicad_blocks.pcb_layout import (
    _dump,
    _merge_project_net_settings,
    _Quoted,
    _rewrite_nets,
    _transform_item,
    parse_sexpr,
    resolve_block_layout,
)


class PCBNetRewriteTests(unittest.TestCase):
    def test_resolve_named_layout_and_legacy_single_layout(self):
        block = SimpleNamespace(
            name="Power Block",
            path=Path("/tmp/power.kicad_block"),
            manifest={
                "layouts": [
                    {
                        "id": "native",
                        "name": "Native",
                        "file": "layout_native.kicad_pcb",
                    },
                    {
                        "id": "low_voltage",
                        "name": "Low voltage",
                        "file": "layout_low_voltage.kicad_pcb",
                        "project_file": "layout_low_voltage.kicad_pro",
                        "ports": {"VIN": "VDD18"},
                    },
                ],
            },
        )
        selected = resolve_block_layout(block, "low_voltage")
        self.assertEqual(selected["name"], "Low voltage")
        self.assertEqual(selected["file"], "layout_low_voltage.kicad_pcb")
        self.assertEqual(selected["project_file"], "layout_low_voltage.kicad_pro")
        self.assertEqual(selected["ports"], {"VIN": "VDD18"})
        with self.assertRaisesRegex(ValueError, "multiple PCB layouts"):
            resolve_block_layout(block)
        defaulted = SimpleNamespace(
            name=block.name,
            path=block.path,
            manifest={**block.manifest, "default_layout": "native"},
        )
        self.assertEqual(resolve_block_layout(defaulted)["id"], "native")
        with self.assertRaisesRegex(ValueError, "Unknown PCB layout"):
            resolve_block_layout(block, "missing")

        legacy = SimpleNamespace(
            name="Old Block",
            path=Path("/tmp/old.kicad_block"),
            manifest={
                "layout_file": "layout.kicad_pcb",
                "layout_project_file": "layout.kicad_pro",
            },
        )
        self.assertEqual(
            resolve_block_layout(legacy),
            {
                "id": None,
                "name": "layout",
                "file": "layout.kicad_pcb",
                "project_file": "layout.kicad_pro",
                "ports": None,
            },
        )

    def test_named_pad_net_keeps_mapped_code_and_net_name(self):
        pad = parse_sexpr('(pad "1" smd rect (net 7 "SW"))')
        _rewrite_nets(pad, {7: (2, "VIN_BOARD")}, include_net_name=True)
        self.assertEqual(_dump(pad), '(pad "1" smd rect (net 2 "VIN_BOARD"))')
        self.assertIsInstance(pad[4][2], _Quoted)

    def test_track_net_remains_numeric_only(self):
        segment = parse_sexpr("(segment (start 1 2) (end 3 4) (net 7))")
        _rewrite_nets(segment, {7: (9, "X1_SW")}, include_net_name=False)
        self.assertEqual(
            _dump(segment),
            "(segment (start 1 2) (end 3 4) (net 9))",
        )

    def test_rotation_transforms_footprint_and_absolute_fields_but_not_local_pads(self):
        footprint = parse_sexpr(
            '(footprint "Test:Part" '
            "(at 100 100 0) "
            '(property "Reference" "R1" (at 100 98 0)) '
            '(pad "1" smd rect (at -0.75 0 0) (size 1 1) (net 0 "")))'
        )
        _transform_item(
            footprint,
            anchor_mm=(100.0, 100.0),
            position_mm=(120.0, 130.0),
            rotation_deg=90.0,
        )

        self.assertIn("(at 120 130 90)", _dump(footprint))
        self.assertIn("(at 118 130 90)", _dump(footprint))
        self.assertIn("(at -0.75 0 0)", _dump(footprint))

    def test_rotation_transforms_track_endpoints_about_layout_anchor(self):
        segment = parse_sexpr(
            "(segment (start 100 100) (end 105 100) (width 0.25) "
            '(layer "F.Cu") (net 3) (uuid "old"))'
        )
        _transform_item(
            segment,
            anchor_mm=(100.0, 100.0),
            position_mm=(120.0, 130.0),
            rotation_deg=90.0,
        )
        self.assertIn("(start 120 130)", _dump(segment))
        self.assertIn("(end 120 125)", _dump(segment))

    def test_project_netclasses_merge_namespaced_and_external_assignments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target.kicad_pro"
            source = root / "layout.kicad_pro"
            output = root / "merged.kicad_pro"
            target.write_text(
                json.dumps(
                    {
                        "meta": {"filename": "target.kicad_pro", "version": 3},
                        "net_settings": {
                            "classes": [
                                {
                                    "name": "Default",
                                    "priority": 2147483647,
                                    "clearance": 0.2,
                                },
                                {
                                    "name": "Fast",
                                    "priority": 0,
                                    "clearance": 0.2,
                                    "track_width": 0.2,
                                },
                            ],
                            "netclass_assignments": {"VIN_BOARD": ["Fast"]},
                            "net_chain_classes": {"PROJECT_CHAIN": "PROJECT_GROUP"},
                            "net_chain_netclasses": {},
                        },
                    }
                ),
                encoding="utf-8",
            )
            source.write_text(
                json.dumps(
                    {
                        "meta": {"filename": "layout.kicad_pro", "version": 3},
                        "net_settings": {
                            "classes": [
                                {
                                    "name": "Default",
                                    "priority": 2147483647,
                                    "clearance": 0.2,
                                },
                                {
                                    "name": "Fast",
                                    "priority": 0,
                                    "clearance": 0.1,
                                    "track_width": 0.15,
                                },
                            ],
                            "netclass_assignments": {
                                "VIN": ["Fast"],
                                "SW": ["Fast"],
                            },
                            "net_chain_classes": {"SIG_CHAIN": "SERIAL_GROUP"},
                            "net_chain_netclasses": {"SIG_CHAIN": "Fast"},
                            "netclass_patterns": [
                                {"pattern": "G*", "netclass": "Fast"},
                                {"pattern": "S*", "netclass": "Fast"},
                                {"pattern": "NO_MATCH*", "netclass": "Fast"},
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            original_target = target.read_bytes()
            original_source = source.read_bytes()

            result = _merge_project_net_settings(
                target,
                source,
                output,
                instance_reference="X1",
                layout_ports={"VIN": "VIN", "GND": "GND"},
                pin_to_board_net={"VIN": "VIN_BOARD", "GND": "GND_BOARD"},
                source_net_to_board_net={
                    "VIN": "VIN_BOARD",
                    "GND": "GND_BOARD",
                    "SW": "X1_SW",
                },
                source_chain_name_map={"SIG_CHAIN": "X1_SIG_CHAIN"},
            )

            self.assertEqual(result["merged_classes"], ["X1_Fast"])
            self.assertEqual(result["assigned_nets"], ["VIN_BOARD", "X1_SW"])
            self.assertTrue(result["ignored_patterns"])
            self.assertEqual(
                result["applied_patterns"],
                [
                    {
                        "pattern": "G*",
                        "netclass": "Fast",
                        "resolved_nets": ["GND_BOARD"],
                    },
                    {"pattern": "S*", "netclass": "Fast", "resolved_nets": ["X1_SW"]},
                ],
            )
            self.assertEqual(result["unmatched_patterns"], ["NO_MATCH*"])
            self.assertEqual(
                result["data"]["net_settings"]["net_chain_classes"],
                {
                    "PROJECT_CHAIN": "PROJECT_GROUP",
                    "X1_SIG_CHAIN": "SERIAL_GROUP",
                },
            )
            self.assertEqual(
                result["data"]["net_settings"]["net_chain_netclasses"],
                {"X1_SIG_CHAIN": "X1_Fast"},
            )
            self.assertEqual(result["merged_chain_names"], ["X1_SIG_CHAIN"])
            self.assertFalse(result["ignored_chain_classes"])
            self.assertEqual(target.read_bytes(), original_target)
            self.assertEqual(source.read_bytes(), original_source)
            merged = result["data"]
            self.assertEqual(
                merged["net_settings"]["netclass_assignments"]["VIN_BOARD"],
                ["Fast", "X1_Fast"],
            )
            self.assertEqual(
                merged["net_settings"]["netclass_assignments"]["X1_SW"],
                ["X1_Fast"],
            )
            self.assertEqual(merged["meta"]["filename"], output.name)

    def test_kicad_netclass_regex_and_bus_vector_patterns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target.kicad_pro"
            source = root / "layout.kicad_pro"
            output = root / "merged.kicad_pro"
            target.write_text(
                json.dumps(
                    {
                        "net_settings": {
                            "classes": [{"name": "Default", "priority": 2147483647}]
                        }
                    }
                ),
                encoding="utf-8",
            )
            source.write_text(
                json.dumps(
                    {
                        "net_settings": {
                            "classes": [
                                {"name": "Default", "priority": 2147483647},
                                {"name": "BusClass", "priority": 0},
                            ],
                            "netclass_patterns": [
                                {"pattern": "D[0..2]", "netclass": "BusClass"},
                                {"pattern": "^CLK(_N)?$", "netclass": "BusClass"},
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = _merge_project_net_settings(
                target,
                source,
                output,
                instance_reference="U1",
                layout_ports={},
                pin_to_board_net={},
                source_net_to_board_net={
                    "D0": "U1_D0",
                    "D1": "U1_D1",
                    "D2": "U1_D2",
                    "CLK": "CLK_BOARD",
                    "CLK_N": "U1_CLK_N",
                    "CLK_P": "U1_CLK_P",
                },
            )
            assignments = result["data"]["net_settings"]["netclass_assignments"]
            self.assertEqual(assignments["U1_D0"], ["BusClass"])
            self.assertEqual(assignments["U1_D1"], ["BusClass"])
            self.assertEqual(assignments["U1_D2"], ["BusClass"])
            self.assertEqual(assignments["CLK_BOARD"], ["BusClass"])
            self.assertEqual(assignments["U1_CLK_N"], ["BusClass"])
            self.assertNotIn("U1_CLK_P", assignments)
            self.assertEqual(result["unmatched_patterns"], [])

    def test_unmatched_project_chain_overrides_are_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target.kicad_pro"
            source = root / "layout.kicad_pro"
            output = root / "merged.kicad_pro"
            target.write_text(
                json.dumps(
                    {
                        "net_settings": {
                            "classes": [{"name": "Default", "priority": 2147483647}]
                        }
                    }
                ),
                encoding="utf-8",
            )
            source.write_text(
                json.dumps(
                    {
                        "net_settings": {
                            "classes": [{"name": "Default", "priority": 2147483647}],
                            "net_chain_classes": {"STALE_CHAIN": "GROUP"},
                            "net_chain_netclasses": {},
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = _merge_project_net_settings(
                target,
                source,
                output,
                instance_reference="U1",
                layout_ports={},
                pin_to_board_net={},
                source_chain_name_map={},
            )
            self.assertTrue(result["ignored_chain_classes"])
            self.assertEqual(
                result["unmatched_chains"],
                ["net_chain_classes:STALE_CHAIN"],
            )
            self.assertEqual(result["data"]["net_settings"]["net_chain_classes"], {})

    def test_unknown_net_code_is_rejected(self):
        segment = parse_sexpr("(segment (net 99))")
        with self.assertRaisesRegex(ValueError, "undefined source net code 99"):
            _rewrite_nets(segment, {7: (9, "X1_SW")}, include_net_name=False)


if __name__ == "__main__":
    unittest.main()
