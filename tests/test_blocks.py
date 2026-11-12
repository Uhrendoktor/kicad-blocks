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
    parse_parameters,
    parse_pins,
    render_symbol_library,
    resolve_parameter_values,
    rotate_label_spin_style,
    schematic_symbol_orientation,
    substitute_parameter_templates,
    transform_symbol_point_to_schematic,
)
from kicad_blocks.repository import (
    add_dependency,
    add_library_root,
    build_project_symbol_catalog,
    create_block,
    default_library_root,
    library_roots,
    link_instance_to_local_copy,
    list_blocks,
    local_copy_for_instance,
    make_local_copy,
    publish_local_copy,
    read_manifest,
    refresh_library_block,
    register_project_symbol_library,
    remove_library_root,
    resolve_dependency_graph,
    resolve_dependency_occurrences,
    revision_for_hashes,
    set_library_root,
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

    def test_multi_selection_extracts_all_symbol_instances(self):
        from types import SimpleNamespace

        from kicad_blocks.plugin import _selected_symbols

        first = SimpleNamespace(
            definition=SimpleNamespace(id=SimpleNamespace(library="KBlocks", name="Buck"))
        )
        second = SimpleNamespace(
            definition=SimpleNamespace(id=SimpleNamespace(library="KBlocks", name="Buck"))
        )
        schematic = SimpleNamespace(get_selection=lambda: [first, object(), second])

        self.assertEqual(_selected_symbols(schematic), [first, second])

    def test_plugin_manifest_has_valid_action_scope_contract(self):
        manifest = json.loads(
            (PLUGIN / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["runtime"]["type"], "python")
        self.assertTrue(manifest["actions"])
        expected_scopes = {
            "place-block": ["schematic"],
            "manage-library": ["schematic"],
            "create-block": ["schematic"],
            "open-implementation": ["schematic"],
            "configure-instance": ["schematic"],
            "make-local-copy": ["schematic"],
            "synchronize-local-copy": ["schematic"],
            "edit-facade": ["schematic"],
            "publish-block": ["schematic"],
            "recursive-bom": ["schematic"],
            "recursive-erc": ["schematic"],
            "validate-libraries": ["schematic"],
            "apply-pcb-layout": ["schematic"],
            "apply-multiple-pcb-layouts": ["schematic"],
            "nest-block": ["schematic"],
            "place-imported-layout": ["pcb"],
        }
        self.assertEqual(
            {action["identifier"] for action in manifest["actions"]},
            set(expected_scopes),
        )
        for action in manifest["actions"]:
            self.assertTrue(action["identifier"])
            self.assertTrue(action["entrypoint"])
            self.assertEqual(action["scopes"], expected_scopes[action["identifier"]])
            self.assertTrue((PLUGIN / action["entrypoint"]).is_file())
            arguments = action.get("args", [])
            if action["identifier"] == "place-imported-layout":
                self.assertEqual(arguments, [])
                self.assertEqual(action["entrypoint"], "place_imported_layout.py")
            else:
                self.assertEqual(arguments, [action["identifier"]])
                self.assertEqual(action["entrypoint"], "actions.py")
            for key in ("icons-light", "icons-dark"):
                for icon in action.get(key, []):
                    self.assertTrue((PLUGIN / icon).is_file(), icon)
        self.assertNotIn("manager", expected_scopes)

    def test_project_catalog_can_seed_selected_block_configuration(self):
        from kicad_blocks.repository import set_project_symbol_defaults

        with tempfile.TemporaryDirectory(prefix="kicad-block-placement-defaults-") as temp:
            root = Path(temp) / "library"
            project = Path(temp) / "project"
            root.mkdir()
            project.mkdir()
            block = create_block(
                root,
                "Placement Defaults Block",
                parse_pins("VIN:input, GND:passive"),
                parameters=[{"name": "MODE", "type": "string", "default": "normal"}],
            )
            catalog = build_project_symbol_catalog(project, [root])
            set_project_symbol_defaults(
                catalog,
                block.manifest["symbol_name"],
                {"KB_VARIANT": "industrial", "KB_PARAM_MODE": "fast"},
            )
            rendered = catalog.read_text(encoding="utf-8")
            self.assertIn('(property "KB_VARIANT" "industrial"', rendered)
            self.assertIn('(property "KB_PARAM_MODE" "fast"', rendered)
            self.assertIn('(property "KB_PARAM_MODE" "normal"', block.symbol_library.read_text(encoding="utf-8"))

    def test_registered_block_libraries_build_one_project_symbol_catalog(self):
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="kicad-block-multi-library-") as temp:
            root = Path(temp)
            settings = root / "settings.json"
            first_library = root / "library-one"
            second_library = root / "library-two"
            project = root / "project"
            first_library.mkdir()
            second_library.mkdir()
            project.mkdir()
            with patch("kicad_blocks.repository.settings_file", return_value=settings), patch.dict(
                os.environ, {"KICAD_BLOCKS_LIBRARY": ""}, clear=False
            ):
                first = create_block(
                    first_library,
                    "Library One Block",
                    parse_pins("VIN:input, GND:passive"),
                )
                second = create_block(
                    second_library,
                    "Library Two Block",
                    parse_pins("SDA:bidirectional, SCL:bidirectional"),
                )

                add_library_root(first_library, make_default=True)
                add_library_root(second_library)
                registered = library_roots()
                self.assertEqual(registered[:2], [first_library.resolve(), second_library.resolve()])
                self.assertEqual(default_library_root(), first_library.resolve())

                catalog = build_project_symbol_catalog(
                    project, [first_library, second_library]
                )
                catalog_text = catalog.read_text(encoding="utf-8")
                self.assertIn(first.manifest["symbol_name"], catalog_text)
                self.assertIn(second.manifest["symbol_name"], catalog_text)
                self.assertTrue(catalog.is_file())

                remove_library_root(second_library)
                self.assertNotIn(second_library.resolve(), library_roots())
                self.assertTrue(second_library.exists())
                self.assertEqual(default_library_root(), first_library.resolve())


    def test_selected_library_is_registered_even_when_environment_overrides_default(self):
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="kicad-block-library-env-") as temp:
            root = Path(temp)
            override = root / "environment-library"
            selected = root / "selected-library"
            override.mkdir()
            selected.mkdir()
            settings = root / "settings.json"
            with patch("kicad_blocks.repository.settings_file", return_value=settings), patch.dict(
                os.environ, {"KICAD_BLOCKS_LIBRARY": str(override)}
            ):
                self.assertEqual(set_library_root(selected), selected.resolve())
                self.assertEqual(default_library_root(), override.resolve())
                self.assertIn(selected.resolve(), library_roots())

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


    def test_parameter_templates_only_expand_inside_quoted_fields_and_escape_values(self):
        parameters = parse_parameters("PART=STM32, LABEL=3V3")
        source = '(property "Value" "{{PART}}" (at 0 0 0))\n'
        self.assertEqual(
            substitute_parameter_templates(source, parameters),
            '(property "Value" "STM32" (at 0 0 0))\n',
        )
        escaped = substitute_parameter_templates(
            '(property "Value" "{{PART}}" (at 0 0 0))',
            parameters,
            {"PART": 'part "A"\\rev'},
        )
        self.assertIn(r'part \"A\"\\rev', escaped)
        escaped_quote_source = r'(property "Value" "quoted \"label\" {{LABEL}}" (at 0 0 0))'
        self.assertEqual(
            substitute_parameter_templates(escaped_quote_source, parameters),
            r'(property "Value" "quoted \"label\" 3V3" (at 0 0 0))',
        )
        for source in (
            '(property "Value" {{PART}})',
            '; {{PART}}\n',
            '; "commented string {{PART}}\n',
            '{{PART}}',
        ):
            with self.subTest(source=source), self.assertRaisesRegex(
                ValueError, "inside quoted KiCad text fields"
            ):
                substitute_parameter_templates(source, parameters)


    def test_parse_typed_parameters_and_rejects_invalid_values(self):
        parameters = parse_parameters(
            "OUTPUT_VOLTAGE:number=3.3, CHANNELS:integer=4, "
            "ENABLE:boolean=yes, PART=STM32"
        )
        self.assertEqual(
            [(item["name"], item["type"], item["default"]) for item in parameters],
            [
                ("OUTPUT_VOLTAGE", "number", "3.3"),
                ("CHANNELS", "integer", "4"),
                ("ENABLE", "boolean", "true"),
                ("PART", "string", "STM32"),
            ],
        )
        self.assertEqual(
            resolve_parameter_values(parameters, {"OUTPUT_VOLTAGE": "1.8", "ENABLE": "no"}),
            {
                "OUTPUT_VOLTAGE": "1.8",
                "CHANNELS": "4",
                "ENABLE": "false",
                "PART": "STM32",
            },
        )
        for invalid in (
            "A:number=nan",
            "A:integer=1.2",
            "A:boolean=maybe",
            "bad-name:string=x",
            "A=1,a=2",
            "A=one\nline",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_parameters(invalid)

    def test_parameterized_symbol_library_creates_hidden_instance_fields(self):
        parameters = parse_parameters("OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true")
        content = render_symbol_library(
            "power_block", "Power Block", "block-id", parse_pins("VIN:input, GND"),
            parameters=parameters,
        )
        self.assertTrue(_balanced_sexpr(content))
        self.assertIn('(property "KB_PARAM_OUTPUT_VOLTAGE" "3.3"', content)
        self.assertIn('(property "KB_PARAM_ENABLE" "true"', content)
        self.assertEqual(content.count('(property "KB_PARAM_'), 2)
        self.assertIn('(effects (font (size 1.0 1.0)) hide)', content)


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
        self.assertIn('(hierarchical_label "VIN" (shape input)', implementation)
        self.assertIn('(hierarchical_label "GND" (shape passive)', implementation)
        self.assertIn('(hierarchical_label "VOUT" (shape output)', implementation)
        self.assertNotIn("(global_label ", implementation)
        self.assertNotIn("(sheet ", implementation)
        self.assertEqual(len(list_blocks(self.root)), 1)

    def test_create_block_stores_multiple_named_layouts_and_parameters(self):
        first_board = self.project / "standard.kicad_pcb"
        second_board = self.project / "low_profile.kicad_pcb"
        project_settings = self.project / "low_profile.kicad_pro"
        first_board.write_text("(kicad_pcb)\n", encoding="utf-8")
        second_board.write_text("(kicad_pcb (version 20250114))\n", encoding="utf-8")
        project_settings.write_text(json.dumps({"meta": {"version": 3}}), encoding="utf-8")
        parameters = parse_parameters("OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true")

        record = create_block(
            self.root,
            "Parameterized Power",
            self.pins,
            parameters=parameters,
            layout_files=[
                {
                    "name": "Standard",
                    "path": first_board,
                    "ports": {"VIN": "VIN", "GND": "GND", "VOUT": "VOUT"},
                },
                {
                    "name": "Low profile",
                    "path": second_board,
                    "project_path": project_settings,
                },
            ],
        )

        manifest = read_manifest(record.path)
        self.assertEqual(
            [(item["id"], item["name"]) for item in manifest["layouts"]],
            [("standard", "Standard"), ("low_profile", "Low profile")],
        )
        self.assertEqual(manifest["parameters"], parameters)
        self.assertEqual(manifest["default_layout"], "standard")
        self.assertIn('(property "KB_LAYOUT" "standard"', record.symbol_library.read_text(encoding="utf-8"))
        self.assertEqual(manifest["layout_file"], "layout_standard.kicad_pcb")
        self.assertEqual(
            (record.path / "layout_low_profile.kicad_pcb").read_text(encoding="utf-8"),
            "(kicad_pcb (version 20250114))\n",
        )
        self.assertTrue((record.path / "layout_low_profile.kicad_pro").is_file())
        self.assertIn("layout_standard.kicad_pcb", manifest["hashes"])
        self.assertIn("layout_low_profile.kicad_pro", manifest["hashes"])

    def test_create_block_rejects_incomplete_layout_port_mapping(self):
        source_board = self.project / "incomplete.kicad_pcb"
        source_board.write_text("(kicad_pcb)\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "map every block facade pin"):
            create_block(
                self.root,
                "Incomplete Layout Ports",
                self.pins,
                layout_files=[{
                    "name": "broken",
                    "path": source_board,
                    "ports": {"VIN": "VIN"},
                }],
            )

    def test_create_block_copies_optional_pcb_and_project_settings_assets(self):
        source_layout = self.project / "board.kicad_pcb"
        source_layout.write_text("(kicad_pcb)\n", encoding="utf-8")
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
            "(kicad_pcb)\n",
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

    def test_layout_configuration_is_pulled_into_local_copy(self):
        first_board = self.project / "sync_standard.kicad_pcb"
        second_board = self.project / "sync_low_profile.kicad_pcb"
        first_board.write_text("(kicad_pcb)\n", encoding="utf-8")
        second_board.write_text("(kicad_pcb)\n", encoding="utf-8")
        source = create_block(
            self.root,
            "Layout Sync",
            self.pins,
            layout_files=[
                {"name": "Standard", "path": first_board},
                {"name": "Low profile", "path": second_board},
            ],
        )
        local = make_local_copy(source, self.project)
        source_manifest = read_manifest(source.path)
        source_manifest["default_layout"] = "low_profile"
        source_manifest["layouts"][1]["name"] = "Low profile Rev B"
        (source.path / "manifest.json").write_text(
            json.dumps(source_manifest, indent=2) + "\n", encoding="utf-8"
        )

        result = synchronize_local_copy(local)

        self.assertTrue(result["layouts_updated"])
        local_manifest = read_manifest(local.path)
        self.assertEqual(local_manifest["default_layout"], "low_profile")
        self.assertEqual(local_manifest["layouts"][1]["name"], "Low profile Rev B")
        self.assertEqual(local_manifest["base_layouts"]["default_layout"], "low_profile")
        self.assertEqual(
            local_manifest["base_layouts"]["layouts"][1]["name"], "Low profile Rev B"
        )

    def test_layout_configuration_local_edits_publish_and_divergent_edits_conflict(self):
        first_board = self.project / "publish_standard.kicad_pcb"
        second_board = self.project / "publish_compact.kicad_pcb"
        first_board.write_text("(kicad_pcb)\n", encoding="utf-8")
        second_board.write_text("(kicad_pcb)\n", encoding="utf-8")
        source = create_block(
            self.root,
            "Layout Publish",
            self.pins,
            layout_files=[
                {"name": "Standard", "path": first_board},
                {"name": "Compact", "path": second_board},
            ],
        )
        local = make_local_copy(source, self.project)
        local_manifest = read_manifest(local.path)
        local_manifest["default_layout"] = "compact"
        local_manifest["layouts"][1]["name"] = "Compact Local"
        (local.path / "manifest.json").write_text(
            json.dumps(local_manifest, indent=2) + "\n", encoding="utf-8"
        )

        result = publish_local_copy(local)

        self.assertEqual(result["published"], ["manifest.layouts"])
        source_manifest = read_manifest(source.path)
        self.assertEqual(source_manifest["default_layout"], "compact")
        self.assertEqual(source_manifest["layouts"][1]["name"], "Compact Local")
        self.assertTrue(result["layouts_updated"])
        self.assertEqual(
            read_manifest(local.path)["base_layouts"]["layouts"][1]["name"],
            "Compact Local",
        )

        local_manifest = read_manifest(local.path)
        source_manifest = read_manifest(source.path)
        local_manifest["layouts"][1]["name"] = "Local Divergence"
        source_manifest["layouts"][1]["name"] = "Library Divergence"
        (local.path / "manifest.json").write_text(
            json.dumps(local_manifest, indent=2) + "\n", encoding="utf-8"
        )
        (source.path / "manifest.json").write_text(
            json.dumps(source_manifest, indent=2) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "manifest.layouts"):
            publish_local_copy(local)
        self.assertEqual(
            read_manifest(source.path)["layouts"][1]["name"], "Library Divergence"
        )


    def test_parameter_schema_is_pulled_into_local_copy(self):
        source = create_block(
            self.root,
            "Parameterized Sync",
            self.pins,
            parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3"),
        )
        local = make_local_copy(source, self.project)
        source_manifest = read_manifest(source.path)
        source_manifest["parameters"][0]["default"] = "1.8"
        (source.path / "manifest.json").write_text(
            json.dumps(source_manifest, indent=2) + "\n", encoding="utf-8"
        )

        result = synchronize_local_copy(local)

        self.assertEqual(result["conflicts"], [])
        local_manifest = read_manifest(local.path)
        self.assertEqual(local_manifest["parameters"][0]["default"], "1.8")
        self.assertEqual(local_manifest["base_parameters"], local_manifest["parameters"])

    def test_parameter_schema_local_edits_publish_and_divergent_edits_conflict(self):
        source = create_block(
            self.root,
            "Parameterized Publish",
            self.pins,
            parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3"),
        )
        local = make_local_copy(source, self.project)
        local_manifest = read_manifest(local.path)
        local_manifest["parameters"][0]["default"] = "1.8"
        (local.path / "manifest.json").write_text(
            json.dumps(local_manifest, indent=2) + "\n", encoding="utf-8"
        )

        result = publish_local_copy(local)

        self.assertEqual(result["published"], ["manifest.parameters"])
        self.assertEqual(read_manifest(source.path)["parameters"][0]["default"], "1.8")
        self.assertEqual(
            read_manifest(local.path)["base_parameters"],
            read_manifest(source.path)["parameters"],
        )

        # Both sides now have a common baseline; divergent edits are rejected.
        local_manifest = read_manifest(local.path)
        source_manifest = read_manifest(source.path)
        local_manifest["parameters"][0]["default"] = "2.5"
        source_manifest["parameters"][0]["default"] = "5.0"
        (local.path / "manifest.json").write_text(
            json.dumps(local_manifest, indent=2) + "\n", encoding="utf-8"
        )
        (source.path / "manifest.json").write_text(
            json.dumps(source_manifest, indent=2) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "manifest.parameters"):
            publish_local_copy(local)
        self.assertEqual(read_manifest(source.path)["parameters"][0]["default"], "5.0")


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
