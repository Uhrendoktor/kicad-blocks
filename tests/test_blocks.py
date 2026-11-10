from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import (
    facade_pin_connection_points,
    parse_pins,
    render_symbol_library,
    rotate_label_spin_style,
    schematic_symbol_orientation,
    transform_symbol_point_to_schematic,
)
from kicad_blocks.repository import (
    add_dependency,
    create_block,
    link_instance_to_local_copy,
    list_blocks,
    local_copy_for_instance,
    make_local_copy,
    publish_local_copy,
    read_manifest,
    refresh_library_block,
    register_project_symbol_library,
    resolve_dependency_graph,
    resolve_dependency_occurrences,
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
    def test_nested_symbol_pin_coordinates_transform_for_all_rotations(self):
        points = {
            0: (-7.62, -1.27),
            90: (-1.27, 7.62),
            180: (7.62, 1.27),
            270: (1.27, -7.62),
        }
        for rotation, expected in points.items():
            with self.subTest(rotation=rotation):
                self.assertEqual(
                    transform_symbol_point_to_schematic(-7.62, 1.27, rotation),
                    expected,
                )

    def test_nested_label_spin_styles_rotate_with_the_child_symbol(self):
        expected = {
            0: (1, 3),
            90: (4, 2),
            180: (3, 1),
            270: (2, 4),
        }
        for rotation, (left_pin, right_pin) in expected.items():
            with self.subTest(rotation=rotation):
                self.assertEqual(rotate_label_spin_style(1, rotation), left_pin)
                self.assertEqual(rotate_label_spin_style(3, rotation), right_pin)

    def test_nested_rotation_maps_to_kicad_ipc_orientation_enum(self):
        self.assertEqual(
            {angle: schematic_symbol_orientation(angle) for angle in (0, 90, 180, 270)},
            {0: 1, 90: 2, 180: 3, 270: 4},
        )
        with self.assertRaises(ValueError):
            schematic_symbol_orientation(45)

    def test_facade_connection_points_match_generated_symbol_pin_endpoints(self):
        pins = parse_pins("VIN:input, GND:passive, VOUT:output")
        points = facade_pin_connection_points(pins)

        # render_symbol_library places the electrical connection endpoints at
        # +/- (body half-width 5.08 mm + pin length 2.54 mm).
        self.assertEqual(points["VIN"][0], -7.62)
        self.assertEqual(points["GND"][0], -7.62)
        self.assertEqual(points["VOUT"][0], 7.62)
        self.assertEqual(points["VIN"][2], 1)   # SLSS_LEFT, extending outward
        self.assertEqual(points["VOUT"][2], 3)  # SLSS_RIGHT, extending outward

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

    def test_facade_identifier_uses_one_stable_library_nickname(self):
        from types import SimpleNamespace

        from kicad_blocks.plugin import _symbol_identifier

        instance = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(library="KBlocks", name="buck_1234abcd")
            )
        )
        self.assertEqual(_symbol_identifier(instance), ("KBlocks", "buck_1234abcd"))

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

    def test_create_block_copies_optional_pcb_and_project_settings_assets(self):
        source_layout = self.project / "board.kicad_pcb"
        source_layout.write_text("(kicad_pcb)\\n", encoding="utf-8")
        source_project = self.project / "board.kicad_pro"
        source_project.write_text(
            json.dumps(
                {
                    "meta": {"filename": "board.kicad_pro", "version": 3},
                    "net_settings": {"classes": [], "netclass_assignments": {}},
                }
            ),
            encoding="utf-8",
        )
        record = create_block(
            self.root,
            "With Layout Settings",
            self.pins,
            layout_file=source_layout,
            layout_project_file=source_project,
        )

        self.assertEqual(record.manifest["layout_file"], "layout.kicad_pcb")
        self.assertEqual(record.manifest["layout_project_file"], "layout.kicad_pro")
        self.assertEqual(
            (record.path / "layout.kicad_pcb").read_text(encoding="utf-8"),
            "(kicad_pcb)\\n",
        )
        self.assertEqual(
            json.loads((record.path / "layout.kicad_pro").read_text(encoding="utf-8"))["meta"]["filename"],
            "board.kicad_pro",
        )
        self.assertIn("layout.kicad_pro", record.manifest["files"])
        self.assertIn("layout.kicad_pro", record.manifest["hashes"])
        self.assertNotEqual(
            source_project.read_bytes(),
            b"",
        )

    def test_create_block_rejects_settings_without_layout(self):
        source_project = self.project / "board.kicad_pro"
        source_project.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "requires a PCB layout"):
            create_block(
                self.root,
                "Settings Without Layout",
                self.pins,
                layout_project_file=source_project,
            )

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

    def test_local_copy_reuses_facade_catalog_and_is_bound_by_instance_kiid(self):
        source = create_block(self.root, "Portable Local", self.pins)
        local = make_local_copy(source, self.project)

        self.assertEqual(local.manifest["library_nickname"], source.manifest["library_nickname"])
        self.assertEqual(local.manifest["symbol_name"], source.manifest["symbol_name"])
        self.assertEqual(local.symbol_library, source.symbol_library)

        table = register_project_symbol_library(
            self.project, source.manifest["library_nickname"], source.symbol_library
        )
        first = table.read_text(encoding="utf-8")
        self.assertIn("KBlocks", first)
        self.assertNotIn("KBLocal_", first)

        link_instance_to_local_copy(self.project, "instance-kiid-001", local)
        linked = local_copy_for_instance(self.project, "instance-kiid-001")
        self.assertIsNotNone(linked)
        self.assertEqual(linked.path, local.path)
        self.assertIsNone(local_copy_for_instance(self.project, "other-instance"))
        self.assertTrue((self.project / ".kicad_blocks" / "instances.json").is_file())

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

    def test_project_library_table_has_one_stable_catalog_row_for_all_blocks(self):
        a = create_block(self.root, "Block A", self.pins)
        b = create_block(self.root, "Block B", self.pins)
        self.assertEqual(a.manifest["library_nickname"], "KBlocks")
        self.assertEqual(b.manifest["library_nickname"], "KBlocks")
        self.assertEqual(a.symbol_library, b.symbol_library)

        table = register_project_symbol_library(self.project, "KBlocks", a.symbol_library)
        register_project_symbol_library(self.project, "KBlocks", b.symbol_library)
        content = table.read_text(encoding="utf-8")
        self.assertEqual(content.count('(name "KBlocks")'), 1)

        foreign_catalog = self.project / "foreign.kicad_sym"
        foreign_catalog.write_text(a.symbol_library.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(ValueError):
            register_project_symbol_library(self.project, "KBlocks", foreign_catalog)

    def test_shared_catalog_contains_facades_for_all_blocks(self):
        first = create_block(self.root, "Dual Buck", self.pins)
        second = create_block(self.root, "Dual Boost", self.pins)
        catalog = first.symbol_library.read_text(encoding="utf-8")
        self.assertIn(f'(symbol "{first.manifest["symbol_name"]}"', catalog)
        self.assertIn(f'(symbol "{second.manifest["symbol_name"]}"', catalog)
        self.assertNotEqual(first.manifest["symbol_name"], second.manifest["symbol_name"])

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

    def test_local_copy_can_publish_local_edit_to_library(self):
        source = create_block(self.root, "Publish Local Edit", self.pins)
        local = make_local_copy(source, self.project)
        original_version = source.manifest["version"]
        local.implementation.write_text(
            local.implementation.read_text(encoding="utf-8") + "\n; local publication\n",
            encoding="utf-8",
        )

        result = publish_local_copy(local)

        self.assertEqual(result["published"], ["implementation.kicad_sch"])
        self.assertGreater(result["version"], original_version)
        self.assertIn(
            "local publication",
            source.implementation.read_text(encoding="utf-8"),
        )
        source_manifest = read_manifest(source.path)
        local_manifest = read_manifest(local.path)
        self.assertEqual(local_manifest["source_revision"], source_manifest["revision"])
        self.assertEqual(local_manifest["base_hashes"], source_manifest["hashes"])
        self.assertEqual(publish_local_copy(local)["published"], [])

    def test_local_facade_publish_updates_shared_catalog(self):
        source = create_block(self.root, "Local Facade Edit", self.pins)
        local = make_local_copy(source, self.project)
        local_symbol_asset = local.path / local.manifest["symbol_file"]
        local_text = local_symbol_asset.read_text(encoding="utf-8")
        self.assertIn('(property "Datasheet" ""', local_text)
        local_text = local_text.replace(
            '(property "Datasheet" ""',
            '(property "Datasheet" "local-catalog-marker"',
            1,
        )
        local_symbol_asset.write_text(local_text, encoding="utf-8")

        result = publish_local_copy(local)

        self.assertEqual(result["published"], ["block.kicad_sym"])
        catalog_text = source.symbol_library.read_text(encoding="utf-8")
        self.assertIn("local-catalog-marker", catalog_text)
        source_text = (source.path / source.manifest["symbol_file"]).read_text(encoding="utf-8")
        self.assertIn("local-catalog-marker", source_text)
        self.assertEqual(
            read_manifest(local.path)["base_hashes"],
            read_manifest(source.path)["hashes"],
        )

    def test_local_sync_merges_independent_local_and_upstream_edits(self):
        source = create_block(self.root, "Bidirectional Merge", self.pins)
        local = make_local_copy(source, self.project)
        local.implementation.write_text(
            local.implementation.read_text(encoding="utf-8") + "\n; local side\n",
            encoding="utf-8",
        )
        source_symbol_asset = source.path / source.manifest["symbol_file"]
        source_symbol_asset.write_text(
            source_symbol_asset.read_text(encoding="utf-8") + "\n; upstream side\n",
            encoding="utf-8",
        )

        result = publish_local_copy(local)

        self.assertEqual(result["published"], ["implementation.kicad_sch"])
        self.assertEqual(result["pulled"], ["block.kicad_sym"])
        self.assertIn("; local side", source.implementation.read_text(encoding="utf-8"))
        self.assertIn("; local side", local.implementation.read_text(encoding="utf-8"))
        source_symbol_asset = source.path / source.manifest["symbol_file"]
        local_symbol_asset = local.path / local.manifest["symbol_file"]
        self.assertIn("; upstream side", source_symbol_asset.read_text(encoding="utf-8"))
        self.assertIn("; upstream side", local_symbol_asset.read_text(encoding="utf-8"))
        local_manifest = read_manifest(local.path)
        source_manifest = read_manifest(source.path)
        self.assertEqual(local_manifest["base_hashes"], source_manifest["hashes"])
        self.assertEqual(local_manifest["source_revision"], source_manifest["revision"])

    def test_identical_concurrent_local_and_upstream_edits_converge(self):
        source = create_block(self.root, "Converged Edit", self.pins)
        local = make_local_copy(source, self.project)
        identical = source.implementation.read_text(encoding="utf-8") + "\n; identical edit\n"
        local.implementation.write_text(identical, encoding="utf-8")
        source.implementation.write_text(identical, encoding="utf-8")
        original_version = source.manifest["version"]

        result = publish_local_copy(local)

        self.assertEqual(result["published"], [])
        self.assertEqual(result["pulled"], [])
        self.assertEqual(result["converged"], ["implementation.kicad_sch"])
        self.assertEqual(result["version"], original_version)
        self.assertEqual(read_manifest(local.path)["base_hashes"], read_manifest(source.path)["hashes"])

    def test_local_publish_rejects_divergent_library_edit_without_overwrite(self):
        source = create_block(self.root, "Publish Conflict", self.pins)
        local = make_local_copy(source, self.project)
        local.implementation.write_text("; local implementation\n", encoding="utf-8")
        source.implementation.write_text("; library implementation\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Synchronization conflict"):
            publish_local_copy(local)

        self.assertEqual(
            local.implementation.read_text(encoding="utf-8"),
            "; local implementation\n",
        )
        self.assertEqual(
            source.implementation.read_text(encoding="utf-8"),
            "; library implementation\n",
        )

    def test_local_publish_can_publish_nested_dependency_reference(self):
        source = create_block(self.root, "Publish Dependency Parent", self.pins)
        child = create_block(self.root, "Publish Dependency Child", self.pins)
        local = make_local_copy(source, self.project)
        local = add_dependency(local, child)

        result = publish_local_copy(local)

        self.assertEqual(result["published"], ["manifest.dependencies"])
        source_manifest = read_manifest(source.path)
        local_manifest = read_manifest(local.path)
        self.assertEqual(
            source_manifest["dependencies"][0]["block_id"],
            child.manifest["block_id"],
        )
        self.assertEqual(
            local_manifest["base_dependencies"],
            source_manifest["dependencies"],
        )

    def test_same_child_can_be_instantiated_twice_with_unique_occurrence_metadata(self):
        parent = create_block(self.root, "Dual Power Controller", self.pins)
        child = create_block(self.root, "Reusable Power Stage", self.pins)
        parent = add_dependency(
            parent, child, reference="B1", position_mm=(80.0, 100.0)
        )
        parent = add_dependency(
            parent,
            child,
            reference="B2",
            position_mm=(160.0, 100.0),
            pin_map={"VIN": "VIN_SW", "GND": "GND", "VOUT": "VOUT_A"},
        )

        occurrences = resolve_dependency_occurrences(parent, self.root)
        children = [item for item in occurrences if item.record.manifest["block_id"] == child.manifest["block_id"]]

        self.assertEqual(len(children), 2)
        self.assertEqual(
            [item.instance_path for item in children],
            [("B1",), ("B2",)],
        )
        parent_manifest = read_manifest(parent.path)
        dependencies = parent_manifest["dependencies"]
        self.assertNotEqual(dependencies[0]["instance_id"], dependencies[1]["instance_id"])
        self.assertEqual(dependencies[1]["pin_map"]["VIN"], "VIN_SW")

    def test_nested_instance_requires_complete_valid_pin_map(self):
        parent = create_block(self.root, "Mapped Parent", self.pins)
        child = create_block(self.root, "Mapped Child", self.pins)
        with self.assertRaisesRegex(ValueError, "map every child pin"):
            add_dependency(parent, child, pin_map={"VIN": "VIN_PARENT"})
        with self.assertRaisesRegex(ValueError, "Invalid parent net name"):
            add_dependency(parent, child, pin_map={"VIN": "bad net", "GND": "GND", "VOUT": "VOUT"})

    def test_nested_dependency_graph_resolves_after_library_folder_move(self):
        parent = create_block(self.root, "Portable Controller", self.pins)
        child = create_block(self.root, "Portable Power Stage", self.pins)
        parent = add_dependency(parent, child)
        self.assertFalse(Path(parent.manifest["dependencies"][0]["path"]).is_absolute())

        relocated_root = self.root.parent / "relocated-library"
        self.root.rename(relocated_root)
        relocated_parent = type(
            "Record",
            (),
            {
                "path": relocated_root / parent.path.name,
                "manifest": read_manifest(relocated_root / parent.path.name),
            },
        )()

        graph = resolve_dependency_graph(relocated_parent, relocated_root)

        self.assertEqual(
            [item.manifest["block_id"] for item in graph],
            [child.manifest["block_id"]],
        )

    def test_nested_symbol_library_table_uses_relative_path_and_can_retarget(self):
        parent = create_block(self.root, "Relative Table Parent", self.pins)
        child = create_block(self.root, "Relative Table Child", self.pins)
        first = register_project_symbol_library(
            parent.path,
            child.manifest["library_nickname"],
            child.symbol_library,
            prefer_relative=True,
        )
        content = first.read_text(encoding="utf-8")
        self.assertIn("${KIPRJMOD}/../", content)
        relocated_child = self.project / "different" / "relocated.kicad_sym"
        relocated_child.parent.mkdir(parents=True)
        relocated_child.write_text(child.symbol_library.read_text(encoding="utf-8"), encoding="utf-8")
        register_project_symbol_library(
            parent.path,
            child.manifest["library_nickname"],
            relocated_child,
            prefer_relative=True,
            replace_existing=True,
        )
        updated = first.read_text(encoding="utf-8")
        self.assertIn("../../project/different/relocated.kicad_sym", updated)
        self.assertNotIn(child.path.name + "/block.kicad_sym", updated)

    def test_nested_dependencies_are_recorded_and_cycles_rejected(self):
        parent = create_block(self.root, "Controller", self.pins)
        child = create_block(self.root, "Power Stage", self.pins)
        parent = add_dependency(parent, child)
        self.assertEqual(parent.manifest["dependencies"][0]["block_id"], child.manifest["block_id"])
        with self.assertRaisesRegex(ValueError, "cycle"):
            add_dependency(child, parent)


if __name__ == "__main__":
    unittest.main()
