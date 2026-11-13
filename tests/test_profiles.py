"""Tests for named block profiles that bind variant, parameters and PCB layout."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import (
    parse_parameters,
    parse_pins,
    resolve_profile_configuration,
)
from kicad_blocks.plugin import _parse_profile_parameter_overrides
from kicad_blocks.repository import (
    create_block,
    make_local_copy,
    publish_local_copy,
    read_manifest,
    synchronize_local_copy,
)


class BlockProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "library"
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.sources = Path(self.temp.name) / "sources"
        self.sources.mkdir()

        self.variant_18 = self.sources / "implementation_1v8.kicad_sch"
        self.variant_33 = self.sources / "implementation_3v3.kicad_sch"
        self.variant_18.write_text(
            '(kicad_sch (version 20250114) (generator "test"))\n',
            encoding="utf-8",
        )
        self.variant_33.write_text(
            '(kicad_sch (version 20250114) (generator "test"))\n',
            encoding="utf-8",
        )
        self.standard = self.sources / "standard.kicad_pcb"
        self.compact = self.sources / "compact.kicad_pcb"
        self.standard.write_text("(kicad_pcb)\n", encoding="utf-8")
        self.compact.write_text("(kicad_pcb)\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def create_profile_block(self):
        return create_block(
            self.root,
            "Profiled Power Stage",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
            parameters=parse_parameters(
                "OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true, PHASES:integer=2"
            ),
            variant_files=[
                {"value": "1V8", "path": self.variant_18, "layout_id": "compact"},
                {"value": "3V3", "path": self.variant_33, "layout_id": "standard"},
            ],
            layout_files=[
                {"id": "standard", "name": "Standard", "path": self.standard},
                {"id": "compact", "name": "Compact", "path": self.compact},
            ],
            profiles=[
                {
                    "id": "standard_3v3",
                    "name": "Standard 3.3 V",
                    "description": "Default board and supply rail.",
                    "variant": "3V3",
                    "parameters": {"OUTPUT_VOLTAGE": "3.3", "ENABLE": "true"},
                },
                {
                    "id": "low_voltage_compact",
                    "name": "Low voltage compact",
                    "variant": "1V8",
                    "parameters": {"OUTPUT_VOLTAGE": "1.8", "PHASES": 1},
                },
            ],
        )

    def test_profile_resolves_linked_variant_parameters_and_layout(self):
        block = self.create_profile_block()
        manifest = read_manifest(block.path)

        self.assertEqual(
            resolve_profile_configuration(manifest, "LOW_VOLTAGE_COMPACT"),
            {
                "id": "low_voltage_compact",
                "variant": "1V8",
                "parameters": {
                    "OUTPUT_VOLTAGE": "1.8",
                    "ENABLE": "true",
                    "PHASES": "1",
                },
                "layout": "compact",
            },
        )
        standard_profile = resolve_profile_configuration(manifest, "standard_3v3")
        self.assertEqual(standard_profile["variant"], "3V3")
        self.assertEqual(standard_profile["layout"], "standard")

        facade = (block.path / manifest["symbol_file"]).read_text(encoding="utf-8")
        self.assertIn('property "KB_PROFILE" ""', facade)

    def test_profile_parameter_prompt_parser_normalizes_typed_overrides(self):
        parameters = parse_parameters(
            "OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true, PHASES:integer=2"
        )
        self.assertEqual(
            _parse_profile_parameter_overrides(
                "OUTPUT_VOLTAGE=1.8, ENABLE=no, PHASES=1", parameters
            ),
            {"OUTPUT_VOLTAGE": "1.8", "ENABLE": "false", "PHASES": "1"},
        )
        with self.assertRaisesRegex(ValueError, "Unknown profile parameter"):
            _parse_profile_parameter_overrides("MISSING=1", parameters)
        with self.assertRaisesRegex(ValueError, "entered more than once"):
            _parse_profile_parameter_overrides("PHASES=1, PHASES=2", parameters)

    def test_profile_definitions_are_validated_against_block_declarations(self):
        with self.assertRaisesRegex(ValueError, "declared implementation variant"):
            create_block(
                self.root,
                "Invalid Profile Variant",
                parse_pins("VIN:input"),
                variant_files=[{"value": "3V3", "path": self.variant_33}],
                profiles=[{"name": "Unknown rail", "variant": "5V"}],
            )

        with self.assertRaisesRegex(ValueError, "unknown parameter"):
            create_block(
                self.root,
                "Invalid Profile Parameter",
                parse_pins("VIN:input"),
                parameters=parse_parameters("VOUT:number=3.3"),
                profiles=[{"name": "Bad parameter", "parameters": {"MISSING": "1"}}],
            )

        with self.assertRaisesRegex(ValueError, "declared PCB layout"):
            create_block(
                self.root,
                "Invalid Profile Layout",
                parse_pins("VIN:input"),
                layout_files=[{"name": "Standard", "path": self.standard}],
                profiles=[{"name": "Bad layout", "layout": "not_real"}],
            )

    def test_profiles_round_trip_through_local_copy_sync_and_publish(self):
        source = self.create_profile_block()
        local = make_local_copy(source, self.project)

        source_manifest = read_manifest(source.path)
        source_manifest["profiles"][1]["description"] = "Upstream profile update."
        (source.path / "manifest.json").write_text(
            json.dumps(source_manifest, indent=2) + "\n",
            encoding="utf-8",
        )

        result = synchronize_local_copy(local)
        self.assertTrue(result["profiles_updated"])
        local_manifest = read_manifest(local.path)
        self.assertEqual(
            local_manifest["profiles"][1]["description"],
            "Upstream profile update.",
        )
        self.assertEqual(local_manifest["base_profiles"], local_manifest["profiles"])

        local_manifest["profiles"][1]["parameters"]["OUTPUT_VOLTAGE"] = "1.5"
        (local.path / "manifest.json").write_text(
            json.dumps(local_manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        result = publish_local_copy(local)
        self.assertIn("manifest.profiles", result["published"])
        self.assertEqual(
            read_manifest(source.path)["profiles"][1]["parameters"]["OUTPUT_VOLTAGE"],
            "1.5",
        )
        self.assertEqual(
            read_manifest(local.path)["base_profiles"],
            read_manifest(source.path)["profiles"],
        )

    def test_divergent_profile_changes_are_rejected_without_overwrite(self):
        source = self.create_profile_block()
        local = make_local_copy(source, self.project)

        local_manifest = read_manifest(local.path)
        source_manifest = read_manifest(source.path)
        local_manifest["profiles"][0]["name"] = "Local standard"
        source_manifest["profiles"][0]["name"] = "Library standard"
        (local.path / "manifest.json").write_text(
            json.dumps(local_manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        (source.path / "manifest.json").write_text(
            json.dumps(source_manifest, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "manifest.profiles"):
            publish_local_copy(local)

        self.assertEqual(
            read_manifest(source.path)["profiles"][0]["name"],
            "Library standard",
        )
        self.assertEqual(
            read_manifest(local.path)["profiles"][0]["name"],
            "Local standard",
        )


if __name__ == "__main__":
    unittest.main()
