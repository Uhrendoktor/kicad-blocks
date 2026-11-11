"""Focused tests for V1 block variant metadata and lookup."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_pins, resolve_variant_implementation
from kicad_blocks.plugin import _resolve_variant_text
from kicad_blocks.repository import (
    create_block,
    make_local_copy,
    publish_local_copy,
    synchronize_local_copy,
)


class _Project:
    def __init__(self, values):
        self.values = values

    def expand_text_variables(self, text):
        for name, value in self.values.items():
            text = text.replace("${" + name + "}", value)
        return text


class BlockVariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "library"
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.sources = Path(self.temp.name) / "sources"
        self.sources.mkdir()
        self.v18 = self.sources / "implementation_1v8.kicad_sch"
        self.v33 = self.sources / "implementation_3v3.kicad_sch"
        self.v18.write_text('(kicad_sch (version 20250114) (generator "test"))\n', encoding="utf-8")
        self.v33.write_text('(kicad_sch (version 20250114) (generator "test"))\n', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def create_variant_block(self):
        return create_block(
            self.root,
            "Variant Test",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
            variant_files=[
                {"value": "1V8", "path": self.v18},
                {"value": "3V3", "path": self.v33},
            ],
        )

    def test_variant_value_maps_to_copied_schematic(self):
        block = self.create_variant_block()
        self.assertEqual(resolve_variant_implementation(block.manifest, "3v3"), "implementation_3v3.kicad_sch")
        self.assertTrue((block.path / "implementation_3v3.kicad_sch").is_file())
        self.assertEqual({item["value"] for item in block.manifest["variants"]}, {"1V8", "3V3"})
        facade = (block.path / block.manifest["symbol_file"]).read_text(encoding="utf-8")
        self.assertIn('property "KB_VARIANT" ""', facade)

    def test_unknown_or_empty_variant_never_falls_back(self):
        block = self.create_variant_block()
        with self.assertRaisesRegex(ValueError, "Unknown KB_VARIANT"):
            resolve_variant_implementation(block.manifest, "5V")
        with self.assertRaisesRegex(ValueError, "KB_VARIANT is empty"):
            resolve_variant_implementation(block.manifest, "")

    def test_non_variant_block_keeps_single_implementation(self):
        block = create_block(self.root, "Single Implementation", parse_pins("VIN:input, GND:passive"))
        self.assertEqual(resolve_variant_implementation(block.manifest, ""), "implementation.kicad_sch")

    def test_duplicate_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate block variant value"):
            create_block(
                self.root,
                "Duplicate Variant",
                parse_pins("VIN:input"),
                variant_files=[
                    {"value": "3V3", "path": self.v18},
                    {"value": "3v3", "path": self.v33},
                ],
            )

    def test_project_and_sheet_text_variables_are_expanded(self):
        project = _Project({"RAIL": "1V8"})
        self.assertEqual(
            _resolve_variant_text("${RAIL}", project, {}),
            "1V8",
        )
        self.assertEqual(
            _resolve_variant_text("${SHEET_RAIL}", project, {"SHEET_RAIL": "${RAIL}"}),
            "1V8",
        )

    def test_unresolved_variable_is_reported(self):
        with self.assertRaisesRegex(ValueError, "Could not resolve KiCad text variable"):
            _resolve_variant_text("${MISSING}", _Project({}), {})

    def test_variants_sync_with_local_copy_and_publish_back(self):
        source = self.create_variant_block()
        local = make_local_copy(source, self.project)
        manifest_path = source.path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["variants"] = [manifest["variants"][1]]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        result = synchronize_local_copy(local)
        self.assertTrue(result["variants_updated"])
        synced = json.loads((local.path / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([item["value"] for item in synced["variants"]], ["3V3"])

        synced["variants"] = [
            {"value": "1V8", "file": "implementation_1v8.kicad_sch"},
            {"value": "3V3", "file": "implementation_3v3.kicad_sch"},
        ]
        (local.path / "manifest.json").write_text(json.dumps(synced, indent=2) + "\n", encoding="utf-8")
        result = publish_local_copy(local)
        self.assertIn("manifest.variants", result["published"])
        latest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual([item["value"] for item in latest["variants"]], ["1V8", "3V3"])


if __name__ == "__main__":
    unittest.main()
