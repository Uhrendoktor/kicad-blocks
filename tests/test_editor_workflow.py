"""Exercise the plugin's library editor launch and publish workflow."""

import sys
import tempfile
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

    def tearDown(self):
        self.temp.cleanup()

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
        self.assertNotEqual(published_manifest["revision"], initial_manifest["revision"])
        self.assertIn("implementation.kicad_sch", published_manifest["hashes"])
        self.kicad.reload_library.assert_called_once()
        self.kicad.wait_for_libraries.assert_called_once()
        self.messagebox.showinfo.assert_called()

    def test_instance_configuration_is_one_undoable_ipc_update(self):
        first = SimpleNamespace(name="KB_PARAM_OUTPUT_VOLTAGE", text=SimpleNamespace(value="3.3"))
        second = SimpleNamespace(name="KB_LAYOUT", text=SimpleNamespace(value="standard"))
        instance = SimpleNamespace(reference="X1", user_fields=[first, second])
        commit = object()
        schematic = SimpleNamespace(
            begin_commit=Mock(return_value=commit),
            update_items=Mock(return_value=[instance]),
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
        schematic.update_items.assert_called_once_with([instance])
        schematic.push_commit.assert_called_once_with(
            commit, "Configure KiCad block instance"
        )
        schematic.save.assert_called_once_with()
        schematic.drop_commit.assert_not_called()

    def test_instance_configuration_rolls_back_local_fields_when_ipc_update_fails(self):
        field = SimpleNamespace(name="KB_PARAM_OUTPUT_VOLTAGE", text=SimpleNamespace(value="3.3"))
        instance = SimpleNamespace(reference="X1", user_fields=[field])
        commit = object()
        schematic = SimpleNamespace(
            begin_commit=Mock(return_value=commit),
            update_items=Mock(side_effect=RuntimeError("IPC update failed")),
            push_commit=Mock(),
            save=Mock(),
            drop_commit=Mock(),
        )

        with self.assertRaisesRegex(RuntimeError, "IPC update failed"):
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


if __name__ == "__main__":
    unittest.main()
