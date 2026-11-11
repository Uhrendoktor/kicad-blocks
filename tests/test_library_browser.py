"""Unit tests for the searchable block browser and native placement handoff."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_parameters, parse_pins
from kicad_blocks.plugin import (
    _block_overview_text,
    _filter_library_records,
    _preferred_library_selection,
    _start_native_symbol_placement,
)
from kicad_blocks.repository import create_block


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

    def test_overview_contains_pin_types_parameters_variants_and_layouts(self):
        overview = _block_overview_text(self.block)
        self.assertIn("VIN", overview)
        self.assertIn("input", overview)
        self.assertIn("OUTPUT_VOLTAGE : number = 3.3", overview)
        self.assertIn("1V8", overview)
        self.assertIn("3V3", overview)
        self.assertIn("Compact", overview)

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
    def test_native_placement_handoff_uses_add_symbol_hotkey_and_exact_library_name(self):
        calls = []
        focus_values = iter(("0x222", "0x111"))

        def command_runner(arguments, **kwargs):
            calls.append((arguments, kwargs))
            if arguments[1] == "getwindowfocus":
                return SimpleNamespace(stdout=next(focus_values))
            if arguments[1] == "getwindowpid":
                return SimpleNamespace(stdout="4242")
            if arguments[1] == "getwindowname":
                return SimpleNamespace(stdout="Choose Symbol")
            return SimpleNamespace(stdout="")

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

    def test_native_placement_refuses_to_type_when_choose_symbol_dialog_is_absent(self):
        calls = []

        def command_runner(arguments, **kwargs):
            calls.append(arguments)
            if arguments[1] == "getwindowfocus":
                return SimpleNamespace(stdout="0x111")
            if arguments[1] == "getwindowpid":
                return SimpleNamespace(stdout="4242")
            if arguments[1] == "getwindowname":
                return SimpleNamespace(stdout="Schematic Editor")
            return SimpleNamespace(stdout="")

        with self.assertRaisesRegex(RuntimeError, "Choose Symbol dialog did not open"):
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

    def test_native_placement_requires_xdotool_and_editor_window(self):
        with (
            patch("kicad_blocks.plugin.shutil.which", return_value=None),
            self.assertRaisesRegex(RuntimeError, "needs xdotool"),
        ):
            _start_native_symbol_placement(
                SimpleNamespace(),
                "0x111", "buck_converter_ab12cd34",
                xdotool=None, command_runner=Mock(), pause=lambda _seconds: None,
            )
        with self.assertRaisesRegex(RuntimeError, "identify the active Eeschema window"):
            _start_native_symbol_placement(
                SimpleNamespace(),
                None, "buck_converter_ab12cd34",
                xdotool="/usr/bin/xdotool", command_runner=Mock(), pause=lambda _seconds: None,
            )

if __name__ == "__main__":
    unittest.main()
