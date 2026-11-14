from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.extraction import selected_sheet_file, selected_sheet_instance_uuid
from kicad_blocks.repository import create_block, read_manifest, refresh_library_block
from kicad_blocks.sexpr import (
    dump_sexpr,
    parse_sexpr,
    sexpr_all,
    sexpr_direct,
    sexpr_head,
)


def _source_sheet(path: Path) -> Path:
    source = """(kicad_sch
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
    """
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
        fragment = """(sheet
            (at 100 100)
            (size 40 30)
            (property "Sheetname" "Power Stage" (at 100 100 0) (effects (font (size 1 1))))
            (property "Sheetfile" "power_stage.kicad_sch" (at 100 100 0) (effects (font (size 1 1))))
            (uuid "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        )"""
        self.assertEqual(
            selected_sheet_file(fragment, self.project),
            self.source.resolve(),
        )
        self.assertEqual(
            selected_sheet_instance_uuid(fragment),
            "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
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
        self.assertEqual(len(sexpr_all(implementation, "hierarchical_label")), 3)
        self.assertEqual(len(sexpr_all(implementation, "sheet")), 0)
        self.assertEqual(
            str(sexpr_direct(sexpr_direct(implementation, "sheet_instances"), "path")[1]),
            "/",
        )
        resistor = sexpr_all(implementation, "symbol")[0]
        instances = sexpr_direct(resistor, "instances")
        project = next(item for item in instances[1:] if sexpr_head(item) == "project")
        path = next(item for item in project[1:] if sexpr_head(item) == "path")
        self.assertEqual(str(path[1]), "/")
        self.assertEqual(str(sexpr_direct(path, "reference")[1]), "R7")
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
        ).replace("(shape input)", "(shape bidirectional)")
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

    def test_publish_preserves_symbol_editor_body_artwork_while_syncing_pins(self):
        record = create_block(
            self.root,
            "Power Stage",
            source_schematic=self.source,
            source_project_name="TestProject",
        )

        facade = parse_sexpr(record.symbol_library.read_text(encoding="utf-8"))
        symbol = sexpr_all(facade, "symbol")[0]
        graphic_unit = next(
            child for child in sexpr_all(symbol, "symbol") if str(child[1]).endswith("_0_1")
        )
        rectangle = sexpr_all(graphic_unit, "rectangle")[0]
        stroke = sexpr_direct(rectangle, "stroke")
        width = sexpr_direct(stroke, "width")
        width[1] = "0.500"
        record.symbol_library.write_text(dump_sexpr(facade) + "\n", encoding="utf-8")

        implementation = record.implementation.read_text(encoding="utf-8")
        implementation = implementation.replace(
            '(hierarchical_label "VIN"',
            '(hierarchical_label "VCC"',
        ).replace("(shape input)", "(shape bidirectional)")
        record.implementation.write_text(implementation, encoding="utf-8")

        refresh_library_block(record)

        updated_facade = record.symbol_library.read_text(encoding="utf-8")
        self.assertIn("(width 0.500)", updated_facade)
        block_asset = (record.path / record.manifest["symbol_file"]).read_text(encoding="utf-8")
        self.assertIn("(width 0.500)", block_asset)
        self.assertIn('(name "VCC"', updated_facade)
        self.assertIn("(pin bidirectional line", updated_facade)

    def test_publish_rejects_concurrent_catalog_and_block_facade_edits(self):
        record = create_block(
            self.root,
            "Power Stage",
            source_schematic=self.source,
            source_project_name="TestProject",
        )
        asset_path = record.path / record.manifest["symbol_file"]
        catalog_path = record.symbol_library

        def change_body_width(path: Path, value: str) -> None:
            tree = parse_sexpr(path.read_text(encoding="utf-8"))
            symbol = sexpr_all(tree, "symbol")[0]
            graphic_unit = next(
                child for child in sexpr_all(symbol, "symbol") if str(child[1]).endswith("_0_1")
            )
            rectangle = sexpr_all(graphic_unit, "rectangle")[0]
            sexpr_direct(sexpr_direct(rectangle, "stroke"), "width")[1] = value
            path.write_text(dump_sexpr(tree) + "\n", encoding="utf-8")

        change_body_width(asset_path, "0.300")
        change_body_width(catalog_path, "0.500")
        asset_before = asset_path.read_bytes()
        catalog_before = catalog_path.read_bytes()

        with self.assertRaisesRegex(RuntimeError, "Facade symbol conflict"):
            refresh_library_block(record)

        self.assertEqual(asset_path.read_bytes(), asset_before)
        self.assertEqual(catalog_path.read_bytes(), catalog_before)

    def test_publish_rejects_an_extracted_block_with_no_hierarchical_labels(self):
        record = create_block(
            self.root,
            "Power Stage",
            source_schematic=self.source,
            source_project_name="TestProject",
        )
        previous_facade = record.symbol_library.read_bytes()
        previous_pins = read_manifest(record.path)["pins"]

        implementation = parse_sexpr(record.implementation.read_text(encoding="utf-8"))
        implementation[:] = [
            child for child in implementation if sexpr_head(child) != "hierarchical_label"
        ]
        record.implementation.write_text(dump_sexpr(implementation) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "implementation has none"):
            refresh_library_block(record)

        self.assertEqual(record.symbol_library.read_bytes(), previous_facade)
        self.assertEqual(read_manifest(record.path)["pins"], previous_pins)

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

    def test_nested_hierarchical_sheet_tree_is_copied_and_kept_internal(self):
        root_sheet_uuid = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        leaf_sheet_uuid = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        root_source = self.source.read_text(encoding="utf-8").replace(
            "(sheet_instances",
            f'''(sheet
                (at 20 20) (size 10 10)
                (property "Sheetname" "Nested" (at 20 20 0) (effects (font (size 1 1))))
                (property "Sheetfile" "nested.kicad_sch" (at 20 20 0) (effects (font (size 1 1))))
                (uuid "{root_sheet_uuid}")
                (instances (project "OriginalProject"
                    (path "/root/sheet" (page "3"))))
            )
            (sheet_instances''',
        )
        self.source.write_text(root_source, encoding="utf-8")

        nested_source = self.project / "nested.kicad_sch"
        nested_source.write_text(
            f'''(kicad_sch
            (version 20250114)
            (generator "eeschema")
            (uuid "22222222-2222-4222-8222-222222222222")
            (paper "A4")
            (lib_symbols)
            (symbol
                (lib_id "Device:R")
                (at 20 20 0)
                (unit 1)
                (property "Reference" "R2" (at 20 20 0) (effects (font (size 1 1))))
                (property "Value" "22k" (at 20 20 0) (effects (font (size 1 1))))
                (instances (project "OriginalProject"
                    (path "/root/sheet/{root_sheet_uuid}"
                        (reference "R2") (unit 1))))
            )
            (hierarchical_label "INNER_SIGNAL"
                (shape input)
                (at 10 10 0)
                (effects (font (size 1.27 1.27)) (justify left bottom))
                (uuid "abababab-abab-4bab-8bab-abababababab")
            )
            (sheet
                (at 30 30) (size 10 10)
                (property "Sheetname" "Leaf" (at 30 30 0) (effects (font (size 1 1))))
                (property "Sheetfile" "leaf.kicad_sch" (at 30 30 0) (effects (font (size 1 1))))
                (uuid "{leaf_sheet_uuid}")
                (instances (project "OriginalProject"
                    (path "/root/sheet/{root_sheet_uuid}" (page "4"))))
            )
            (sheet_instances (path "/root/sheet/{root_sheet_uuid}" (page "3")))
        )''',
            encoding="utf-8",
        )

        leaf_source = self.project / "leaf.kicad_sch"
        leaf_source.write_text(
            f"""(kicad_sch
            (version 20250114)
            (generator "eeschema")
            (uuid "33333333-3333-4333-8333-333333333333")
            (paper "A4")
            (lib_symbols)
            (hierarchical_label "LEAF_SIGNAL"
                (shape output)
                (at 5 5 0)
                (effects (font (size 1.27 1.27)) (justify left bottom))
                (uuid "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd")
            )
            (sheet_instances
                (path "/root/sheet/{root_sheet_uuid}/{leaf_sheet_uuid}" (page "4"))
            )
        )""",
            encoding="utf-8",
        )

        original_root = self.source.read_bytes()
        original_nested = nested_source.read_bytes()
        original_leaf = leaf_source.read_bytes()
        record = create_block(
            self.root,
            "Nested Interface",
            source_schematic=self.source,
            source_project_name="TestProject",
        )

        manifest = read_manifest(record.path)
        nested_files = [
            name for name in manifest["files"] if name.startswith("implementation_sheet_")
        ]
        self.assertEqual(len(nested_files), 2)
        self.assertTrue(all((record.path / name).is_file() for name in nested_files))

        root_tree = parse_sexpr(record.implementation.read_text(encoding="utf-8"))
        root_sheets = [node for node in root_tree[1:] if sexpr_head(node) == "sheet"]
        self.assertEqual(len(root_sheets), 1)
        root_sheet = root_sheets[0]
        root_child_file = str(
            next(p for p in sexpr_all(root_sheet, "property") if str(p[1]) == "Sheetfile")[2]
        )
        root_child_uuid = str(sexpr_direct(root_sheet, "uuid")[1])
        self.assertTrue((record.path / root_child_file).is_file())
        self.assertEqual(
            str(sexpr_direct(sexpr_direct(root_tree, "sheet_instances"), "path")[1]), "/"
        )

        child_tree = parse_sexpr((record.path / root_child_file).read_text(encoding="utf-8"))
        child_sheets = [node for node in child_tree[1:] if sexpr_head(node) == "sheet"]
        self.assertEqual(len(child_sheets), 1)
        child_sheet = child_sheets[0]
        leaf_file = str(
            next(p for p in sexpr_all(child_sheet, "property") if str(p[1]) == "Sheetfile")[2]
        )
        leaf_uuid = str(sexpr_direct(child_sheet, "uuid")[1])
        self.assertTrue((record.path / leaf_file).is_file())
        self.assertEqual(
            str(sexpr_direct(sexpr_direct(child_tree, "sheet_instances"), "path")[1]),
            "/" + root_child_uuid,
        )
        leaf_tree = parse_sexpr((record.path / leaf_file).read_text(encoding="utf-8"))
        self.assertEqual(
            str(sexpr_direct(sexpr_direct(leaf_tree, "sheet_instances"), "path")[1]),
            "/" + root_child_uuid + "/" + leaf_uuid,
        )

        self.assertEqual(
            [pin["name"] for pin in manifest["pins"]],
            ["VIN", "GND", "VOUT"],
        )
        self.assertEqual(len(sexpr_all(child_tree, "hierarchical_label")), 1)
        self.assertEqual(len(sexpr_all(leaf_tree, "hierarchical_label")), 1)

        self.assertEqual(self.source.read_bytes(), original_root)
        self.assertEqual(nested_source.read_bytes(), original_nested)
        self.assertEqual(leaf_source.read_bytes(), original_leaf)


if __name__ == "__main__":
    unittest.main()
