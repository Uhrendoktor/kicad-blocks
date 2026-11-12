from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.extraction import selected_sheet_file
from kicad_blocks.pcb_layout import _all, _direct, _head, parse_sexpr
from kicad_blocks.repository import create_block, read_manifest, refresh_library_block


def _source_sheet(path: Path) -> Path:
    source = '''(kicad_sch
      (version 20250114)
      (generator "eeschema")
      (uuid "11111111-1111-4111-8111-111111111111")
      (paper "A4")
      (title_block (title "Power Stage"))
      (lib_symbols)
      (symbol
        (lib_id "Device:R")
        (at 50 50 0)
        (unit 1)
        (property "Reference" "R1" (at 50 50 0) (effects (font (size 1 1))))
        (property "Value" "10k" (at 50 50 0) (effects (font (size 1 1))))
        (instances
          (project "OriginalProject"
            (path "/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/11111111-2222-4333-8444-555555555555"
              (reference "R7")
              (unit 1)
            )
          )
        )
      )
      (hierarchical_label "VIN"
        (shape input)
        (at 40 40 0)
        (effects (font (size 1.27 1.27)) (justify left bottom))
        (uuid "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
      )
      (hierarchical_label "GND"
        (shape passive)
        (at 40 50 0)
        (effects (font (size 1.27 1.27)) (justify left bottom))
        (uuid "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
      )
      (hierarchical_label "VOUT"
        (shape output)
        (at 60 50 0)
        (effects (font (size 1.27 1.27)) (justify left bottom))
        (uuid "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
      )
      (sheet_instances (path "/root/sheet" (page "2")))
    )
    '''
    path.write_text(source, encoding="utf-8")
    return path


class HierarchicalSheetExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "library"
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.source = _source_sheet(self.project / "power_stage.kicad_sch")

    def tearDown(self):
        self.temp.cleanup()

    def test_selected_hierarchical_sheet_resolves_its_sheetfile(self):
        fragment = '''(sheet
            (at 100 100)
            (size 40 30)
            (property "Sheetname" "Power Stage" (at 100 100 0) (effects (font (size 1 1))))
            (property "Sheetfile" "power_stage.kicad_sch" (at 100 100 0) (effects (font (size 1 1))))
            (uuid "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        )'''
        self.assertEqual(
            selected_sheet_file(fragment, self.project),
            self.source.resolve(),
        )

    def test_create_block_derives_facade_pins_from_all_hierarchical_labels(self):
        source_before = self.source.read_bytes()
        record = create_block(
            self.root,
            "Power Stage",
            source_schematic=self.source,
            source_project_name="TestProject",
        )

        manifest = read_manifest(record.path)
        self.assertEqual(
            [(pin["name"], pin["electrical_type"]) for pin in manifest["pins"]],
            [("VIN", "input"), ("GND", "passive"), ("VOUT", "output")],
        )
        facade = record.symbol_library.read_text(encoding="utf-8")
        self.assertIn("(pin input line", facade)
        self.assertIn("(pin passive line", facade)
        self.assertIn("(pin output line", facade)

        implementation = parse_sexpr(record.implementation.read_text(encoding="utf-8"))
        self.assertEqual(len(_all(implementation, "hierarchical_label")), 3)
        self.assertEqual(len(_all(implementation, "sheet")), 0)
        self.assertEqual(
            str(_direct(_direct(implementation, "sheet_instances"), "path")[1]),
            "/",
        )
        resistor = _all(implementation, "symbol")[0]
        instances = _direct(resistor, "instances")
        project = next(item for item in instances[1:] if _head(item) == "project")
        path = next(item for item in project[1:] if _head(item) == "path")
        self.assertEqual(str(path[1]), "/")
        self.assertEqual(str(_direct(path, "reference")[1]), "R7")
        self.assertEqual(str(project[1]), "TestProject")

        # Extracting a block must not modify the original project subsheet.
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_publish_syncs_label_name_and_electrical_shape_to_facade(self):
        record = create_block(
            self.root,
            "Power Stage",
            source_schematic=self.source,
            source_project_name="TestProject",
        )
        original_library = record.symbol_library.read_text(encoding="utf-8")
        self.assertIn("(pin input line", original_library)

        implementation = record.implementation.read_text(encoding="utf-8")
        implementation = implementation.replace(
            '(hierarchical_label "VIN"',
            '(hierarchical_label "VCC"',
        ).replace('(shape input)', '(shape bidirectional)')
        record.implementation.write_text(implementation, encoding="utf-8")

        result = refresh_library_block(record)
        manifest = read_manifest(record.path)
        self.assertEqual(
            [(pin["name"], pin["electrical_type"]) for pin in manifest["pins"]],
            [("VCC", "bidirectional"), ("GND", "passive"), ("VOUT", "output")],
        )
        facade = record.symbol_library.read_text(encoding="utf-8")
        self.assertIn('(name "VCC"', facade)
        self.assertNotIn('(name "VIN"', facade)
        self.assertIn("(pin bidirectional line", facade)
        self.assertIn("block.kicad_sym", result["changed"])
        self.assertGreater(result["version"], 1)

    def test_conflicting_duplicate_hierarchical_label_shapes_are_rejected(self):
        implementation = self.source.read_text(encoding="utf-8").replace(
            '(hierarchical_label "GND"',
            '(hierarchical_label "VIN"',
        )
        self.source.write_text(implementation, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "conflicting shapes"):
            create_block(
                self.root,
                "Broken Interface",
                source_schematic=self.source,
                source_project_name="TestProject",
            )

    def test_nested_subsheets_are_rejected_with_actionable_message(self):
        implementation = self.source.read_text(encoding="utf-8").replace(
            "(sheet_instances",
            '''(sheet
                (at 20 20) (size 10 10)
                (property "Sheetname" "Nested" (at 20 20 0) (effects (font (size 1 1))))
                (property "Sheetfile" "nested.kicad_sch" (at 20 20 0) (effects (font (size 1 1))))
                (uuid "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
            )
            (sheet_instances''',
        )
        self.source.write_text(implementation, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "nested hierarchical sheets"):
            create_block(
                self.root,
                "Nested Interface",
                source_schematic=self.source,
                source_project_name="TestProject",
            )


if __name__ == "__main__":
    unittest.main()
