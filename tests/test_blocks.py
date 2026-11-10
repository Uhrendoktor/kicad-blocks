from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_pins, render_symbol_library
from kicad_blocks.repository import (
    add_dependency,
    create_block,
    list_blocks,
    make_local_copy,
    read_manifest,
    refresh_library_block,
    register_project_symbol_library,
    revision_for_hashes,
    sha256_file,
    synchronize_local_copy,
)


def _balanced_sexpr(text: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


class BlockModelTests(unittest.TestCase):
    def test_plugin_manifest_has_valid_schematic_action_contract(self):
        manifest = json.loads(
            (PLUGIN / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["runtime"]["type"], "python")
        self.assertTrue(manifest["actions"])
        for action in manifest["actions"]:
            self.assertTrue(action["identifier"])
            self.assertTrue(action["entrypoint"])
            self.assertIn("schematic", action["scopes"])
            self.assertTrue((PLUGIN / action["entrypoint"]).is_file())

    def test_parse_pins_aliases_and_default(self):
        pins = parse_pins("VIN:input, GND, SDA:bidir, ALERT:tristate")
        self.assertEqual(
            [(pin.name, pin.number, pin.electrical_type) for pin in pins],
            [
                ("VIN", "1", "input"),
                ("GND", "2", "passive"),
                ("SDA", "3", "bidirectional"),
                ("ALERT", "4", "tristate"),
            ],
        )

    def test_parse_pins_rejects_invalid_definitions(self):
        for bad in ("", "A:unknown", "VIN:input,vin:output", "bad name:input", "A:input,B:output,C:passive" + "," + ",".join(f"P{i}" for i in range(4, 66))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_pins(bad)

    def test_symbol_library_is_balanced_and_maps_pin_types(self):
        pins = parse_pins("VIN:input, GND:passive, VOUT:output, SDA:bidir")
        text = render_symbol_library("buck_converter", "Buck Converter", "block-id", pins)
        self.assertTrue(_balanced_sexpr(text))
        self.assertIn('(generator "kicad_blocks")', text)
        self.assertIn('(property "Block ID" "block-id"', text)
        self.assertIn('(pin input line', text)
        self.assertIn('(pin passive line', text)
        self.assertIn('(pin output line', text)
        self.assertIn('(pin bidirectional line', text)
        self.assertEqual(text.count("(pin "), len(pins))
        self.assertNotIn("hierarchical", text.casefold())


class BlockRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "library"
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.pins = parse_pins("VIN:input, GND:passive, VOUT:output")

    def tearDown(self):
        self.temp.cleanup()

    def test_api_library_identifier_uses_kipy_field_names(self):
        from types import SimpleNamespace

        from kicad_blocks.plugin import _symbol_identifier

        instance = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(library_nickname="KBlocks_A1B2C3D4", entry_name="buck")
            )
        )
        self.assertEqual(
            _symbol_identifier(instance), ("KBlocks_A1B2C3D4", "buck")
        )

    def test_create_block_writes_facade_and_separate_implementation(self):
        record = create_block(self.root, "Buck Converter", self.pins, description="Step-down")
        manifest = read_manifest(record.path)
        self.assertEqual(manifest["name"], "Buck Converter")
        self.assertEqual(manifest["pins"][0]["number"], "1")
        self.assertTrue(record.symbol_library.is_file())
        self.assertTrue(record.implementation.is_file())
        implementation = record.implementation.read_text(encoding="utf-8")
        self.assertIn('(global_label "VIN"', implementation)
        self.assertNotIn("(sheet ", implementation)
        self.assertNotIn("hierarchical_label", implementation)
        self.assertEqual(len(list_blocks(self.root)), 1)

    def test_create_block_copies_optional_pcb_asset(self):
        source_layout = self.project / "board.kicad_pcb"
        source_layout.write_text("(kicad_pcb)\n", encoding="utf-8")
        record = create_block(self.root, "With Layout", self.pins, layout_file=source_layout)
        self.assertEqual(record.manifest["layout_file"], "layout.kicad_pcb")
        self.assertEqual((record.path / "layout.kicad_pcb").read_text(), "(kicad_pcb)\n")

    def test_project_library_table_registration_is_idempotent(self):
        record = create_block(self.root, "Power Stage", self.pins)
        table = register_project_symbol_library(
            self.project, record.manifest["library_nickname"], record.symbol_library
        )
        first = table.read_text(encoding="utf-8")
        register_project_symbol_library(
            self.project, record.manifest["library_nickname"], record.symbol_library
        )
        self.assertEqual(table.read_text(encoding="utf-8"), first)
        self.assertIn(record.manifest["library_nickname"], first)
        self.assertTrue(_balanced_sexpr(first))

    def test_project_local_symbol_library_uses_portable_kipRJMOD_uri(self):
        source = create_block(self.root, "Portable Local", self.pins)
        local = make_local_copy(source, self.project)
        table = register_project_symbol_library(
            self.project, local.manifest["library_nickname"], local.symbol_library
        )
        content = table.read_text(encoding="utf-8")
        self.assertIn('${KIPRJMOD}/.kicad_blocks/local/', content)
        register_project_symbol_library(
            self.project, local.manifest["library_nickname"], local.symbol_library
        )
        self.assertEqual(table.read_text(encoding="utf-8"), content)

    def test_publish_bumps_version_when_library_assets_change(self):
        record = create_block(self.root, "Published Block", self.pins)
        original_version = record.manifest["version"]
        record.implementation.write_text(
            record.implementation.read_text(encoding="utf-8") + "\n; published edit\n",
            encoding="utf-8",
        )
        result = refresh_library_block(record)
        self.assertEqual(result["changed"], ["implementation.kicad_sch"])
        self.assertEqual(result["version"], original_version + 1)
        current = read_manifest(record.path)
        self.assertEqual(current["hashes"]["implementation.kicad_sch"], sha256_file(record.implementation))
        self.assertEqual(refresh_library_block(record)["changed"], [])

    def test_project_library_table_refuses_nickname_collision(self):
        a = create_block(self.root, "Block A", self.pins)
        b = create_block(self.root, "Block B", self.pins)
        nickname = a.manifest["library_nickname"]
        register_project_symbol_library(self.project, nickname, a.symbol_library)
        with self.assertRaises(ValueError):
            register_project_symbol_library(self.project, nickname, b.symbol_library)

    def test_local_copy_is_independent_and_syncs_unmodified_assets(self):
        source = create_block(self.root, "Power Stage", self.pins)
        local = make_local_copy(source, self.project)
        self.assertTrue(local.manifest["local_copy"])
        self.assertNotEqual(local.path, source.path)
        source.implementation.write_text(
            source.implementation.read_text(encoding="utf-8") + "\n; upstream change\n",
            encoding="utf-8",
        )
        result = synchronize_local_copy(local)
        self.assertEqual(result["copied"], ["implementation.kicad_sch"])
        self.assertIn("upstream change", local.implementation.read_text(encoding="utf-8"))
        synced = read_manifest(local.path)
        expected_hashes = {name: sha256_file(local.path / name) for name in synced["files"]}
        self.assertEqual(synced["revision"], revision_for_hashes(expected_hashes))

    def test_sync_preserves_local_edits_when_source_is_unchanged(self):
        source = create_block(self.root, "Power Stage", self.pins)
        local = make_local_copy(source, self.project)
        local.implementation.write_text(
            local.implementation.read_text(encoding="utf-8") + "\n; local edit\n",
            encoding="utf-8",
        )
        result = synchronize_local_copy(local)
        self.assertEqual(result["copied"], [])
        self.assertIn("local edit", local.implementation.read_text(encoding="utf-8"))

    def test_sync_refuses_conflicting_edits_without_overwrite(self):
        source = create_block(self.root, "Power Stage", self.pins)
        local = make_local_copy(source, self.project)
        local.implementation.write_text("; local implementation\n", encoding="utf-8")
        source.implementation.write_text("; upstream implementation\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Sync conflict"):
            synchronize_local_copy(local)
        self.assertEqual(local.implementation.read_text(encoding="utf-8"), "; local implementation\n")

    def test_sync_refuses_pin_interface_changes(self):
        source = create_block(self.root, "Power Stage", self.pins)
        local = make_local_copy(source, self.project)
        source.manifest["pins"].append(
            {"name": "FAULT", "number": "4", "electrical_type": "output"}
        )
        (source.path / "manifest.json").write_text(
            json.dumps(source.manifest, indent=2) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "pin interface changed"):
            synchronize_local_copy(local)

    def test_dependency_changes_are_pulled_into_an_unmodified_local_copy(self):
        source = create_block(self.root, "Controller", self.pins)
        child = create_block(self.root, "Power Stage", self.pins)
        local = make_local_copy(source, self.project)
        add_dependency(source, child)
        result = synchronize_local_copy(local)
        self.assertTrue(result["dependencies_updated"])
        refreshed = read_manifest(local.path)
        self.assertEqual(
            refreshed["dependencies"][0]["block_id"],
            child.manifest["block_id"],
        )

    def test_conflicting_dependency_edits_are_rejected(self):
        source = create_block(self.root, "Controller", self.pins)
        child_a = create_block(self.root, "Power A", self.pins)
        child_b = create_block(self.root, "Power B", self.pins)
        local = make_local_copy(source, self.project)
        add_dependency(source, child_a)
        local_manifest = read_manifest(local.path)
        local_manifest["dependencies"] = [{
            "block_id": child_b.manifest["block_id"],
            "name": child_b.name,
            "path": str(child_b.path),
            "revision": child_b.manifest["revision"],
        }]
        (local.path / "manifest.json").write_text(
            json.dumps(local_manifest, indent=2) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "manifest.dependencies"):
            synchronize_local_copy(type("Record", (), {"path": local.path, "manifest": local_manifest})())

    def test_nested_dependencies_are_recorded_and_cycles_rejected(self):
        parent = create_block(self.root, "Controller", self.pins)
        child = create_block(self.root, "Power Stage", self.pins)
        parent = add_dependency(parent, child)
        self.assertEqual(parent.manifest["dependencies"][0]["block_id"], child.manifest["block_id"])
        with self.assertRaisesRegex(ValueError, "cycle"):
            add_dependency(child, parent)


if __name__ == "__main__":
    unittest.main()
