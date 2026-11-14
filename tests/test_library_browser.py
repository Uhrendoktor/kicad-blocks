"""Unit tests for the searchable block browser and native placement handoff."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, TypeVar, TypedDict, cast
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))


from kicad_blocks.model import (
    parse_parameters,
    parse_pins,
    resolve_variant_implementation,
)
from kicad_blocks.repository import BlockRecord, create_block, read_manifest

plugin_actions = import_module("kicad_blocks.plugin")

_HookResult = TypeVar("_HookResult")


def _private_hook(name: str) -> Callable[..., _HookResult]:
    """Resolve a private UI test hook without importing its private symbol."""
    return cast(Callable[..., _HookResult], getattr(plugin_actions, name))


class _MinimizableWindow(Protocol):
    """Window protocol used by the placement handoff tests."""

    master: _MinimizableWindow | None

    def state(self) -> str: ...

    def iconify(self) -> None: ...

    def deiconify(self) -> None: ...


class _CommandOutput(Protocol):
    """Subprocess response fixture consumed by the editor-window helpers."""

    stdout: str
    returncode: int


class _LayoutChoice(TypedDict):
    """Minimal layout descriptor shape consumed by the library-browser tests."""

    id: str | None
    name: str
    file: str
    project_file: str | None


def _command_output(stdout: str = "", returncode: int = 0) -> _CommandOutput:
    return cast(_CommandOutput, SimpleNamespace(stdout=stdout, returncode=returncode))


# These tests exercise UI implementation details directly. Resolve the private
# hooks by name and give each one the narrow return contract used by the tests.
_attach_tk_window_to_editor = cast(Callable[..., bool], _private_hook("_attach_tk_window_to_editor"))
_bind_mousewheel = cast(Callable[..., None], _private_hook("_bind_mousewheel"))
_block_overview_text = cast(Callable[..., str], _private_hook("_block_overview_text"))
_capture_eeschema_window_id = cast(Callable[..., str | None], _private_hook("_capture_eeschema_window_id"))
_choose_block_layout = cast(Callable[..., _LayoutChoice | None], _private_hook("_choose_block_layout"))
_clean_facade_preview_svg = cast(Callable[..., Path], _private_hook("_clean_facade_preview_svg"))
_filter_library_records = cast(Callable[..., list[BlockRecord]], _private_hook("_filter_library_records"))
_layout_choices = cast(Callable[..., list[_LayoutChoice]], _private_hook("_layout_choices"))
_library_record_for_instance = cast(Callable[..., BlockRecord], _private_hook("_library_record_for_instance"))
_library_record_for_tree_row = cast(Callable[..., BlockRecord | None], _private_hook("_library_record_for_tree_row"))
_minimize_placement_windows = cast(Callable[..., list[_MinimizableWindow]], _private_hook("_minimize_placement_windows"))
_new_blank_pcb_source = cast(Callable[..., Path], _private_hook("_new_blank_pcb_source"))
_pick_directory = cast(Callable[..., str | None], _private_hook("_pick_directory"))
_pick_kicad_file = cast(Callable[..., str | None], _private_hook("_pick_kicad_file"))
_pick_record = cast(Callable[..., BlockRecord | None], _private_hook("_pick_record"))
_preferred_library_selection = cast(Callable[..., BlockRecord | None], _private_hook("_preferred_library_selection"))
_restore_placement_windows = cast(Callable[..., None], _private_hook("_restore_placement_windows"))
_save_block_layouts = cast(Callable[..., BlockRecord], _private_hook("_save_block_layouts"))
_save_block_variants = cast(Callable[..., None], _private_hook("_save_block_variants"))
_set_window_geometry = cast(Callable[..., None], _private_hook("_set_window_geometry"))
_start_native_symbol_placement = cast(Callable[..., None], _private_hook("_start_native_symbol_placement"))
_system_prefers_dark_theme = cast(Callable[..., bool], _private_hook("_system_prefers_dark_theme"))


class LibraryBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
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

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_search_matches_description_pins_and_variant_values(self) -> None:
        records = [self.block, self.filter_block]
        self.assertEqual(_filter_library_records(records, "buck")[0].name, "Buck Converter")
        self.assertEqual(_filter_library_records(records, "common-mode")[0].name, "USB Filter")
        self.assertEqual(_filter_library_records(records, "USB_P")[0].name, "USB Filter")
        self.assertEqual(_filter_library_records(records, "1v8")[0].name, "Buck Converter")
        self.assertEqual(_filter_library_records(records, "vout")[0].name, "Buck Converter")
        self.assertEqual(_filter_library_records(records, ""), records)

    def test_native_facade_preview_removes_reference_and_component_name(self) -> None:
        from xml.etree import ElementTree as ET

        svg = self.root / "preview-source.svg"
        svg.write_text(
            """<svg xmlns="http://www.w3.org/2000/svg">
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

    def test_overview_contains_pin_types_parameters_variants_and_layouts(self) -> None:
        overview = _block_overview_text(self.block)
        self.assertIn("VIN", overview)
        self.assertIn("input", overview)
        self.assertIn("OUTPUT_VOLTAGE  ·  number  ·  default: 3.3", overview)
        self.assertIn("1V8", overview)
        self.assertIn("3V3", overview)
        self.assertIn("Compact", overview)
        self.assertIn("Default · implementation.kicad_sch", overview)
        self.assertIn("Circuit implementations · 3", overview)
        self.assertIn("Synchronous buck regulator", overview)
        self.assertIn("Connections · 4", overview)
        self.assertNotIn("Block ID", overview)
        self.assertNotIn(str(self.block.manifest["block_id"]), overview)


    def test_new_pcb_layout_is_created_as_default_and_saved_in_block(self) -> None:
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

    def test_each_pcb_layout_keeps_multiple_implementation_associations(self) -> None:
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

    def test_removing_all_pcb_layouts_clears_obsolete_variant_layout_ids(self) -> None:
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

    def test_native_file_and_directory_choosers_use_system_filedialog(self) -> None:
        open_dialog = Mock(return_value="/tmp/block.kicad_sch")
        directory_dialog = Mock(return_value="/tmp/library")
        with patch(
            "kicad_blocks.plugin.filedialog",
            SimpleNamespace(
                askopenfilename=open_dialog,
                askdirectory=directory_dialog,
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

    def test_layout_linked_to_implementation_is_used_when_instance_has_default(self) -> None:
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

    def test_implementation_link_supersedes_implicit_default_layout(self) -> None:
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

    def test_manifest_rejects_variant_link_to_unknown_layout(self) -> None:
        manifest_path = self.block.path / "manifest.json"
        manifest = read_manifest(self.block.path)
        manifest["variants"][0]["layout_id"] = "missing"
        manifest_path.write_text(
            __import__("json").dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "links to unknown PCB layout"):
            read_manifest(self.block.path)

    def test_editing_implementation_variants_preserves_pcb_layout_links(self) -> None:
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

    def test_default_implementation_is_persisted_and_used_for_empty_selector(self) -> None:
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

    def test_variant_metadata_can_be_added_edited_and_removed_from_library(self) -> None:
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

    def test_variant_can_be_edited_without_copying_its_file_onto_itself(self) -> None:
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

    def test_invalid_profile_change_does_not_copy_new_variant_files(self) -> None:
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

    def test_variant_editor_refuses_to_remove_an_implementation_used_by_a_profile(self) -> None:
        profiled = create_block(
            self.root,
            "Profiled Variant Block",
            parse_pins("VIN:input, VOUT:output"),
            variant_files=[{"value": "1V8", "path": self.v18}],
            profiles=[{"name": "Low voltage", "variant": "1V8"}],
        )
        with self.assertRaisesRegex(ValueError, "still use an implementation"):
            _save_block_variants(profiled, [])

    def test_window_geometry_respects_display_bounds(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.requested_geometry: str | None = None
                self.minimum: tuple[int, int] | None = None

            def update_idletasks(self) -> None:
                pass

            def winfo_screenwidth(self) -> int:
                return 1366

            def winfo_screenheight(self) -> int:
                return 768

            def geometry(self, value: str) -> None:
                self.requested_geometry = value

            def minsize(self, width: int, height: int) -> None:
                self.minimum = (width, height)

        window = FakeWindow()
        _set_window_geometry(window, 1480, 980, 1160, 740)
        self.assertEqual(window.requested_geometry, "1286x668+40+50")
        self.assertEqual(window.minimum, (1160, 668))

    def test_tk_transient_uses_client_window_and_editor_owner(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.classes: tuple[str, ...] | None = None
                self.attributes: tuple[str, ...] | None = None

            def wm_class(self, *arguments: str) -> tuple[str, ...]:
                self.classes = arguments
                return arguments

            def wm_attributes(self, *arguments: str) -> tuple[str, ...]:
                self.attributes = arguments
                return arguments

            def update_idletasks(self) -> None:
                pass

            def winfo_id(self) -> str:
                return "0x222"

        window = FakeWindow()
        with (
            patch("kicad_blocks.plugin.tk", SimpleNamespace(TclError=RuntimeError)),
            patch("kicad_blocks.plugin._set_x11_transient_owner", return_value=True) as set_owner,
        ):
            self.assertTrue(_attach_tk_window_to_editor(window, "0x111"))
        set_owner.assert_called_once_with("0x222", "0x111")
        self.assertEqual(window.classes, ("eeschema", "eeschema"))
        self.assertEqual(window.attributes, ("-type", "dialog"))


    def test_record_lookup_finds_a_block_in_another_registered_library(self) -> None:
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

    def test_mousewheel_scrolls_without_dragging_the_scrollbar(self) -> None:
        class FakeWidget:
            def __init__(self, children: Sequence[FakeWidget] = ()) -> None:
                self.bindings: dict[str, Callable[..., str]] = {}
                self.children: list[FakeWidget] = list(children)

            def bind(self, event: str, callback: Callable[..., str], add: str | None = None) -> None:
                self.bindings[event] = callback

            def winfo_children(self) -> list[FakeWidget]:
                return self.children

        widget = FakeWidget()
        scrolls = []
        _bind_mousewheel(widget, scrolls.append)

        widget.bindings["<MouseWheel>"](SimpleNamespace(delta=120, num=None))
        widget.bindings["<Button-5>"](SimpleNamespace(delta=0, num=5))
        widget.bindings["<Button-4>"](SimpleNamespace(delta=0, num=4))

        self.assertEqual(scrolls, [-1, 1, -1])

    def test_mousewheel_accumulates_high_resolution_trackpad_deltas(self) -> None:
        class FakeWidget:
            def __init__(self) -> None:
                self.bindings: dict[str, Callable[..., str]] = {}

            def bind(self, event: str, callback: Callable[..., str], add: str | None = None) -> None:
                self.bindings[event] = callback

        widget = FakeWidget()
        scrolls = []
        with patch("kicad_blocks.plugin.sys.platform", "darwin"):
            _bind_mousewheel(widget, scrolls.append)
        handler = widget.bindings["<MouseWheel>"]
        handler(SimpleNamespace(delta=0.25, num=None))
        handler(SimpleNamespace(delta=0.25, num=None))
        handler(SimpleNamespace(delta=0.5, num=None))
        self.assertEqual(scrolls, [-1])

    def test_mousewheel_binding_can_cover_nested_controls(self) -> None:
        class FakeWidget:
            def __init__(self, children: Sequence[FakeWidget] = ()) -> None:
                self.bindings: dict[str, Callable[..., str]] = {}
                self.children: list[FakeWidget] = list(children)

            def bind(self, event: str, callback: Callable[..., str], add: str | None = None) -> None:
                self.bindings[event] = callback

            def winfo_children(self) -> list[FakeWidget]:
                return self.children

        child = FakeWidget()
        widget = FakeWidget([child])
        scrolls = []
        _bind_mousewheel(widget, scrolls.append, include_children=True)
        child.bindings["<MouseWheel>"](SimpleNamespace(delta=120, num=None))
        self.assertEqual(scrolls, [-1])

    def test_dark_theme_can_be_forced_or_disabled(self) -> None:
        with patch.dict(os.environ, {"KICAD_BLOCKS_THEME": "dark"}):
            self.assertTrue(_system_prefers_dark_theme())
        with patch.dict(os.environ, {"KICAD_BLOCKS_THEME": "light"}):
            self.assertFalse(_system_prefers_dark_theme())

    def test_record_picker_returns_single_choice_without_opening_a_dialog(self) -> None:
        self.assertIs(_pick_record([self.block], object(), "Pick Block"), self.block)

    def test_record_picker_reports_an_empty_library(self) -> None:
        with self.assertRaisesRegex(ValueError, "No blocks were found"):
            _pick_record([], object(), "Pick Block")

    def test_search_keeps_selected_block_when_it_still_matches(self) -> None:
        records = [self.block, self.filter_block]
        preferred = _preferred_library_selection(
            _filter_library_records(records, "converter"), self.block
        )
        self.assertIs(preferred, self.block)

    def test_search_falls_back_to_first_visible_block_when_selection_disappears(self) -> None:
        visible = _filter_library_records([self.block, self.filter_block], "USB")
        preferred = _preferred_library_selection(visible, self.block)
        self.assertIs(preferred, self.filter_block)
        self.assertIsNone(_preferred_library_selection([], self.block))
    def test_double_click_row_lookup_never_reuses_a_previous_selection(self) -> None:
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

    def test_placement_minimizes_browser_and_manager_and_restores_on_failure(self) -> None:
        class FakeWindow:
            def __init__(self, master: _MinimizableWindow | None = None, initial_state: str = "normal") -> None:
                self.master = master
                self.current_state = initial_state
                self.events: list[str] = []

            def state(self) -> str:
                return self.current_state

            def iconify(self) -> None:
                self.events.append("iconify")
                self.current_state = "iconic"

            def deiconify(self) -> None:
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

    def test_minimizer_leaves_already_hidden_windows_unchanged(self) -> None:
        class FakeWindow:
            def __init__(self, master: _MinimizableWindow | None = None, initial_state: str = "normal") -> None:
                self.master = master
                self.current_state = initial_state
                self.events: list[str] = []

            def state(self) -> str:
                return self.current_state

            def iconify(self) -> None:
                self.events.append("iconify")
                self.current_state = "iconic"

        manager = FakeWindow()
        browser = FakeWindow(master=manager, initial_state="iconic")
        minimized = _minimize_placement_windows(browser)
        self.assertEqual(minimized, [manager])
        self.assertEqual(browser.events, [])
        self.assertEqual(manager.events, ["iconify"])

    def test_editor_capture_finds_eeschema_when_browser_has_focus(self) -> None:
        def command_runner(arguments: list[str], **kwargs: bool | int | str) -> _CommandOutput:
            command = arguments[1:]
            if command == ["getwindowfocus"]:
                return _command_output(stdout="0x222", returncode=0)
            if command == ["search", "--onlyvisible", "--class", "eeschema"]:
                return _command_output(stdout="0x111\n", returncode=0)
            if command == ["search", "--onlyvisible", "--name", "[Ss]chematic [Ee]ditor"]:
                return _command_output(stdout="0x111\n", returncode=0)
            if command == ["getwindowclassname", "0x222"]:
                return _command_output(stdout="eeschema", returncode=0)
            if command == ["getwindowname", "0x222"]:
                return _command_output(stdout="Functional Block Library", returncode=0)
            if command == ["getwindowclassname", "0x111"]:
                return _command_output(stdout="eeschema", returncode=0)
            if command == ["getwindowname", "0x111"]:
                return _command_output(stdout="Schematic Editor — project", returncode=0)
            if arguments[0] == "/usr/bin/xprop":
                return _command_output(stdout="WM_TRANSIENT_FOR: not found.", returncode=0)
            return _command_output(stdout="", returncode=0)

        with (
            patch(
                "kicad_blocks.plugin.shutil.which",
                side_effect=lambda name: {
                    "xdotool": "/usr/bin/xdotool",
                    "xprop": "/usr/bin/xprop",
                }.get(name),
            ),
            patch("kicad_blocks.plugin.subprocess.run", side_effect=command_runner),
        ):
            self.assertEqual(_capture_eeschema_window_id(), "0x111")

    def test_editor_capture_does_not_treat_plugin_browser_as_eeschema(self) -> None:
        def command_runner(arguments: list[str], **kwargs: bool | int | str) -> _CommandOutput:
            command = arguments[1:]
            if command == ["getwindowfocus"]:
                return _command_output(stdout="0x222", returncode=0)
            if command == ["search", "--onlyvisible", "--class", "eeschema"]:
                return _command_output(stdout="", returncode=1)
            if command == ["search", "--onlyvisible", "--name", "[Ss]chematic [Ee]ditor"]:
                return _command_output(stdout="", returncode=1)
            if command == ["getwindowclassname", "0x222"]:
                return _command_output(stdout="eeschema", returncode=0)
            if command == ["getwindowname", "0x222"]:
                return _command_output(stdout="Functional Block Library", returncode=0)
            if arguments[0] == "/usr/bin/xprop":
                return _command_output(stdout="WM_TRANSIENT_FOR: not found.", returncode=0)
            return _command_output(stdout="", returncode=0)

        with (
            patch(
                "kicad_blocks.plugin.shutil.which",
                side_effect=lambda name: {
                    "xdotool": "/usr/bin/xdotool",
                    "xprop": "/usr/bin/xprop",
                }.get(name),
            ),
            patch("kicad_blocks.plugin.subprocess.run", side_effect=command_runner),
        ):
            self.assertIsNone(_capture_eeschema_window_id())

    def test_native_placement_handoff_uses_add_symbol_hotkey_and_exact_library_name(self) -> None:
        calls = []
        focus_values = iter(("0x222", "0x111"))

        def command_runner(arguments: list[str], **kwargs: bool | int | str) -> _CommandOutput:
            calls.append((arguments, kwargs))
            if arguments[1] == "getwindowfocus":
                return _command_output(stdout=next(focus_values))
            if arguments[1] == "getwindowpid":
                return _command_output(stdout="4242")
            if arguments[1] == "getwindowname":
                return _command_output(stdout="Choose Symbol" if arguments[2] == "0x222" else "Schematic Editor")
            return _command_output(stdout="")

        pauses = []
        _start_native_symbol_placement(
            SimpleNamespace(),
            "0x111",
            "buck_converter_ab12cd34",
            xdotool="/usr/bin/xdotool",
            command_runner=command_runner,
            pause=pauses.append,
        )
        commands = [item[0][1:] for item in calls]
        self.assertEqual(commands[0], ["getwindowpid", "0x111"])
        self.assertIn(["windowfocus", "--sync", "0x111"], commands)
        self.assertIn(["key", "--clearmodifiers", "a"], commands)
        self.assertIn(
            ["type", "--clearmodifiers", "--delay", "12", "buck_converter_ab12cd34"],
            commands,
        )
        self.assertIn(["key", "--clearmodifiers", "Return"], commands)
        self.assertNotIn("position", " ".join(" ".join(command) for command in commands).casefold())
        self.assertTrue(pauses)

    def test_native_placement_refuses_to_type_when_choose_symbol_dialog_is_absent(self) -> None:
        calls = []

        def command_runner(arguments: list[str], **kwargs: bool | int | str) -> _CommandOutput:
            calls.append(arguments)
            if arguments[1] == "getwindowfocus":
                return _command_output(stdout="0x111")
            if arguments[1] == "getwindowpid":
                return _command_output(stdout="4242")
            if arguments[1] == "getwindowname":
                return _command_output(stdout="Schematic Editor")
            return _command_output(stdout="")

        with self.assertRaisesRegex(RuntimeError, "did not open its native symbol chooser"):
            _start_native_symbol_placement(
                SimpleNamespace(),
                "0x111",
                "buck_converter_ab12cd34",
                xdotool="/usr/bin/xdotool",
                command_runner=command_runner,
                pause=lambda _seconds: None,
            )
        self.assertFalse(any(command[1] == "type" for command in calls))
        self.assertIn(["key", "--clearmodifiers", "Escape"], [command[1:] for command in calls])

    def test_native_placement_uses_kicad_ipc_without_xdotool_or_captured_xid(self) -> None:
        kicad = SimpleNamespace(run_action=Mock())
        with patch("kicad_blocks.plugin.shutil.which", return_value=None):
            _start_native_symbol_placement(
                kicad,
                None,
                "buck_converter_ab12cd34",
                xdotool=None,
                command_runner=Mock(),
                pause=lambda _seconds: None,
            )
        kicad.run_action.assert_called_once_with("eeschema.InteractiveDrawing.placeSymbol")

    def test_native_placement_requires_editor_when_ipc_is_unavailable(self) -> None:
        with (
            patch("kicad_blocks.plugin.shutil.which", return_value="/usr/bin/xdotool"),
            patch("kicad_blocks.plugin._capture_eeschema_window_id", return_value=None),
            self.assertRaisesRegex(
                RuntimeError, "could not identify the active Schematic Editor"
            ),
        ):
            _start_native_symbol_placement(
                SimpleNamespace(),
                None,
                "buck_converter_ab12cd34",
                xdotool="/usr/bin/xdotool",
                command_runner=Mock(),
                pause=lambda _seconds: None,
            )

if __name__ == "__main__":
    unittest.main()
