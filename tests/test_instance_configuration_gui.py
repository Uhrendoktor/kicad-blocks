"""GUI smoke-test the editable KiCad block instance configuration form."""

from __future__ import annotations

import contextlib
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN_SOURCE))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_parameters, parse_pins
from kicad_blocks.repository import create_block


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def main() -> None:
    plugin_actions._load_tkinter()
    with tempfile.TemporaryDirectory(prefix="kicad-block-config-gui-") as temp:
        root_dir = Path(temp)
        library = root_dir / "library"
        project = root_dir / "project"
        project.mkdir()
        standard = root_dir / "standard.kicad_pcb"
        compact = root_dir / "compact.kicad_pcb"
        standard.write_text("(kicad_pcb)\n", encoding="utf-8")
        compact.write_text("(kicad_pcb)\n", encoding="utf-8")
        block = create_block(
            library,
            "Configuration GUI Block",
            parse_pins("VIN:input, GND:passive"),
            parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3"),
            layout_files=[
                {"name": "Standard", "path": standard},
                {"name": "Compact", "path": compact},
            ],
        )

        fields = [
            SimpleNamespace(
                name="KB_PARAM_OUTPUT_VOLTAGE",
                text=SimpleNamespace(value="3.3"),
            ),
            SimpleNamespace(name="KB_LAYOUT", text=SimpleNamespace(value="standard")),
        ]
        instance = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000777",
            reference="X1",
            definition=SimpleNamespace(
                id=SimpleNamespace(
                    library=block.manifest["library_nickname"],
                    name=block.manifest["symbol_name"],
                )
            ),
            user_fields=fields,
        )
        commit = object()
        schematic = SimpleNamespace(
            get_selection=lambda: [instance],
            begin_commit=Mock(return_value=commit),
            update_items=Mock(return_value=[instance]),
            push_commit=Mock(),
            drop_commit=Mock(),
            save=Mock(),
        )
        parent = plugin_actions.tk.Tk()
        parent.title("KiCad Blocks Configuration Test Parent")
        parent.geometry("380x120+20+20")
        parent.update()

        interaction_errors: list[str] = []
        original_showinfo = plugin_actions.messagebox.showinfo
        original_showerror = plugin_actions.messagebox.showerror
        plugin_actions.messagebox.showinfo = lambda *_args, **_kwargs: None
        plugin_actions.messagebox.showerror = lambda _title, message, **_kwargs: interaction_errors.append(str(message))
        deadline = time.monotonic() + 8.0

        def drive_form() -> None:
            try:
                dialogs = [
                    child
                    for child in parent.winfo_children()
                    if isinstance(child, plugin_actions.tk.Toplevel)
                    and child.title().startswith("Configure X1")
                ]
                if not dialogs:
                    if time.monotonic() < deadline:
                        parent.after(100, drive_form)
                        return
                    raise TimeoutError("The Configure Block Instance form did not appear.")
                dialog = dialogs[-1]
                widgets = list(_walk(dialog))
                combos = [
                    widget for widget in widgets
                    if isinstance(widget, plugin_actions.ttk.Combobox)
                ]
                entries = [
                    widget for widget in widgets
                    if isinstance(widget, plugin_actions.ttk.Entry)
                    and not isinstance(widget, plugin_actions.ttk.Combobox)
                ]
                buttons = [
                    widget for widget in widgets
                    if isinstance(widget, plugin_actions.ttk.Button)
                ]
                save_button = next(
                    (button for button in buttons if button.cget("text") == "Save instance"),
                    None,
                )
                if len(entries) != 1 or len(combos) != 1 or save_button is None:
                    raise AssertionError(
                        f"Unexpected configuration form controls: "
                        f"entries={len(entries)}, combos={len(combos)}, save={save_button is not None}"
                    )
                entries[0].delete(0, "end")
                entries[0].insert(0, "1.8")
                combos[0].set("Compact  [compact]")
                save_button.invoke()
            except (
                AssertionError,
                OSError,
                RuntimeError,
                StopIteration,
                plugin_actions.tk.TclError,
            ) as exc:
                interaction_errors.append(str(exc))
                for child in parent.winfo_children():
                    if child is not parent:
                        with contextlib.suppress(Exception):
                            child.destroy()

        parent.after(200, drive_form)
        try:
            plugin_actions._show_selected_block_configuration(
                schematic, project, library, parent
            )
        finally:
            plugin_actions.messagebox.showinfo = original_showinfo
            plugin_actions.messagebox.showerror = original_showerror
            with contextlib.suppress(plugin_actions.tk.TclError):
                parent.destroy()

        if interaction_errors:
            raise RuntimeError(
                "Instance configuration GUI interaction failed: " + interaction_errors[0]
            )
        values = {field.name: field.text.value for field in instance.user_fields}
        if values != {
            "KB_PARAM_OUTPUT_VOLTAGE": "1.8",
            "KB_LAYOUT": "compact",
        }:
            raise AssertionError(f"Configuration form did not save the edited fields: {values!r}")
        schematic.begin_commit.assert_called_once_with()
        schematic.update_items.assert_called_once_with([instance])
        schematic.push_commit.assert_called_once_with(
            commit, "Configure KiCad block instance"
        )
        schematic.save.assert_called_once_with()
        print(
            "Editable block instance configuration GUI passed: "
            "parameter and PCB layout saved through one IPC transaction."
        )


if __name__ == "__main__":
    main()
