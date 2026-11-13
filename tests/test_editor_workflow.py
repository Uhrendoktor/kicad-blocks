"""Exercise the plugin's library editor launch and publish workflow."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
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

    def tearDown(self):
        self.temp.cleanup()

    def test_edit_facade_uses_native_eeschema_library_symbol_hotkey(self):
        instance = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(
                    library=self.block.manifest["library_nickname"],
                    name=self.block.manifest["symbol_name"],
                )
            )
        )
        schematic = SimpleNamespace(get_selection=Mock(return_value=[instance]))
        focus_result = SimpleNamespace(returncode=0, stdout="0x200\\n", stderr="")
        run_results = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            focus_result,
        ]

        with (
            patch.object(
                plugin_actions.shutil, "which", return_value="/usr/bin/xdotool"
            ),
            patch.object(
                plugin_actions, "_block_record_for_instance", return_value=self.block
            ) as resolve_block,
            patch.object(
                plugin_actions,
                "_minimize_placement_windows",
                return_value=[self.parent],
            ) as minimize,
            patch.object(plugin_actions, "_restore_placement_windows") as restore,
            patch.object(
                plugin_actions.subprocess, "run", side_effect=run_results
            ) as run_command,
        ):
            opened = plugin_actions._edit_facade_symbol(
                self.library,
                schematic,
                self.project,
                "0x100",
                self.parent,
            )

        self.assertIsNone(opened)
        resolve_block.assert_called_once_with(self.library, self.project, instance)
        minimize.assert_called_once_with(self.parent)
        restore.assert_not_called()
        self.assertEqual(
            run_command.call_args_list[0].args[0],
            ["/usr/bin/xdotool", "windowfocus", "--sync", "0x100"],
        )
        self.assertEqual(
            run_command.call_args_list[1].args[0],
            ["/usr/bin/xdotool", "key", "--clearmodifiers", "ctrl+shift+e"],
        )
        self.assertEqual(
            run_command.call_args_list[2].args[0],
            ["/usr/bin/xdotool", "getwindowfocus"],
        )

    def test_create_block_from_selected_sheet_never_prompts_for_manual_pins(self):
        source = self.project / "power_stage.kicad_sch"
        source.write_text("(kicad_sch)", encoding="utf-8")
        selection = """(sheet
            (property "Sheetname" "Power Stage" (at 100 100 0) (effects (font (size 1 1))))
            (property "Sheetfile" "power_stage.kicad_sch" (at 100 100 0) (effects (font (size 1 1))))
            (uuid "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        )"""
        schematic = SimpleNamespace(
            get_selection_as_string=Mock(return_value=selection)
        )
        messagebox = SimpleNamespace(showinfo=Mock())
        with (
            patch.object(
                plugin_actions,
                "_ask_create_block_details",
                return_value=("Power Stage", "Power stage description"),
            ) as details,
            patch.object(plugin_actions, "messagebox", messagebox),
            patch.object(
                plugin_actions, "create_block", return_value=self.block
            ) as create,
            patch.object(
                plugin_actions, "_register_block", return_value=self.block
            ) as register,
        ):
            plugin_actions._create_and_place(
                self.library,
                schematic,
                self.kicad,
                self.project,
                self.parent,
                "0x100",
            )

        details.assert_called_once_with(self.parent, source.resolve())
        create.assert_called_once_with(
            self.library,
            "Power Stage",
            source_schematic=source.resolve(),
            source_project_name=self.project.name,
            source_sheet_uuid="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            description="Power stage description",
        )
        register.assert_called_once_with(self.project, self.block, self.kicad)
        messagebox.showinfo.assert_called_once()

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
            document=SimpleNamespace(sheet_path=FakeSheetPath(["root-id", "sheet-id"])),
        )
        messagebox = SimpleNamespace(showinfo=Mock())

        with (
            patch.object(
                plugin_actions,
                "_ask_create_block_details",
                return_value=("Power Stage", "Current sheet capture"),
            ) as details,
            patch.object(plugin_actions, "messagebox", messagebox),
            patch.object(
                plugin_actions, "create_block", return_value=self.block
            ) as create,
            patch.object(plugin_actions, "_register_block", return_value=self.block),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )

        details.assert_called_once_with(self.parent, source.resolve())
        create.assert_called_once_with(
            self.library,
            "Power Stage",
            source_schematic=source.resolve(),
            source_project_name=self.project.name,
            source_sheet_uuid="sheet-id",
            description="Current sheet capture",
        )

    def test_reload_symbol_library_uses_gui_supported_reload_only(self):
        kicad = SimpleNamespace(
            reload_library=Mock(),
            wait_for_libraries=Mock(
                return_value=[SimpleNamespace(nickname="KBlocks", error_message=None)]
            ),
            load_all_libraries=Mock(
                side_effect=AssertionError("headless-only API called")
            ),
        )

        self.assertTrue(plugin_actions._reload_symbol_library(kicad, "KBlocks"))
        kicad.reload_library.assert_called_once_with(
            type=1, scope=2, nicknames=["KBlocks"]
        )
        kicad.wait_for_libraries.assert_called_once()
        kicad.load_all_libraries.assert_not_called()

    def test_edit_facade_requires_one_selected_symbol(self):
        schematic = SimpleNamespace(get_selection=Mock(return_value=[]))
        with self.assertRaisesRegex(
            ValueError, "Select one or more block symbol instances"
        ):
            plugin_actions._edit_facade_symbol(
                self.library,
                schematic,
                self.project,
                "0x100",
                self.parent,
            )

    def test_edit_opens_library_implementation_and_publish_advances_revision(self):
        initial_manifest = read_manifest(self.block.path)
        process = object()

        with (
            patch.object(plugin_actions, "messagebox", self.messagebox),
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
        self.assertNotEqual(
            published_manifest["revision"], initial_manifest["revision"]
        )
        self.assertIn("implementation.kicad_sch", published_manifest["hashes"])
        self.kicad.reload_library.assert_called_once()
        self.kicad.wait_for_libraries.assert_called_once()
        self.messagebox.showinfo.assert_called()

    def test_instance_configuration_is_one_undoable_ipc_update(self):
        from google.protobuf.empty_pb2 import Empty
        from kipy.proto.common.commands import editor_commands_pb2

        first = SimpleNamespace(
            name="KB_PARAM_OUTPUT_VOLTAGE", text=SimpleNamespace(value="3.3")
        )
        second = SimpleNamespace(
            name="KB_LAYOUT", text=SimpleNamespace(value="standard")
        )
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

        field = SimpleNamespace(
            name="KB_PARAM_OUTPUT_VOLTAGE", text=SimpleNamespace(value="3.3")
        )
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
            parameters=parse_parameters(
                "OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true"
            ),
        )
        instance = SimpleNamespace(
            user_fields=[
                SimpleNamespace(
                    name="KB_PARAM_OUTPUT_VOLTAGE", text=SimpleNamespace(value="1.8")
                ),
                SimpleNamespace(
                    name="KB_PARAM_ENABLE", text=SimpleNamespace(value="no")
                ),
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
        field = SimpleNamespace(
            name="KB_LAYOUT", text=SimpleNamespace(value="low_profile")
        )
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
            user_fields=[
                SimpleNamespace(name="KB_LAYOUT", text=SimpleNamespace(value="removed"))
            ],
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
        kicad = SimpleNamespace(
            get_kicad_binary_path=Mock(return_value="/usr/bin/pcbnew")
        )
        missing = self.project / "missing.kicad_pcb"
        with self.assertRaisesRegex(
            FileNotFoundError, "Generated PCB board does not exist"
        ):
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
            document=SimpleNamespace(sheet_path=FakeSheetPath(["root-id", "sheet-id"])),
        )
        with (
            patch.object(
                plugin_actions,
                "_ask_create_block_details",
                return_value=("Power Stage", "Active sheet fallback"),
            ),
            patch.object(plugin_actions, "messagebox", self.messagebox),
            patch.object(
                plugin_actions, "create_block", return_value=self.block
            ) as create,
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
            description="Active sheet fallback",
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
            document=SimpleNamespace(sheet_path=FakeSheetPath(["root-id", "sheet-id"])),
        )
        with (
            patch.object(
                plugin_actions,
                "_ask_create_block_details",
                return_value=("UART Interface", "Selected child sheet"),
            ),
            patch.object(plugin_actions, "messagebox", self.messagebox),
            patch.object(
                plugin_actions, "create_block", return_value=self.block
            ) as create,
            patch.object(plugin_actions, "_register_block", return_value=self.block),
        ):
            plugin_actions._create_and_place(
                self.library, schematic, self.kicad, self.project, self.parent
            )

        create.assert_called_once_with(
            self.library,
            "UART Interface",
            source_schematic=selected_source.resolve(),
            source_project_name=self.project.name,
            source_sheet_uuid=selected_uuid,
            description="Selected child sheet",
        )

    def test_run_action_captures_kicad_window_before_creating_tk(self):
        events = []
        root = Mock()
        fake_tk = SimpleNamespace(
            Tk=Mock(side_effect=lambda: events.append("tk.Tk") or root),
            TclError=Exception,
        )
        with (
            patch.object(
                plugin_actions,
                "_capture_eeschema_window_id",
                side_effect=lambda: events.append("capture") or "0x100",
            ),
            patch.object(
                plugin_actions,
                "_load_tkinter",
                side_effect=lambda: events.append("load tkinter"),
            ),
            patch.object(plugin_actions, "tk", fake_tk),
            patch.object(plugin_actions, "_configure_tk_theme"),
            patch.object(plugin_actions, "_attach_tk_window_to_editor"),
            patch.object(plugin_actions, "_show_action_hub"),
        ):
            plugin_actions.run_action("open-hub")

        self.assertEqual(events[:3], ["capture", "load tkinter", "tk.Tk"])
        self.assertEqual(
            root._kicad_blocks_editor_window_id,
            "0x100",
        )


if __name__ == "__main__":
    unittest.main()
