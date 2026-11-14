"""Unit tests for the searchable block browser and native placement handoff."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import (
    parse_parameters,
    parse_pins,
    resolve_variant_implementation,
)
from kicad_blocks.plugin import (
    _attach_tk_window_to_editor,
    _bind_mousewheel,
    _block_overview_text,
    _choose_block_layout,
    _clean_facade_preview_svg,
    _filter_library_records,
    _layout_choices,
    _library_record_for_instance,
    _library_record_for_tree_row,
    _minimize_placement_windows,
    _native_symbol_preview_photo,
    _new_blank_pcb_source,
    _pick_directory,
    _pick_kicad_file,
    _pick_record,
    _pick_save_file,
    _preferred_library_selection,
    _restore_placement_windows,
    _save_block_layouts,
    _save_block_variants,
    _schematic_page_center_mm,
    _set_window_geometry,
    _start_native_symbol_placement,
    _system_prefers_dark_theme,
)
from kicad_blocks.repository import LIBRARY_NICKNAME, create_block, read_manifest


class LibraryBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "library"
        self.board = Path(self.temp.name) / "compact.kicad_pcb"
        self.board.write_text("(kicad_pcb)", encoding="utf-8")
        self.v18 = Path(self.temp.name) / "implementation_1v8.kicad_sch"
        self.v33 = Path(self.temp.name) / "implementation_3v3.kicad_sch"
        self.v18.write_text('(kicad_sch (version 20250114) (generator "test"))\n', encoding="utf-8")
        self.v33.write_text('(kicad_sch (version 20250114) (generator "test"))\n', encoding="utf-8")
        self.block = create_block(
            self.root,
            "Buck Converter",
            parse_pins("VIN:input, GND:passive, VOUT:output, EN:input"),
            description="Synchronous buck regulator with enable input",
            parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true"),
            variant_files=[
                {"value": "1V8", "path": self.v18},
                {"value": "3V3", "path": self.v33},
            ],
            layout_files=[{"name": "Compact", "path": self.board}],
        )
        self.filter_block = create_block(
            self.root,
            "USB Filter",
            parse_pins("VBUS:input, GND:passive, USB_P:bidirectional, USB_N:bidirectional"),
            description="USB input protection and common-mode filtering",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_search_matches_description_pins_and_variant_values(self):
        records = [self.block, self.filter_block]
        self.assertEqual(_filter_library_records(records, "buck")[0].name, "Buck Converter")
        self.assertEqual(_filter_library_records(records, "common-mode")[0].name, "USB Filter")
        self.assertEqual(_filter_library_records(records, "USB_P")[0].name, "USB Filter")
        self.assertEqual(_filter_library_records(records, "1v8")[0].name, "Buck Converter")
        self.assertEqual(_filter_library_records(records, "vout")[0].name, "Buck Converter")
        self.assertEqual(_filter_library_records(records, ""), records)

    def test_native_facade_preview_exports_from_the_project_catalog_override(self):
        symbol_name = self.block.manifest["symbol_name"]
        project_catalog = self.root / "project" / "KBlocks.kicad_sym"
        project_catalog.parent.mkdir(parents=True)
        project_catalog.write_text(
            self.block.symbol_library.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        calls = []

        def export(arguments, **kwargs):
            calls.append(arguments)
            output_dir = Path(arguments[arguments.index("--output") + 1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{symbol_name}.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><g><text>VIN</text></g></svg>',
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch("kicad_blocks.plugin.find_kicad_cli", return_value="/usr/bin/kicad-cli"),
            patch("kicad_blocks.plugin.subprocess.run", side_effect=export),
            patch(
                "kicad_blocks.plugin._clean_facade_preview_svg",
                side_effect=lambda svg_file, _record, _output: svg_file,
            ),
            patch("resvg_py.svg_to_bytes", return_value=b"not-a-png"),
            patch(
                "kicad_blocks.plugin.tk",
                SimpleNamespace(PhotoImage=Mock(return_value=object())),
            ),
            patch("kicad_blocks.plugin._NATIVE_PREVIEW_CACHE", {}),
        ):
            _native_symbol_preview_photo(
                self.block,
                library_file=project_catalog,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], str(project_catalog.resolve()))
        self.assertNotEqual(
            calls[0][-1],
            str(self.block.symbol_library.resolve()),
        )

    def test_native_save_dialog_uses_tk_initial_name_filters_and_suffix(self):
        save_dialog = Mock(return_value="/tmp/exported.csv")
        with patch(
            "kicad_blocks.plugin.filedialog",
            SimpleNamespace(asksaveasfilename=save_dialog),
        ):
            selected = _pick_save_file(
                object(),
                title="Save recursive BOM",
                initial_path=Path("/tmp") / "buck_bom.csv",
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            )

        self.assertEqual(selected, "/tmp/exported.csv")
        self.assertEqual(save_dialog.call_args.kwargs["initialdir"], "/tmp")
        self.assertEqual(save_dialog.call_args.kwargs["initialfile"], "buck_bom.csv")
        self.assertEqual(save_dialog.call_args.kwargs["defaultextension"], ".csv")
        self.assertEqual(
            save_dialog.call_args.kwargs["filetypes"],
            [("CSV", "*.csv"), ("All files", "*.*")],
        )

    def test_native_file_and_directory_dialog_cancel_returns_none(self):
        open_dialog = Mock(return_value="")
        directory_dialog = Mock(return_value="")
        save_dialog = Mock(return_value="")
        with patch(
            "kicad_blocks.plugin.filedialog",
            SimpleNamespace(
                askopenfilename=open_dialog,
                askdirectory=directory_dialog,
                asksaveasfilename=save_dialog,
            ),
        ):
            selected_file = _pick_kicad_file(
                object(), title="Open board", suffix=".kicad_pcb",
                initial_path=self.board,
            )
            selected_directory = _pick_directory(
                object(), title="Choose library", initial_path=self.root,
            )
            selected_save = _pick_save_file(
                object(), title="Save BOM",
                initial_path=Path("/tmp") / "bom.csv",
                defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            )
        self.assertIsNone(selected_file)
        self.assertIsNone(selected_directory)
        self.assertIsNone(selected_save)

    def test_native_facade_preview_removes_reference_and_component_name(self):
        from xml.etree import ElementTree as ET

        svg = self.root / "preview-source.svg"
        svg.write_text(
            """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 24">
              <g id="reference"><text>X?</text><desc>X?</desc><path d="M0 0"/></g>
              <g id="component-name"><text>Buck Converter</text></g>
              <g id="pins"><text>VIN</text><desc>VIN</desc></g>
            </svg>""",
            encoding="utf-8",
        )
        record = SimpleNamespace(
            manifest={
                "name": "Buck Converter",
                "symbol_name": "buck_converter",
                "reference_prefix": "X",
            }
        )

        cleaned = _clean_facade_preview_svg(svg, record, self.root)
        root = ET.parse(cleaned).getroot()
        labels = {
            (node.text or "").strip()
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] in {"text", "desc"}
        }
        self.assertNotIn("X?", labels)
        self.assertNotIn("Buck Converter", labels)
        self.assertIn("VIN", labels)
        self.assertEqual(root.attrib.get("width"), "40px")
        self.assertEqual(root.attrib.get("height"), "24px")

    def test_overview_contains_pin_types_parameters_variants_and_layouts(self):
        overview = _block_overview_text(self.block)
        self.assertIn("VIN", overview)
        self.assertIn("Input", overview)
        self.assertIn("OUTPUT_VOLTAGE  ·  number  ·  default: 3.3", overview)
        self.assertIn("1V8", overview)
        self.assertIn("3V3", overview)
        self.assertIn("Compact", overview)
        self.assertIn("Default · implementation.kicad_sch", overview)
        self.assertIn("Circuit implementations · 3", overview)
        self.assertNotIn("Purpose", overview)
        self.assertNotIn("Synchronous buck regulator", overview)
        self.assertNotIn("Version ·", overview)
        self.assertIn("Connections · 4", overview)
        self.assertNotIn("Block ID", overview)
        self.assertNotIn(str(self.block.manifest["block_id"]), overview)


    def test_new_pcb_layout_is_created_as_default_and_saved_in_block(self):
        source = _new_blank_pcb_source()
        try:
            _save_block_layouts(
                self.filter_block,
                [{"name": "Native", "source": str(source)}],
                default_layout_id=None,
            )
        finally:
            source.unlink(missing_ok=True)

        manifest = read_manifest(self.filter_block.path)
        self.assertEqual(manifest["default_layout"], "native")
        self.assertEqual(manifest["layouts"][0]["name"], "Native")
        self.assertEqual(manifest["layouts"][0]["file"], "layout_native.kicad_pcb")
        self.assertTrue(
            (self.filter_block.path / manifest["layouts"][0]["file"]).is_file()
        )
        self.assertEqual(manifest["layouts"][0]["implementations"], ["Default"])

    def test_each_pcb_layout_keeps_multiple_implementation_associations(self):
        layouts = [dict(item) for item in self.block.manifest["layouts"]]
        updated = _save_block_layouts(
            self.block,
            layouts,
            default_layout_id="compact",
            variant_layouts={"1V8": "compact"},
            layout_implementations={"compact": ["Default", "1V8"]},
        )
        manifest = read_manifest(updated.path)
        self.assertEqual(
            manifest["layouts"][0]["implementations"],
            ["Default", "1V8"],
        )
        self.assertEqual(
            next(item for item in manifest["variants"] if item["value"] == "1V8")["layout_id"],
            "compact",
        )

    def test_removing_all_pcb_layouts_clears_obsolete_variant_layout_ids(self):
        _save_block_layouts(
            self.block,
            _layout_choices(self.block),
            default_layout_id="compact",
            variant_layouts={"1V8": "compact"},
        )
        updated = _save_block_layouts(
            self.block,
            [],
            default_layout_id=None,
        )
        manifest = read_manifest(updated.path)
        self.assertEqual(manifest.get("layouts"), [])
        self.assertIsNone(manifest.get("default_layout"))
        self.assertTrue(
            all("layout_id" not in item for item in manifest.get("variants", []))
        )

    def test_native_file_and_directory_choosers_use_system_filedialog(self):
        open_dialog = Mock(return_value="/tmp/block.kicad_sch")
        directory_dialog = Mock(return_value="/tmp/library")
        with (
            patch("kicad_blocks.plugin.shutil.which", return_value=None),
            patch(
                "kicad_blocks.plugin.filedialog",
                SimpleNamespace(
                    askopenfilename=open_dialog,
                    askdirectory=directory_dialog,
                ),
            ),
        ):
            selected_file = _pick_kicad_file(
                object(),
                title="Choose implementation schematic",
                suffix=".kicad_sch",
                initial_path=str(self.v18),
            )
            selected_directory = _pick_directory(
                object(),
                title="Choose block library",
                initial_path=str(self.root),
            )
        self.assertEqual(selected_file, "/tmp/block.kicad_sch")
        self.assertEqual(selected_directory, "/tmp/library")
        self.assertEqual(open_dialog.call_args.kwargs["initialdir"], str(self.v18.parent))
        self.assertEqual(open_dialog.call_args.kwargs["initialfile"], self.v18.name)
        self.assertEqual(open_dialog.call_args.kwargs["parent"].__class__, object)
        self.assertTrue(directory_dialog.call_args.kwargs["mustexist"])
        self.assertEqual(directory_dialog.call_args.kwargs["initialdir"], str(self.root))

    def test_layout_linked_to_implementation_is_used_when_instance_has_default(self):
        original_layouts = [dict(item) for item in self.block.manifest["layouts"]]
        updated_block = _save_block_layouts(
            self.block,
            original_layouts,
            default_layout_id="compact",
            variant_layouts={"1V8": "compact"},
        )
        manifest = read_manifest(self.block.path)
        linked_variant = next(
            item for item in manifest["variants"] if item["value"] == "1V8"
        )
        self.assertEqual(linked_variant["layout_id"], "compact")

        instance = SimpleNamespace(
            reference="X1",
            user_fields=[
                SimpleNamespace(
                    name="KB_VARIANT",
                    text=SimpleNamespace(value="1V8"),
                ),
                SimpleNamespace(
                    name="KB_LAYOUT",
                    text=SimpleNamespace(value="compact"),
                ),
            ],
        )
        resolved = _choose_block_layout(updated_block, object(), instance=instance)
        self.assertEqual(resolved["id"], "compact")

    def test_implementation_link_supersedes_implicit_default_layout(self):
        original_layouts = [dict(item) for item in self.block.manifest["layouts"]]
        second_board = Path(self.temp.name) / "standard.kicad_pcb"
        second_board.write_text("(kicad_pcb)", encoding="utf-8")
        original_layouts.append({
            "id": "standard",
            "name": "Standard",
            "source": str(second_board),
        })
        updated_block = _save_block_layouts(
            self.block,
            original_layouts,
            default_layout_id="compact",
            variant_layouts={"1V8": "standard"},
        )
        instance = SimpleNamespace(
            reference="X1",
            user_fields=[
                SimpleNamespace(
                    name="KB_VARIANT",
                    text=SimpleNamespace(value="1V8"),
                ),
                SimpleNamespace(
                    name="KB_LAYOUT",
                    text=SimpleNamespace(value="compact"),
                ),
            ],
        )
        resolved = _choose_block_layout(updated_block, object(), instance=instance)
        self.assertEqual(resolved["id"], "standard")

    def test_manifest_rejects_variant_link_to_unknown_layout(self):
        manifest_path = self.block.path / "manifest.json"
        manifest = read_manifest(self.block.path)
        manifest["variants"][0]["layout_id"] = "missing"
        manifest_path.write_text(
            __import__("json").dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "links to unknown PCB layout"):
            read_manifest(self.block.path)

    def test_editing_implementation_variants_preserves_pcb_layout_links(self):
        layouts = _layout_choices(self.block)
        linked_block = _save_block_layouts(
            self.block,
            layouts,
            default_layout_id="compact",
            variant_layouts={"1V8": "compact"},
        )
        before = read_manifest(self.block.path)
        editor_values = [
            {"value": item["value"], "file": item["file"]}
            for item in before["variants"]
        ]

        # The implementation editor only edits selector/file data; it must not
        # silently discard PCB links configured in the separate layout manager.
        _save_block_variants(linked_block, editor_values)

        after = read_manifest(self.block.path)
        variant_1v8 = next(
            item for item in after["variants"] if item["value"] == "1V8"
        )
        self.assertEqual(variant_1v8["layout_id"], "compact")

        replacement_source = Path(self.temp.name) / "renamed_implementation.kicad_sch"
        replacement_source.write_text(
            '(kicad_sch (version 20250114) (generator "replacement"))\n',
            encoding="utf-8",
        )
        _save_block_variants(
            linked_block,
            [
                {
                    "value": "1V8 low-noise",
                    "source": str(replacement_source),
                    "layout_id": "compact",
                },
                *[
                    {"value": item["value"], "file": item["file"]}
                    for item in before["variants"]
                    if item["value"] != "1V8"
                ],
            ],
        )
        renamed = read_manifest(self.block.path)
        renamed_implementation = next(
            item for item in renamed["variants"]
            if item["value"] == "1V8 low-noise"
        )
        self.assertEqual(renamed_implementation["layout_id"], "compact")

    def test_default_implementation_is_persisted_and_used_for_empty_selector(self):
        _save_block_variants(
            self.block,
            [
                {"value": item["value"], "file": item["file"]}
                for item in self.block.manifest["variants"]
            ],
            default_variant="3V3",
        )
        manifest = read_manifest(self.block.path)
        self.assertEqual(manifest["default_variant"], "3V3")
        self.assertEqual(
            resolve_variant_implementation(manifest, ""),
            "implementation_3v3.kicad_sch",
        )
        self.assertEqual(
            resolve_variant_implementation(manifest, "1V8"),
            "implementation_1v8.kicad_sch",
        )

    def test_variant_metadata_can_be_added_edited_and_removed_from_library(self):
        initial_version = read_manifest(self.filter_block.path)["version"]

        _save_block_variants(
            self.filter_block,
            [{"value": "1V8", "source": str(self.v18)}],
        )
        added = read_manifest(self.filter_block.path)
        self.assertEqual(
            added["variants"],
            [{"value": "1V8", "file": "implementation_1v8.kicad_sch"}],
        )
        variant_file = self.filter_block.path / added["variants"][0]["file"]
        self.assertTrue(variant_file.is_file())
        self.assertGreater(added["version"], initial_version)

        _save_block_variants(
            self.filter_block,
            [{"value": "Low voltage", "file": added["variants"][0]["file"]}],
        )
        edited = read_manifest(self.filter_block.path)
        self.assertEqual(edited["variants"][0]["value"], "Low voltage")
        self.assertEqual(edited["variants"][0]["file"], added["variants"][0]["file"])

        _save_block_variants(self.filter_block, [])
        removed = read_manifest(self.filter_block.path)
        self.assertEqual(removed["variants"], [])
        self.assertFalse(variant_file.exists())

    def test_variant_can_be_edited_without_copying_its_file_onto_itself(self):
        _save_block_variants(
            self.filter_block,
            [{"value": "1V8", "source": str(self.v18)}],
        )
        manifest = read_manifest(self.filter_block.path)
        variant_file = self.filter_block.path / manifest["variants"][0]["file"]
        original_bytes = variant_file.read_bytes()

        _save_block_variants(
            self.filter_block,
            [{"value": "1V8", "source": str(variant_file)}],
        )

        self.assertEqual(variant_file.read_bytes(), original_bytes)
        self.assertEqual(
            read_manifest(self.filter_block.path)["variants"][0]["file"],
            variant_file.name,
        )

    def test_invalid_profile_change_does_not_copy_new_variant_files(self):
        profiled = create_block(
            self.root,
            "Profiled Variant Block",
            parse_pins("VIN:input, VOUT:output"),
            variant_files=[{"value": "1V8", "path": self.v18}],
            profiles=[{"name": "Low voltage", "variant": "1V8"}],
        )
        with self.assertRaisesRegex(ValueError, "still use an implementation"):
            _save_block_variants(
                profiled,
                [{"value": "3V3", "source": str(self.v33)}],
            )
        self.assertFalse(
            (profiled.path / "implementation_3v3.kicad_sch").exists()
        )
        self.assertEqual(
            read_manifest(profiled.path)["variants"][0]["value"],
            "1V8",
        )

    def test_variant_editor_refuses_to_remove_an_implementation_used_by_a_profile(self):
        profiled = create_block(
            self.root,
            "Profiled Variant Block",
            parse_pins("VIN:input, VOUT:output"),
            variant_files=[{"value": "1V8", "path": self.v18}],
            profiles=[{"name": "Low voltage", "variant": "1V8"}],
        )
        with self.assertRaisesRegex(ValueError, "still use an implementation"):
            _save_block_variants(profiled, [])

    def test_window_geometry_respects_display_bounds(self):
        class FakeWindow:
            def __init__(self):
                self.requested_geometry = None
                self.minimum = None

            def update_idletasks(self):
                pass

            def winfo_screenwidth(self):
                return 1366

            def winfo_screenheight(self):
                return 768

            def geometry(self, value):
                self.requested_geometry = value

            def minsize(self, width, height):
                self.minimum = (width, height)

        window = FakeWindow()
        _set_window_geometry(window, 1480, 980, 1160, 740)
        self.assertEqual(window.requested_geometry, "1286x668+40+50")
        self.assertEqual(window.minimum, (1160, 668))

    def test_tk_transient_uses_only_the_in_process_parent(self):
        class FakeWindow:
            def __init__(self, master=None):
                self.master = master
                self.transient_parent = None

            def transient(self, parent):
                self.transient_parent = parent

        master = object()
        window = FakeWindow(master)
        with patch("kicad_blocks.plugin.tk", SimpleNamespace(TclError=RuntimeError)):
            self.assertTrue(_attach_tk_window_to_editor(window))
        self.assertIs(window.transient_parent, master)

    def test_record_lookup_finds_a_block_in_another_registered_library(self):
        second_root = Path(self.temp.name) / "second-library"
        other_block = create_block(
            second_root,
            "Second Library Block",
            parse_pins("SDA:bidirectional, SCL:bidirectional"),
        )
        instance = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(
                    library=other_block.manifest["library_nickname"],
                    name=other_block.manifest["symbol_name"],
                )
            )
        )

        resolved = _library_record_for_instance([self.root, second_root], instance)
        self.assertEqual(resolved.path, other_block.path)

    def test_mousewheel_scrolls_without_dragging_the_scrollbar(self):
        class FakeWidget:
            def __init__(self, children=()):
                self.bindings = {}
                self.children = list(children)

            def bind(self, event, callback, add=None):
                self.bindings[event] = callback

            def winfo_children(self):
                return self.children

        widget = FakeWidget()
        scrolls = []
        _bind_mousewheel(widget, scrolls.append)

        widget.bindings["<MouseWheel>"](SimpleNamespace(delta=120, num=None))
        widget.bindings["<Button-5>"](SimpleNamespace(delta=0, num=5))
        widget.bindings["<Button-4>"](SimpleNamespace(delta=0, num=4))

        self.assertEqual(scrolls, [-1, 1, -1])

    def test_mousewheel_accumulates_high_resolution_trackpad_deltas(self):
        class FakeWidget:
            def __init__(self):
                self.bindings = {}

            def bind(self, event, callback, add=None):
                self.bindings[event] = callback

        widget = FakeWidget()
        scrolls = []
        _bind_mousewheel(widget, scrolls.append)
        handler = widget.bindings["<MouseWheel>"]
        handler(SimpleNamespace(delta=0.25, num=None))
        handler(SimpleNamespace(delta=0.25, num=None))
        handler(SimpleNamespace(delta=0.5, num=None))
        self.assertEqual(scrolls, [-1])

    def test_mousewheel_binding_can_cover_nested_controls(self):
        class FakeWidget:
            def __init__(self, children=()):
                self.bindings = {}
                self.children = list(children)

            def bind(self, event, callback, add=None):
                self.bindings[event] = callback

            def winfo_children(self):
                return self.children

        child = FakeWidget()
        widget = FakeWidget([child])
        scrolls = []
        _bind_mousewheel(widget, scrolls.append, include_children=True)
        child.bindings["<MouseWheel>"](SimpleNamespace(delta=120, num=None))
        self.assertEqual(scrolls, [-1])

    def test_dark_theme_can_be_forced_or_disabled(self):
        with patch.dict(os.environ, {"KICAD_BLOCKS_THEME": "dark"}):
            self.assertTrue(_system_prefers_dark_theme())
        with patch.dict(os.environ, {"KICAD_BLOCKS_THEME": "light"}):
            self.assertFalse(_system_prefers_dark_theme())

    def test_system_theme_keeps_native_tk_theme_without_os_probes(self):
        with (
            patch.dict(os.environ, {"KICAD_BLOCKS_THEME": "system"}),
            patch("kicad_blocks.plugin.subprocess.run") as run_process,
        ):
            self.assertIsNone(_system_prefers_dark_theme())
        run_process.assert_not_called()

    def test_record_picker_returns_single_choice_without_opening_a_dialog(self):
        self.assertIs(_pick_record([self.block], object(), "Pick Block"), self.block)

    def test_record_picker_reports_an_empty_library(self):
        with self.assertRaisesRegex(ValueError, "No blocks were found"):
            _pick_record([], object(), "Pick Block")

    def test_search_keeps_selected_block_when_it_still_matches(self):
        records = [self.block, self.filter_block]
        preferred = _preferred_library_selection(
            _filter_library_records(records, "converter"), self.block
        )
        self.assertIs(preferred, self.block)

    def test_search_falls_back_to_first_visible_block_when_selection_disappears(self):
        visible = _filter_library_records([self.block, self.filter_block], "USB")
        preferred = _preferred_library_selection(visible, self.block)
        self.assertIs(preferred, self.filter_block)
        self.assertIsNone(_preferred_library_selection([], self.block))
    def test_double_click_row_lookup_never_reuses_a_previous_selection(self):
        records = {
            str(self.block.manifest["block_id"]): self.block,
            str(self.filter_block.manifest["block_id"]): self.filter_block,
        }
        self.assertIs(
            _library_record_for_tree_row(records, str(self.filter_block.manifest["block_id"])),
            self.filter_block,
        )
        self.assertIsNone(_library_record_for_tree_row(records, ""))
        self.assertIsNone(_library_record_for_tree_row(records, None))
        self.assertIsNone(_library_record_for_tree_row(records, "not-a-row"))

    def test_placement_minimizes_browser_and_manager_and_restores_on_failure(self):
        class FakeWindow:
            def __init__(self, master=None, initial_state="normal"):
                self.master = master
                self.current_state = initial_state
                self.events = []

            def state(self):
                return self.current_state

            def iconify(self):
                self.events.append("iconify")
                self.current_state = "iconic"

            def deiconify(self):
                self.events.append("deiconify")
                self.current_state = "normal"

        manager = FakeWindow()
        browser = FakeWindow(master=manager)
        minimized = _minimize_placement_windows(browser)
        self.assertEqual(minimized, [browser, manager])
        self.assertEqual(browser.current_state, "iconic")
        self.assertEqual(manager.current_state, "iconic")

        _restore_placement_windows(minimized)
        self.assertEqual(manager.events, ["iconify", "deiconify"])
        self.assertEqual(browser.events, ["iconify", "deiconify"])
        self.assertEqual(browser.current_state, "normal")
        self.assertEqual(manager.current_state, "normal")

    def test_minimizer_leaves_already_hidden_windows_unchanged(self):
        class FakeWindow:
            def __init__(self, master=None, initial_state="normal"):
                self.master = master
                self.current_state = initial_state
                self.events = []

            def state(self):
                return self.current_state

            def iconify(self):
                self.events.append("iconify")
                self.current_state = "iconic"

        manager = FakeWindow()
        browser = FakeWindow(master=manager, initial_state="iconic")
        minimized = _minimize_placement_windows(browser)
        self.assertEqual(minimized, [manager])
        self.assertEqual(browser.events, [])
        self.assertEqual(manager.events, ["iconify"])

    def test_sheet_center_uses_page_dimensions_orientation_and_grid(self):
        landscape = SimpleNamespace(page_size=2, orientation=1)
        schematic = SimpleNamespace(get_page_settings=lambda: landscape)
        self.assertEqual(_schematic_page_center_mm(schematic), (148.59, 105.41))

        portrait = SimpleNamespace(page_size=2, orientation=2)
        schematic = SimpleNamespace(get_page_settings=lambda: portrait)
        self.assertEqual(_schematic_page_center_mm(schematic), (105.41, 148.59))

    def test_sheet_center_supports_custom_page_dimensions(self):
        custom = SimpleNamespace(
            page_size=16,
            orientation=1,
            user_page_size=SimpleNamespace(x=300_000_000, y=200_000_000),
        )
        schematic = SimpleNamespace(get_page_settings=lambda: custom)
        self.assertEqual(_schematic_page_center_mm(schematic), (149.86, 100.33))

    def test_sheet_center_rejects_unknown_or_invalid_page_settings(self):
        unknown = SimpleNamespace(
            get_page_settings=lambda: SimpleNamespace(page_size=99, orientation=1)
        )
        with self.assertRaisesRegex(RuntimeError, "Unsupported KiCad schematic page-size"):
            _schematic_page_center_mm(unknown)
        invalid_custom = SimpleNamespace(
            get_page_settings=lambda: SimpleNamespace(
                page_size=16,
                user_page_size=SimpleNamespace(x=0, y=200_000_000),
            )
        )
        with self.assertRaisesRegex(RuntimeError, "invalid schematic page dimensions"):
            _schematic_page_center_mm(invalid_custom)

    def test_place_block_creates_at_page_centre_focuses_and_selects_new_symbol(self):
        symbol = SimpleNamespace(id="placed-symbol-id", path=object())

        class Schematic:
            def __init__(self):
                self.selected = []
                self.placement = None
                self.saved = False

            def get_page_settings(self):
                return SimpleNamespace(page_size=2, orientation=1)

            def place_symbol_from_library(self, symbol_id, position):
                self.placement = (symbol_id, position)
                return symbol

            def clear_selection(self):
                self.selected.clear()

            def add_to_selection(self, items):
                self.selected.extend(items)

            def get_selection(self):
                return self.selected

            def save(self):
                self.saved = True

        schematic = Schematic()
        kicad = SimpleNamespace(get_schematic=lambda: schematic)
        focus = Mock(return_value=schematic)
        vector2_module = SimpleNamespace(
            Vector2=SimpleNamespace(from_xy_mm=lambda x, y: (x, y))
        )
        with (
            patch.dict(
                sys.modules,
                {"kipy": SimpleNamespace(), "kipy.geometry": vector2_module},
            ),
            patch("kicad_blocks.plugin._focus_schematic_items_in_editor", focus),
        ):
            result = _start_native_symbol_placement(
                kicad, None, "buck_converter_ab12cd34"
            )

        self.assertIs(result, symbol)
        self.assertEqual(
            schematic.placement,
            (f"{LIBRARY_NICKNAME}:buck_converter_ab12cd34", (148.59, 105.41)),
        )
        focus.assert_called_once_with(schematic, [symbol])
        self.assertEqual(schematic.selected, [symbol])
        self.assertTrue(schematic.saved)

    def test_place_block_rejects_invalid_symbol_id_before_connecting(self):
        get_schematic = Mock()
        with self.assertRaisesRegex(ValueError, "invalid KiCad symbol name"):
            _start_native_symbol_placement(
                SimpleNamespace(get_schematic=get_schematic), None, "bad symbol id"
            )
        get_schematic.assert_not_called()

    def test_place_block_requires_an_active_schematic(self):
        with self.assertRaisesRegex(RuntimeError, "No active schematic"):
            _start_native_symbol_placement(
                SimpleNamespace(get_schematic=lambda: None), None, "valid_symbol"
            )

if __name__ == "__main__":
    unittest.main()
