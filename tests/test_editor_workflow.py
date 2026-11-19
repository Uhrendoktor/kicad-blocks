"""Exercise the plugin's library editor launch and publish workflow."""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_parameters, parse_pins
from kicad_blocks.repository import create_block, read_manifest


class LibraryEditorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "shared-library"
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.block = create_block(
            self.library,
            "Editable Library Block",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
        )
        self.schematic = SimpleNamespace(get_selection=list)
        self.parent = object()
        self.kicad = SimpleNamespace(
            get_kicad_binary_path=Mock(return_value="/usr/bin/eeschema"),
            reload_library=Mock(),
            wait_for_libraries=Mock(),
        )
        self.messagebox = SimpleNamespace(showinfo=Mock())
        self._dependency_patch = patch.object(
            plugin_actions,
            "synchronize_block_dependencies",
            side_effect=lambda record, *_args, **_kwargs: {
                "block": record, "changed": False, "count": 0
            },
        )
        self._dependency_patch.start()
        self._cli_patch = patch.object(
            plugin_actions, "find_kicad_cli", return_value="/usr/bin/kicad-cli"
        )
        self._cli_patch.start()

    def tearDown(self):
        self._cli_patch.stop()
        self._dependency_patch.stop()
        self.temp.cleanup()

    def test_place_block_uses_page_center_ipc_focus_selection_and_save(self):
        instance_id = SimpleNamespace(value="block-instance-id")
        instance = SimpleNamespace(id=instance_id)
        target = SimpleNamespace(
            clear_selection=Mock(),
            add_to_selection=Mock(),
            get_selection=Mock(return_value=[instance]),
            save=Mock(),
        )
        # This test models the current documented Schematic API: direct
        # page-centre placement, without the proposed transaction/move methods.
        schematic = SimpleNamespace(
            place_symbol_from_library=Mock(return_value=instance),
        )
        kicad = SimpleNamespace(get_schematic=Mock(return_value=schematic))
        with (
            patch.object(
                plugin_actions, "_schematic_page_center_mm", return_value=(100.0, 75.0)
            ),
            patch.object(
                plugin_actions, "_focus_schematic_items_in_editor", return_value=target
            ) as focus_items,
        ):
            placed = plugin_actions._start_native_symbol_placement(
                kicad, None, self.block.manifest["symbol_name"]
            )

        placed_instance, cursor_attached = placed
        self.assertIs(placed_instance, instance)
        self.assertFalse(cursor_attached)
        args, kwargs = schematic.place_symbol_from_library.call_args
        self.assertEqual(
            args[0],
            f"{plugin_actions.LIBRARY_NICKNAME}:{self.block.manifest['symbol_name']}",
        )
        self.assertAlmostEqual(args[1].x, 100_000_000)
        self.assertAlmostEqual(args[1].y, 75_000_000)
        self.assertFalse(kwargs)
        self.assertFalse(hasattr(schematic, "begin_commit"))
        self.assertFalse(hasattr(schematic, "interactive_move"))
        focus_items.assert_called_once_with(schematic, [instance])
        target.clear_selection.assert_called_once_with()
        target.add_to_selection.assert_called_once_with([instance])
        target.save.assert_called_once_with()

    def test_place_block_starts_native_cursor_move_when_schematic_api_exists(self):
        instance = SimpleNamespace(id=SimpleNamespace(value="block-instance-id"))
        commit = object()
        move = Mock()
        schematic = SimpleNamespace(
            begin_commit=Mock(return_value=commit),
            place_symbol_from_library=Mock(return_value=instance),
            interactive_move=move,
            push_commit=Mock(),
            drop_commit=Mock(),
            save=Mock(),
        )
        kicad = SimpleNamespace(get_schematic=Mock(return_value=schematic))
        with patch.object(
            plugin_actions, "_schematic_page_center_mm", return_value=(100.0, 75.0)
        ):
            placed = plugin_actions._start_native_symbol_placement(
                kicad, None, self.block.manifest["symbol_name"]
            )

        placed_instance, cursor_attached = placed
        self.assertIs(placed_instance, instance)
        self.assertTrue(cursor_attached)
        move.assert_called_once_with(instance.id)
        schematic.begin_commit.assert_called_once_with()
        schematic.push_commit.assert_not_called()
        schematic.drop_commit.assert_not_called()
        schematic.save.assert_not_called()

    def test_place_block_falls_back_when_schematic_move_handler_is_unavailable(self):
        instance = SimpleNamespace(id=SimpleNamespace(value="block-instance-id"))
        commit = object()
        target = SimpleNamespace(
            clear_selection=Mock(),
            add_to_selection=Mock(),
            get_selection=Mock(return_value=[instance]),
            save=Mock(),
        )
        schematic = SimpleNamespace(
            begin_commit=Mock(return_value=commit),
            place_symbol_from_library=Mock(return_value=instance),
            interactive_move=Mock(side_effect=RuntimeError("unhandled InteractiveMoveItems")),
            push_commit=Mock(),
            drop_commit=Mock(),
        )
        kicad = SimpleNamespace(get_schematic=Mock(return_value=schematic))
        with (
            patch.object(
                plugin_actions, "_schematic_page_center_mm", return_value=(100.0, 75.0)
            ),
            patch.object(
                plugin_actions, "_focus_schematic_items_in_editor", return_value=target
            ) as focus_items,
        ):
            placed = plugin_actions._start_native_symbol_placement(
                kicad, None, self.block.manifest["symbol_name"]
            )

        placed_instance, cursor_attached = placed
        self.assertIs(placed_instance, instance)
        self.assertFalse(cursor_attached)
        schematic.interactive_move.assert_called_once_with(instance.id)
        schematic.push_commit.assert_called_once_with(
            commit, f"Place functional block {self.block.manifest['symbol_name']}"
        )
        schematic.drop_commit.assert_not_called()
        focus_items.assert_called_once_with(schematic, [instance])
        target.clear_selection.assert_called_once_with()
        target.add_to_selection.assert_called_once_with([instance])
        target.save.assert_called_once_with()

    def test_place_block_uses_supported_page_center_api_without_transaction_methods(self):
        instance = SimpleNamespace(id=SimpleNamespace(value="block-instance-id"))
        target = SimpleNamespace(
            clear_selection=Mock(),
            add_to_selection=Mock(),
            get_selection=Mock(return_value=[instance]),
            save=Mock(),
        )
        schematic = SimpleNamespace(
            place_symbol_from_library=Mock(return_value=instance),
        )
        kicad = SimpleNamespace(get_schematic=Mock(return_value=schematic))
        with (
            patch.object(
                plugin_actions, "_schematic_page_center_mm", return_value=(100.0, 75.0)
            ),
            patch.object(
                plugin_actions, "_focus_schematic_items_in_editor", return_value=target
            ) as focus_items,
        ):
            placed, cursor_attached = plugin_actions._start_native_symbol_placement(
                kicad, None, self.block.manifest["symbol_name"]
            )

        self.assertIs(placed, instance)
        self.assertFalse(cursor_attached)
        schematic.place_symbol_from_library.assert_called_once()
        focus_items.assert_called_once_with(schematic, [instance])
        target.clear_selection.assert_called_once_with()
        target.add_to_selection.assert_called_once_with([instance])
        target.save.assert_called_once_with()

    def test_place_block_does_not_mask_non_capability_interactive_move_errors(self):
        instance = SimpleNamespace(id=SimpleNamespace(value="block-instance-id"))
        commit = object()
        schematic = SimpleNamespace(
            begin_commit=Mock(return_value=commit),
            place_symbol_from_library=Mock(return_value=instance),
            interactive_move=Mock(side_effect=RuntimeError("editor is busy")),
            push_commit=Mock(),
            drop_commit=Mock(),
        )
        kicad = SimpleNamespace(get_schematic=Mock(return_value=schematic))
        with (
            patch.object(
                plugin_actions, "_schematic_page_center_mm", return_value=(100.0, 75.0)
            ),
            self.assertRaisesRegex(RuntimeError, "editor is busy"),
        ):
            plugin_actions._start_native_symbol_placement(
                kicad, None, self.block.manifest["symbol_name"]
            )
        schematic.drop_commit.assert_called_once_with(commit)
        schematic.push_commit.assert_not_called()

    def test_place_block_drops_transaction_when_symbol_creation_fails(self):
        commit = object()
        schematic = SimpleNamespace(
            begin_commit=Mock(return_value=commit),
            place_symbol_from_library=Mock(side_effect=RuntimeError("library is stale")),
            interactive_move=Mock(),
            push_commit=Mock(),
            drop_commit=Mock(),
        )
        kicad = SimpleNamespace(get_schematic=Mock(return_value=schematic))
        with (
            patch.object(
                plugin_actions, "_schematic_page_center_mm", return_value=(100.0, 75.0)
            ),
            self.assertRaisesRegex(RuntimeError, "library is stale"),
        ):
            plugin_actions._start_native_symbol_placement(
                kicad, None, self.block.manifest["symbol_name"]
            )
        schematic.drop_commit.assert_called_once_with(commit)

    def test_facade_geometry_edit_changes_only_selected_pin_in_project_catalog(self):
        import copy

        from kicad_blocks.pcb_layout import _all, _direct, _head, parse_sexpr
        from kicad_blocks.repository import (
            _named_symbol_node,
            build_project_symbol_catalog,
            sha256_file,
        )

        catalog_path = build_project_symbol_catalog(self.project, [self.library])
        original_asset = self.block.symbol_library.read_bytes()
        original_shared_catalog = (
            self.library / plugin_actions.LIBRARY_CATALOG_NAME
        ).read_bytes()
        tree_before = parse_sexpr(catalog_path.read_text(encoding="utf-8"))
        symbol_name = self.block.manifest["symbol_name"]
        symbol_before = _named_symbol_node(tree_before, symbol_name, catalog_path)
        pin_unit = next(
            child for child in symbol_before[1:]
            if _head(child) == "symbol"
            and str(child[1]).startswith(symbol_name + "_")
            and _all(child, "pin")
        )
        pin = _all(pin_unit, "pin")[0]
        pin_name = str(_direct(pin, "name")[1])
        pin_number = str(_direct(pin, "number")[1])
        old_at = copy.deepcopy(_direct(pin, "at"))
        old_length = copy.deepcopy(_direct(pin, "length"))
        expected_document = copy.deepcopy(tree_before)
        expected_symbol = _named_symbol_node(expected_document, symbol_name, catalog_path)
        expected_unit = next(
            child for child in expected_symbol[1:]
            if _head(child) == "symbol" and str(child[1]) == str(pin_unit[1])
        )
        expected_pin = next(
            item for item in _all(expected_unit, "pin")
            if str(_direct(item, "name")[1]) == pin_name
            and str(_direct(item, "number")[1]) == pin_number
        )
        _direct(expected_pin, "at")[1:4] = ["22.86", "17.78", "180"]
        _direct(expected_pin, "length")[1] = "5.08"

        updated = plugin_actions._apply_facade_pin_geometry(
            catalog_path,
            symbol_name,
            str(pin_unit[1]),
            pin_name,
            pin_number,
            x_mm=22.86,
            y_mm=17.78,
            angle_degrees=180,
            length_mm=5.08,
            expected_sha256=sha256_file(catalog_path),
        )

        self.assertEqual(
            parse_sexpr(catalog_path.read_text(encoding="utf-8")),
            expected_document,
        )
        self.assertEqual(updated["x"], "22.86")
        self.assertEqual(updated["y"], "17.78")
        self.assertEqual(updated["angle"], "180")
        self.assertEqual(updated["length"], "5.08")
        self.assertEqual(self.block.symbol_library.read_bytes(), original_asset)
        self.assertEqual(
            (self.library / plugin_actions.LIBRARY_CATALOG_NAME).read_bytes(),
            original_shared_catalog,
        )
        self.assertEqual(str(_direct(pin, "at")[1]), str(old_at[1]))
        self.assertEqual(str(_direct(pin, "length")[1]), str(old_length[1]))

    def test_facade_geometry_edit_rejects_concurrent_catalog_change(self):
        from kicad_blocks.pcb_layout import _all, _direct, _head, parse_sexpr
        from kicad_blocks.repository import (
            _named_symbol_node,
            build_project_symbol_catalog,
            sha256_file,
        )

        catalog_path = build_project_symbol_catalog(self.project, [self.library])
        symbol_name = self.block.manifest["symbol_name"]
        symbol_tree = parse_sexpr(catalog_path.read_text(encoding="utf-8"))
        symbol = _named_symbol_node(symbol_tree, symbol_name, catalog_path)
        unit = next(
            child for child in symbol[1:]
            if _head(child) == "symbol" and _all(child, "pin")
        )
        pin = _all(unit, "pin")[0]
        pin_name = str(_direct(pin, "name")[1])
        pin_number = str(_direct(pin, "number")[1])
        previous_hash = sha256_file(catalog_path)
        original = catalog_path.read_text(encoding="utf-8")
        catalog_path.write_text(original + "\n", encoding="utf-8")
        externally_changed = catalog_path.read_text(encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "changed outside the facade editor"):
            plugin_actions._apply_facade_pin_geometry(
                catalog_path,
                symbol_name,
                str(unit[1]),
                pin_name,
                pin_number,
                x_mm=25.4,
                y_mm=25.4,
                angle_degrees=0,
                length_mm=2.54,
                expected_sha256=previous_hash,
            )
        self.assertEqual(
            catalog_path.read_text(encoding="utf-8"),
            externally_changed,
        )

    def test_facade_geometry_edit_rejects_invalid_geometry_without_writing(self):
        from kicad_blocks.pcb_layout import _all, _direct, _head, parse_sexpr
        from kicad_blocks.repository import (
            _named_symbol_node,
            build_project_symbol_catalog,
            sha256_file,
        )

        catalog_path = build_project_symbol_catalog(self.project, [self.library])
        symbol_name = self.block.manifest["symbol_name"]
        tree = parse_sexpr(catalog_path.read_text(encoding="utf-8"))
        symbol = _named_symbol_node(tree, symbol_name, catalog_path)
        unit = next(
            child for child in symbol[1:]
            if _head(child) == "symbol" and _all(child, "pin")
        )
        pin = _all(unit, "pin")[0]
        before = catalog_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "angles must be"):
            plugin_actions._apply_facade_pin_geometry(
                catalog_path,
                symbol_name,
                str(unit[1]),
                str(_direct(pin, "name")[1]),
                str(_direct(pin, "number")[1]),
                x_mm=25.4,
                y_mm=25.4,
                angle_degrees=45,
                length_mm=2.54,
                expected_sha256=sha256_file(catalog_path),
            )
        self.assertEqual(catalog_path.read_bytes(), before)

    def test_ensure_project_facade_catalog_preserves_existing_unsaved_edits(self):
        from kicad_blocks.repository import (
            build_project_symbol_catalog,
            register_project_symbol_library,
        )

        project_catalog = build_project_symbol_catalog(self.project, [self.library])
        register_project_symbol_library(
            self.project, plugin_actions.LIBRARY_NICKNAME, project_catalog
        )
        original = project_catalog.read_text(encoding="utf-8")
        edited = original.replace(
            f'(property "Value" "{self.block.name}"',
            f'(property "Value" "{self.block.name} In Progress"',
            1,
        )
        self.assertNotEqual(original, edited)
        project_catalog.write_text(edited, encoding="utf-8")

        with patch.object(plugin_actions, "_reload_symbol_library") as reload_library:
            resolved = plugin_actions._ensure_project_facade_catalog(
                self.project, self.block, self.kicad
            )

        self.assertEqual(resolved, project_catalog.resolve())
        self.assertIn("In Progress", project_catalog.read_text(encoding="utf-8"))
        reload_library.assert_not_called()

    def test_create_block_from_selected_sheet_is_one_click_and_names_from_source(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")
        selection = '''(sheet
            (property "Sheetname" "Power Stage" (at 100 100 0) (effects (font (size 1 1))))
            (property "Sheetfile" "power_stage.kicad_sch" (at 100 100 0) (effects (font (size 1 1))))
            (uuid "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        )'''
        schematic = SimpleNamespace(
            get_selection_as_string=Mock(return_value=selection)
        )
        with (
            patch.object(plugin_actions, "create_block", return_value=self.block) as create,
            patch.object(plugin_actions, "_register_block", return_value=self.block) as register,
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent, "0x100"
            )

        create.assert_called_once_with(
            self.library,
            "Power Stage",
            source_schematic=source.resolve(),
            source_project_name=self.project.name,
            source_sheet_uuid="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            description="",
        )
        register.assert_called_once_with(
            self.project, self.block, self.kicad, [self.library.resolve()]
        )

    def test_create_block_chooses_collision_safe_name_without_prompting(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")
        selection = '''(sheet
            (property "Sheetname" "Power Stage" (at 100 100 0) (effects (font (size 1 1))))
            (property "Sheetfile" "power_stage.kicad_sch" (at 100 100 0) (effects (font (size 1 1))))
            (uuid "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        )'''
        schematic = SimpleNamespace(get_selection_as_string=Mock(return_value=selection))
        existing = [SimpleNamespace(name="Power Stage"), SimpleNamespace(name="Power Stage (2)")]
        with (
            patch.object(plugin_actions, "_list_library_records", return_value=existing),
            patch.object(plugin_actions, "create_block", return_value=self.block) as create,
            patch.object(plugin_actions, "_register_block", return_value=self.block),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )
        self.assertEqual(create.call_args.args[:2], (self.library, "Power Stage (3)"))


    def test_create_block_rolls_back_new_block_when_project_registration_fails(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")
        selected_sheet = SimpleNamespace(
            filename_field=SimpleNamespace(
                text=SimpleNamespace(value=source.name)
            ),
            id=SimpleNamespace(
                value="dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            ),
        )
        schematic = SimpleNamespace(get_selection=Mock(return_value=[selected_sheet]))
        created = create_block(
            self.library,
            "Registration Failure Block",
            parse_pins("VIN:input, GND:passive"),
        )
        catalog = self.project / ".kicad_blocks" / "KBlocks.kicad_sym"
        symbol_table = self.project / "sym-lib-table"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text("original catalog", encoding="utf-8")
        symbol_table.write_text("original table", encoding="utf-8")

        with (
            patch.object(plugin_actions, "_list_library_records", return_value=[]),
            patch.object(plugin_actions, "create_block", return_value=created),
            patch.object(
                plugin_actions,
                "_register_block",
                side_effect=ValueError("invalid project symbol table"),
            ),
            self.assertRaisesRegex(ValueError, "invalid project symbol table"),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )

        self.assertFalse(created.path.exists())
        self.assertTrue(self.block.path.is_dir())
        self.assertEqual(catalog.read_text(encoding="utf-8"), "original catalog")
        self.assertEqual(symbol_table.read_text(encoding="utf-8"), "original table")

    def test_create_block_prefers_typed_sheet_symbol_over_serialized_selection(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")
        selected_uuid = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        selected_sheet = SimpleNamespace(
            filename_field=SimpleNamespace(
                text=SimpleNamespace(value="power_stage.kicad_sch")
            ),
            name_field=SimpleNamespace(text=SimpleNamespace(value="Power Stage")),
            id=SimpleNamespace(value=selected_uuid),
        )
        schematic = SimpleNamespace(
            get_selection=Mock(return_value=[selected_sheet]),
            get_selection_as_string=Mock(
                side_effect=RuntimeError(
                    "Serialized selection is unavailable in this editor build."
                )
            ),
        )
        with (
            patch.object(plugin_actions, "_list_library_records", return_value=[]),
            patch.object(plugin_actions, "create_block", return_value=self.block) as create,
            patch.object(plugin_actions, "_register_block", return_value=self.block),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )

        create.assert_called_once_with(
            self.library,
            "Power Stage",
            source_schematic=source.resolve(),
            source_project_name=self.project.name,
            source_sheet_uuid=selected_uuid,
            description="",
        )
        schematic.get_selection_as_string.assert_not_called()

    def test_create_block_rejects_multiple_selected_items_without_falling_back(self):
        schematic = SimpleNamespace(
            get_selection=Mock(return_value=[object(), object()]),
            get_selection_as_string=Mock(return_value=""),
            get_hierarchy=Mock(),
        )
        with (
            patch.object(plugin_actions, "create_block") as create,
            self.assertRaisesRegex(ValueError, "exactly one hierarchical sheet symbol"),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )
        create.assert_not_called()
        schematic.get_hierarchy.assert_not_called()

    def test_create_block_rejects_non_sheet_selection_instead_of_using_open_sheet(self):
        current_sheet = self.project / "power_stage.kicad_sch"
        current_sheet.write_text("(kicad_sch)", encoding="utf-8")
        schematic = SimpleNamespace(
            get_selection=Mock(return_value=[SimpleNamespace(reference="R1")]),
            get_selection_as_string=Mock(return_value=""),
            get_hierarchy=Mock(),
        )
        with (
            patch.object(plugin_actions, "create_block") as create,
            self.assertRaisesRegex(ValueError, "current selection is not a hierarchical sheet symbol"),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )
        create.assert_not_called()
        schematic.get_hierarchy.assert_not_called()

    def test_create_block_normalizes_no_items_selected_ipc_error(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")

        class FakeSheetPath:
            def __init__(self, path):
                self.path = path

        current_sheet = SimpleNamespace(
            filename="power_stage.kicad_sch",
            name="Power Stage",
            path=FakeSheetPath(["root-id", "sheet-id"]),
            children=[],
        )
        root_sheet = SimpleNamespace(
            filename="project.kicad_sch",
            name="Project",
            path=FakeSheetPath(["root-id"]),
            children=[current_sheet],
        )
        schematic = SimpleNamespace(
            get_selection=Mock(side_effect=RuntimeError("No items are selected.")),
            get_selection_as_string=Mock(
                side_effect=RuntimeError("No items are selected.")
            ),
            get_hierarchy=Mock(return_value=SimpleNamespace(root=root_sheet)),
            document=SimpleNamespace(
                sheet_path=FakeSheetPath(["root-id", "sheet-id"])
            ),
        )
        with (
            patch.object(plugin_actions, "_list_library_records", return_value=[]),
            patch.object(plugin_actions, "create_block", return_value=self.block) as create,
            patch.object(plugin_actions, "_register_block", return_value=self.block),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )
        create.assert_called_once_with(
            self.library,
            "Power Stage",
            source_schematic=source.resolve(),
            source_project_name=self.project.name,
            source_sheet_uuid="sheet-id",
            description="",
        )

    def test_create_block_rejects_empty_sheetfile_before_creating_anything(self):
        selected_sheet = SimpleNamespace(
            filename_field=SimpleNamespace(text=SimpleNamespace(value="")),
            id=SimpleNamespace(value="dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        )
        schematic = SimpleNamespace(get_selection=Mock(return_value=[selected_sheet]))
        with (
            patch.object(plugin_actions, "create_block") as create,
            self.assertRaisesRegex(ValueError, "no Sheetfile property"),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )
        create.assert_not_called()

    def test_create_block_rejects_selected_sheet_without_uuid(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")
        selected_sheet = SimpleNamespace(
            filename_field=SimpleNamespace(
                text=SimpleNamespace(value=source.name)
            ),
            id=None,
        )
        schematic = SimpleNamespace(get_selection=Mock(return_value=[selected_sheet]))
        with (
            patch.object(plugin_actions, "create_block") as create,
            self.assertRaisesRegex(ValueError, "no UUID"),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )
        create.assert_not_called()

    def test_create_block_does_not_fallback_when_typed_selection_ipc_fails(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")
        schematic = SimpleNamespace(
            get_selection=Mock(
                side_effect=RuntimeError("KiCad selection API is unavailable")
            ),
            get_selection_as_string=Mock(return_value=""),
            get_hierarchy=Mock(),
        )
        with (
            patch.object(plugin_actions, "create_block") as create,
            self.assertRaisesRegex(RuntimeError, "selection API is unavailable"),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )
        create.assert_not_called()
        schematic.get_selection_as_string.assert_not_called()
        schematic.get_hierarchy.assert_not_called()

    def test_create_block_fails_before_dialog_when_selected_sheetfile_is_missing(self):
        selected_sheet = SimpleNamespace(
            filename_field=SimpleNamespace(text=SimpleNamespace(value="missing.kicad_sch")),
            id=SimpleNamespace(value="dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        )
        schematic = SimpleNamespace(get_selection=Mock(return_value=[selected_sheet]))
        with (
            patch.object(plugin_actions, "create_block") as create,
            self.assertRaisesRegex(FileNotFoundError, "active parent sheet"),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )
        create.assert_not_called()

    def test_create_block_without_selection_uses_the_current_hierarchical_sheet(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")

        class FakeSheetPath:
            def __init__(self, path):
                self.path = path

        current_sheet = SimpleNamespace(
            filename="power_stage.kicad_sch",
            name="Power Stage",
            path=FakeSheetPath(["root-id", "sheet-id"]),
            children=[],
        )
        root_sheet = SimpleNamespace(
            filename="project.kicad_sch",
            name="Project",
            path=FakeSheetPath(["root-id"]),
            children=[current_sheet],
        )
        schematic = SimpleNamespace(
            get_selection_as_string=Mock(return_value=""),
            get_hierarchy=Mock(return_value=SimpleNamespace(root=root_sheet)),
            document=SimpleNamespace(
                sheet_path=FakeSheetPath(["root-id", "sheet-id"])
            ),
        )
        with (
            patch.object(plugin_actions, "create_block", return_value=self.block) as create,
            patch.object(plugin_actions, "_register_block", return_value=self.block),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )

        create.assert_called_once_with(
            self.library,
            "Power Stage",
            source_schematic=source.resolve(),
            source_project_name=self.project.name,
            source_sheet_uuid="sheet-id",
            description="",
        )

    def test_reload_symbol_library_uses_gui_supported_reload_only(self):
        kicad = SimpleNamespace(
            reload_library=Mock(),
            wait_for_libraries=Mock(
                return_value=[SimpleNamespace(nickname="KBlocks", error_message=None)]
            ),
            load_all_libraries=Mock(side_effect=AssertionError("headless-only API called")),
        )

        self.assertTrue(plugin_actions._reload_symbol_library(kicad, "KBlocks"))
        kicad.reload_library.assert_called_once_with(
            type=1, scope=2, nicknames=["KBlocks"]
        )
        kicad.wait_for_libraries.assert_called_once()
        kicad.load_all_libraries.assert_not_called()


    def test_edit_opens_library_implementation_and_publish_advances_revision(self):
        initial_manifest = read_manifest(self.block.path)
        process = object()

        with (
            patch.object(plugin_actions, "messagebox", self.messagebox),
            patch.object(plugin_actions, "_register_implementation_catalog"),
            patch("subprocess.Popen", return_value=process) as popen,
        ):
            opened = plugin_actions._edit_block_implementation(
                self.library,
                self.schematic,
                self.kicad,
                self.project,
                self.parent,
            )

        self.assertIs(opened, process)
        popen.assert_called_once_with(
            ["/usr/bin/eeschema", str(self.block.implementation)],
            close_fds=True,
        )

        # Model the file saved by Eeschema after the user edits the library.
        original = self.block.implementation.read_text(encoding="utf-8")
        self.block.implementation.write_text(
            original + "\n; saved from Eeschema editor workflow\n",
            encoding="utf-8",
        )

        with patch.object(plugin_actions, "messagebox", self.messagebox):
            plugin_actions._publish_block(
                self.library,
                self.kicad,
                self.project,
                self.parent,
            )

        published_manifest = read_manifest(self.block.path)
        self.assertGreater(published_manifest["version"], initial_manifest["version"])
        self.assertNotEqual(published_manifest["revision"], initial_manifest["revision"])
        self.assertIn("implementation.kicad_sch", published_manifest["hashes"])
        self.kicad.reload_library.assert_called_once()
        self.kicad.wait_for_libraries.assert_called_once()
        self.messagebox.showinfo.assert_not_called()

    def test_instance_configuration_is_one_undoable_ipc_update(self):
        from google.protobuf.empty_pb2 import Empty
        from kipy.proto.common.commands import editor_commands_pb2

        first = SimpleNamespace(name="KB_PARAM_OUTPUT_VOLTAGE", text=SimpleNamespace(value="3.3"))
        second = SimpleNamespace(name="KB_LAYOUT", text=SimpleNamespace(value="standard"))
        instance = SimpleNamespace(
            reference="X1",
            user_fields=[first, second],
            proto=Empty(),
        )
        response = editor_commands_pb2.UpdateItemsResponse(status=1)
        response.updated_items.add().status.code = 1
        commit = object()
        send = Mock(return_value=response)
        document = editor_commands_pb2.UpdateItems().header.document
        schematic = SimpleNamespace(
            document=document,
            client=SimpleNamespace(send=send),
            begin_commit=Mock(return_value=commit),
            push_commit=Mock(),
            save=Mock(),
            drop_commit=Mock(),
        )

        plugin_actions._write_instance_configuration(
            schematic,
            instance,
            {"KB_PARAM_OUTPUT_VOLTAGE": "1.8", "KB_LAYOUT": "compact"},
        )

        self.assertEqual(first.text.value, "1.8")
        self.assertEqual(second.text.value, "compact")
        schematic.begin_commit.assert_called_once_with()
        self.assertIs(send.call_args.args[0].__class__, editor_commands_pb2.UpdateItems)
        self.assertEqual(len(send.call_args.args[0].items), 1)
        schematic.push_commit.assert_called_once_with(
            commit, "Configure KiCad block instance"
        )
        schematic.save.assert_called_once_with()
        schematic.drop_commit.assert_not_called()

    def test_instance_configuration_reports_item_update_status_and_rolls_back(self):
        from google.protobuf.empty_pb2 import Empty
        from kipy.proto.common.commands import editor_commands_pb2

        field = SimpleNamespace(name="KB_PARAM_OUTPUT_VOLTAGE", text=SimpleNamespace(value="3.3"))
        instance = SimpleNamespace(reference="X1", user_fields=[field], proto=Empty())
        response = editor_commands_pb2.UpdateItemsResponse(status=1)
        rejected = response.updated_items.add()
        rejected.status.code = 5
        rejected.status.error_message = "symbol item is immutable"
        commit = object()
        schematic = SimpleNamespace(
            document=editor_commands_pb2.UpdateItems().header.document,
            client=SimpleNamespace(send=Mock(return_value=response)),
            begin_commit=Mock(return_value=commit),
            push_commit=Mock(),
            save=Mock(),
            drop_commit=Mock(),
        )

        with self.assertRaisesRegex(RuntimeError, "symbol item is immutable"):
            plugin_actions._write_instance_configuration(
                schematic, instance, {"KB_PARAM_OUTPUT_VOLTAGE": "1.8"}
            )

        self.assertEqual(field.text.value, "3.3")
        schematic.drop_commit.assert_called_once_with(commit)
        schematic.push_commit.assert_not_called()
        schematic.save.assert_not_called()

    def test_instance_configuration_rejects_fields_missing_from_facade(self):
        instance = SimpleNamespace(reference="X1", user_fields=[])
        schematic = SimpleNamespace(begin_commit=Mock())

        with self.assertRaisesRegex(RuntimeError, "KB_VARIANT"):
            plugin_actions._write_instance_configuration(
                schematic, instance, {"KB_VARIANT": "3V3"}
            )

        schematic.begin_commit.assert_not_called()

    def test_parameter_values_validate_custom_instance_fields(self):
        block = create_block(
            self.library,
            "Typed Field Block",
            parse_pins("VIN:input"),
            parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true"),
        )
        instance = SimpleNamespace(
            user_fields=[
                SimpleNamespace(name="KB_PARAM_OUTPUT_VOLTAGE", text=SimpleNamespace(value="1.8")),
                SimpleNamespace(name="KB_PARAM_ENABLE", text=SimpleNamespace(value="no")),
            ]
        )
        self.assertEqual(
            plugin_actions._parameter_values_for_instance(instance, block),
            {"OUTPUT_VOLTAGE": "1.8", "ENABLE": "false"},
        )
        instance.user_fields[0].text.value = "not-a-number"
        with self.assertRaisesRegex(ValueError, "must be a number"):
            plugin_actions._parameter_values_for_instance(instance, block)


    def test_layout_selection_reads_instance_field(self):
        standard = self.project / "standard.kicad_pcb"
        low_profile = self.project / "low_profile.kicad_pcb"
        standard.write_text("(kicad_pcb)", encoding="utf-8")
        low_profile.write_text("(kicad_pcb)", encoding="utf-8")
        block = create_block(
            self.library,
            "Layout Alternatives",
            parse_pins("VIN:input"),
            layout_files=[
                {"name": "Standard", "path": standard},
                {"name": "Low profile", "path": low_profile},
            ],
        )
        field = SimpleNamespace(name="KB_LAYOUT", text=SimpleNamespace(value="low_profile"))
        instance = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000789",
            reference="X7",
            user_fields=[field],
        )

        selected = plugin_actions._choose_block_layout(
            block, self.parent, instance=instance
        )

        self.assertEqual(selected["id"], "low_profile")
        self.assertEqual(selected["file"], "layout_low_profile.kicad_pcb")

    def test_layout_selection_rejects_stale_instance_field(self):
        first = self.project / "first.kicad_pcb"
        second = self.project / "second.kicad_pcb"
        first.write_text("(kicad_pcb)", encoding="utf-8")
        second.write_text("(kicad_pcb)", encoding="utf-8")
        block = create_block(
            self.library,
            "Stale Layout Choice",
            parse_pins("VIN:input"),
            layout_files=[
                {"name": "First", "path": first},
                {"name": "Second", "path": second},
            ],
        )
        instance = SimpleNamespace(
            reference="X1",
            user_fields=[SimpleNamespace(name="KB_LAYOUT", text=SimpleNamespace(value="removed"))],
        )
        with self.assertRaisesRegex(ValueError, "selects unknown layout"):
            plugin_actions._choose_block_layout(block, self.parent, instance=instance)


    def test_open_generated_pcb_copy_launches_only_selected_output_file(self):
        board_copy = self.project / "project_with_block.kicad_pcb"
        board_copy.write_text("(kicad_pcb)", encoding="utf-8")
        executable = "/usr/bin/pcbnew-nightly"
        kicad = SimpleNamespace(get_kicad_binary_path=Mock(return_value=executable))
        process = object()

        with patch("subprocess.Popen", return_value=process) as popen:
            opened = plugin_actions._open_generated_pcb_copy(kicad, board_copy)

        self.assertIs(opened, process)
        popen.assert_called_once_with(
            [executable, str(board_copy.resolve())],
            close_fds=True,
        )
        kicad.get_kicad_binary_path.assert_called_once_with("pcbnew")

    def test_open_generated_pcb_copy_rejects_missing_output(self):
        kicad = SimpleNamespace(get_kicad_binary_path=Mock(return_value="/usr/bin/pcbnew"))
        missing = self.project / "missing.kicad_pcb"
        with self.assertRaisesRegex(FileNotFoundError, "Generated PCB board does not exist"):
            plugin_actions._open_generated_pcb_copy(kicad, missing)

    def test_local_implementation_edit_opens_project_copy_not_library_source(self):
        from kicad_blocks.repository import link_instance_to_local_copy, make_local_copy

        local = make_local_copy(self.block, self.project)
        instance_id = "00000000-0000-0000-0000-000000000123"
        link_instance_to_local_copy(self.project, instance_id, local)
        selected = SimpleNamespace(
            id=instance_id,
            definition=SimpleNamespace(id=object()),
        )
        schematic = SimpleNamespace(get_selection=lambda: [selected])
        process = object()

        with (
            patch.object(plugin_actions, "messagebox", self.messagebox),
            patch.object(plugin_actions, "_register_implementation_catalog"),
            patch("subprocess.Popen", return_value=process) as popen,
        ):
            opened = plugin_actions._edit_block_implementation(
                self.library,
                schematic,
                self.kicad,
                self.project,
                self.parent,
            )

        self.assertIs(opened, process)
        popen.assert_called_once_with(
            ["/usr/bin/eeschema", str(local.implementation)],
            close_fds=True,
        )
        self.assertNotEqual(local.implementation, self.block.implementation)


    def test_create_block_empty_selection_ipc_error_uses_active_sheet(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")

        class FakeSheetPath:
            def __init__(self, path):
                self.path = path

        current_sheet = SimpleNamespace(
            filename="power_stage.kicad_sch",
            name="Power Stage",
            path=FakeSheetPath(["root-id", "sheet-id"]),
            children=[],
        )
        root_sheet = SimpleNamespace(
            filename="project.kicad_sch",
            name="Project",
            path=FakeSheetPath(["root-id"]),
            children=[current_sheet],
        )
        schematic = SimpleNamespace(
            get_selection_as_string=Mock(
                side_effect=ValueError("KiCad returned error: the selection is empty.")
            ),
            get_selection=Mock(return_value=[]),
            get_hierarchy=Mock(return_value=SimpleNamespace(root=root_sheet)),
            document=SimpleNamespace(
                sheet_path=FakeSheetPath(["root-id", "sheet-id"])
            ),
        )
        with (
            patch.object(plugin_actions, "create_block", return_value=self.block) as create,
            patch.object(plugin_actions, "_register_block", return_value=self.block),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )

        create.assert_called_once_with(
            self.library,
            "Power Stage",
            source_schematic=source.resolve(),
            source_project_name=self.project.name,
            source_sheet_uuid="sheet-id",
            description="",
        )

    def test_create_block_uses_selected_sheet_symbol_relative_to_active_sheet(self):
        parent_folder = self.project / "subsystems"
        parent_folder.mkdir()
        active_source = parent_folder / "power_stage.kicad_sch"
        active_source.write_text("(kicad_sch)", encoding="utf-8")
        selected_source = parent_folder / "interfaces" / "uart.kicad_sch"
        selected_source.parent.mkdir()
        selected_source.write_text("(kicad_sch)", encoding="utf-8")
        selected_uuid = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

        class FakeSheetPath:
            def __init__(self, path):
                self.path = path

        current_sheet = SimpleNamespace(
            filename="subsystems/power_stage.kicad_sch",
            name="Power Stage",
            path=FakeSheetPath(["root-id", "sheet-id"]),
            children=[],
        )
        root_sheet = SimpleNamespace(
            filename="project.kicad_sch",
            name="Project",
            path=FakeSheetPath(["root-id"]),
            children=[current_sheet],
        )
        selected_sheet = SimpleNamespace(
            filename_field=SimpleNamespace(
                text=SimpleNamespace(value="interfaces/uart.kicad_sch")
            ),
            name_field=SimpleNamespace(text=SimpleNamespace(value="UART")),
            id=SimpleNamespace(value=selected_uuid),
        )
        schematic = SimpleNamespace(
            get_selection_as_string=Mock(return_value=""),
            get_selection=Mock(return_value=[selected_sheet]),
            get_hierarchy=Mock(return_value=SimpleNamespace(root=root_sheet)),
            document=SimpleNamespace(
                sheet_path=FakeSheetPath(["root-id", "sheet-id"])
            ),
        )
        with (
            patch.object(plugin_actions, "_list_library_records", return_value=[]),
            patch.object(plugin_actions, "create_block", return_value=self.block) as create,
            patch.object(plugin_actions, "_register_block", return_value=self.block),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )

        create.assert_called_once_with(
            self.library,
            "Uart",
            source_schematic=selected_source.resolve(),
            source_project_name=self.project.name,
            source_sheet_uuid=selected_uuid,
            description="",
        )

    def test_edit_facade_action_opens_the_portable_facade_layout_editor(self):
        root = SimpleNamespace(
            title=Mock(), withdraw=Mock(), destroy=Mock(), mainloop=Mock()
        )
        fake_tk = SimpleNamespace(Tk=Mock(return_value=root), TclError=RuntimeError)
        fake_label = SimpleNamespace(pack=Mock())
        fake_ttk = SimpleNamespace(Label=Mock(return_value=fake_label))
        kicad = SimpleNamespace(get_schematic=Mock(return_value=self.schematic))
        kicad_module = types.ModuleType("kipy")
        kicad_module.KiCad = Mock(return_value=kicad)
        project_catalog = self.project / ".kicad_blocks" / "KBlocks.kicad_sym"
        with (
            patch.object(plugin_actions, "tk", fake_tk),
            patch.object(plugin_actions, "ttk", fake_ttk),
            patch.object(plugin_actions, "messagebox", self.messagebox),
            patch.object(plugin_actions, "_load_tkinter"),
            patch.object(plugin_actions, "_configure_tk_theme"),
            patch.object(plugin_actions, "_attach_tk_window_to_editor"),
            patch.object(plugin_actions, "project_directory", return_value=self.project),
            patch.object(plugin_actions, "configured_library_roots", return_value=[self.library]),
            patch.object(plugin_actions, "default_library_root", return_value=self.library),
            patch.object(plugin_actions, "_list_library_records", return_value=[self.block]),
            patch.object(plugin_actions, "_pick_record", return_value=self.block) as pick_record,
            patch.object(plugin_actions, "_ensure_project_facade_catalog", return_value=project_catalog),
            patch.object(plugin_actions, "_edit_facade_symbol", return_value=True) as edit_facade,
            patch.dict(sys.modules, {"kipy": kicad_module}),
        ):
            plugin_actions.run_action("edit-facade")
        pick_record.assert_called_once_with([self.block], root, "Edit Facade")
        edit_facade.assert_called_once_with(
            self.block,
            kicad,
            project_catalog=project_catalog,
            parent=root,
        )
        self.assertEqual(
            [item.args[0] for item in root.title.call_args_list],
            ["KiCad Blocks", f"KiCad Blocks — Facade layout: {self.block.name}"],
        )
        root.mainloop.assert_called_once_with()
        root.withdraw.assert_not_called()
        root.destroy.assert_called_once_with()


    def test_create_block_keeps_owner_window_mapped(self):
        root = SimpleNamespace(title=Mock(), withdraw=Mock(), destroy=Mock())
        fake_tk = SimpleNamespace(Tk=Mock(return_value=root), TclError=RuntimeError)
        kicad = SimpleNamespace(get_schematic=Mock(return_value=self.schematic))
        kicad_module = types.ModuleType("kipy")
        kicad_module.KiCad = Mock(return_value=kicad)
        with (
            patch.object(plugin_actions, "tk", fake_tk),
            patch.object(plugin_actions, "messagebox", self.messagebox),
            patch.object(plugin_actions, "_load_tkinter"),
            patch.object(plugin_actions, "_configure_tk_theme"),
            patch.object(plugin_actions, "_attach_tk_window_to_editor") as attach_window,
            patch.object(plugin_actions, "project_directory", return_value=self.project),
            patch.object(plugin_actions, "configured_library_roots", return_value=[self.library]),
            patch.object(plugin_actions, "default_library_root", return_value=self.library),
            patch.object(plugin_actions, "_create_and_place") as create_from_sheet,
            patch.dict(sys.modules, {"kipy": kicad_module}),
        ):
            plugin_actions.run_action("create-block")
        attach_window.assert_called_once_with(root, None)
        kicad_module.KiCad.assert_called_once_with()
        create_from_sheet.assert_called_once_with(
            self.library, self.schematic, kicad, self.project, root, None
        )
        root.withdraw.assert_not_called()
        self.assertTrue(root._kicad_blocks_reuse_for_create)
        root.destroy.assert_called_once_with()

    def test_run_action_does_not_capture_native_windows_before_creating_tk(self):
        events = []
        root = Mock()
        fake_tk = SimpleNamespace(
            Tk=Mock(side_effect=lambda: events.append("tk.Tk") or root),
            TclError=Exception,
        )
        fake_kipy = types.ModuleType("kipy")
        kicad = SimpleNamespace(get_schematic=Mock(return_value=None))
        fake_kipy.KiCad = Mock(side_effect=lambda: events.append("kipy.KiCad") or kicad)
        with (
            patch.object(plugin_actions, "_load_tkinter", side_effect=lambda: events.append("load tkinter")),
            patch.object(plugin_actions, "tk", fake_tk),
            patch.object(plugin_actions, "_configure_tk_theme"),
            patch.object(plugin_actions, "_attach_tk_window_to_editor"),
            patch.object(plugin_actions, "_show_action_hub"),
            patch.object(plugin_actions, "messagebox", self.messagebox),
            patch.dict(sys.modules, {"kipy": fake_kipy}),
        ):
            plugin_actions.run_action("open-hub")
        self.assertEqual(events, ["load tkinter", "tk.Tk", "kipy.KiCad"])
        self.assertNotIn("_kicad_blocks_editor_window_id", root.__dict__)

if __name__ == "__main__":
    unittest.main()
