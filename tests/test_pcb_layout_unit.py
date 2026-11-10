"""Unit tests for KiCad PCB net-reference serialization rules."""

import sys
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.pcb_layout import (
    _dump,
    _Quoted,
    _rewrite_nets,
    _merge_project_net_settings,
    _transform_item,
    parse_sexpr,
)


class PCBNetRewriteTests(unittest.TestCase):
    def test_named_pad_net_keeps_mapped_code_and_net_name(self):
        pad = parse_sexpr('(pad "1" smd rect (net 7 "SW"))')
        _rewrite_nets(pad, {7: (2, "VIN_BOARD")}, include_net_name=True)
        self.assertEqual(_dump(pad), '(pad "1" smd rect (net 2 "VIN_BOARD"))')
        self.assertIsInstance(pad[4][2], _Quoted)

    def test_track_net_remains_numeric_only(self):
        segment = parse_sexpr('(segment (start 1 2) (end 3 4) (net 7))')
        _rewrite_nets(segment, {7: (9, "X1_SW")}, include_net_name=False)
        self.assertEqual(
            _dump(segment),
            '(segment (start 1 2) (end 3 4) (net 9))',
        )

    def test_rotation_transforms_footprint_and_absolute_fields_but_not_local_pads(self):
        footprint = parse_sexpr(
            '(footprint "Test:Part" '
            '(at 100 100 0) '
            '(property "Reference" "R1" (at 100 98 0)) '
            '(pad "1" smd rect (at -0.75 0 0) (size 1 1) (net 0 "")))'
        )
        _transform_item(
            footprint,
            anchor_mm=(100.0, 100.0),
            position_mm=(120.0, 130.0),
            rotation_deg=90.0,
        )

        self.assertIn('(at 120 130 90)', _dump(footprint))
        self.assertIn('(at 118 130 90)', _dump(footprint))
        self.assertIn('(at -0.75 0 0)', _dump(footprint))

    def test_rotation_transforms_track_endpoints_about_layout_anchor(self):
        segment = parse_sexpr(
            '(segment (start 100 100) (end 105 100) (width 0.25) '
            '(layer "F.Cu") (net 3) (uuid "old"))'
        )
        _transform_item(
            segment,
            anchor_mm=(100.0, 100.0),
            position_mm=(120.0, 130.0),
            rotation_deg=90.0,
        )
        self.assertIn('(start 120 130)', _dump(segment))
        self.assertIn('(end 120 125)', _dump(segment))

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
                                {"name": "Default", "priority": 2147483647, "clearance": 0.2},
                                {"name": "Fast", "priority": 0, "clearance": 0.2, "track_width": 0.2},
                            ],
                            "netclass_assignments": {"VIN_BOARD": ["Fast"]},
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
                                {"name": "Default", "priority": 2147483647, "clearance": 0.2},
                                {"name": "Fast", "priority": 0, "clearance": 0.1, "track_width": 0.15},
                            ],
                            "netclass_assignments": {
                                "VIN": ["Fast"],
                                "SW": ["Fast"],
                            },
                            "netclass_patterns": [{"pattern": ".*", "netclass": "Fast"}],
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
            )

            self.assertEqual(result["merged_classes"], ["X1_Fast"])
            self.assertEqual(result["assigned_nets"], ["VIN_BOARD", "X1_SW"])
            self.assertTrue(result["ignored_patterns"])
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

    def test_unknown_net_code_is_rejected(self):
        segment = parse_sexpr('(segment (net 99))')
        with self.assertRaisesRegex(ValueError, "undefined source net code 99"):
            _rewrite_nets(segment, {7: (9, "X1_SW")}, include_net_name=False)


if __name__ == "__main__":
    unittest.main()
