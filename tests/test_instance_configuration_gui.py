"""GUI smoke-test the editable KiCad block instance configuration form."""

from __future__ import annotations

import argparse
import contextlib
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_parameters, parse_pins
from kicad_blocks.repository import create_block


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _capture_dialog(dialog, destination: Path) -> None:
    """Save a screenshot of the actual configuration dialog while it is visible."""
    from PIL import ImageGrab, ImageStat

    dialog.update_idletasks()
    dialog.update()
    time.sleep(0.2)
    x, y = dialog.winfo_rootx(), dialog.winfo_rooty()
    width, height = dialog.winfo_width(), dialog.winfo_height()
    if width < 500 or height < 260:
        raise AssertionError(
            f"Configuration dialog is unexpectedly small: {width}x{height}."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height))
    if image.width < 500 or image.height < 260:
        raise AssertionError(
            f"Captured configuration image is unexpectedly small: {image.size!r}."
        )
    sample = image.convert("RGB").resize((48, 32))
    if max(ImageStat.Stat(sample).stddev) < 2.0:
        raise AssertionError("Configuration dialog screenshot appears blank.")
    image.save(destination, format="PNG")


def main(screenshot_path: Path | None = None) -> None:
    plugin_actions._load_tkinter()
    with tempfile.TemporaryDirectory(prefix="kicad-block-config-gui-") as temp:
        root_dir = Path(temp)
        library = root_dir / "library"
        project = root_dir / "project"
        project.mkdir()
        standard = root_dir / "standard.kicad_pcb"
        compact = root_dir / "compact.kicad_pcb"
        variant_18 = root_dir / "implementation_1v8.kicad_sch"
        variant_33 = root_dir / "implementation_3v3.kicad_sch"
        standard.write_text("(kicad_pcb)\\n", encoding="utf-8")
        compact.write_text("(kicad_pcb)\\n", encoding="utf-8")
        variant_18.write_text(
            '(kicad_sch (version 20250114) (generator "test"))\\n', encoding="utf-8"
        )
        variant_33.write_text(
            '(kicad_sch (version 20250114) (generator "test"))\\n', encoding="utf-8"
        )
        block = create_block(
            library,
            "Configuration GUI Block",
            parse_pins("VIN:input, GND:passive"),
            parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3"),
            variant_files=[
                {"value": "3V3", "path": variant_33},
                {"value": "1V8", "path": variant_18},
            ],
            layout_files=[
                {"id": "standard", "name": "Standard", "path": standard},
                {"id": "compact", "name": "Compact", "path": compact},
            ],
            profiles=[
                {
                    "id": "standard_3v3",
                    "name": "Standard 3.3 V",
                    "variant": "3V3",
                    "parameters": {"OUTPUT_VOLTAGE": "3.3"},
                    "layout": "standard",
                },
                {
                    "id": "low_voltage_compact",
                    "name": "Low voltage compact",
                    "variant": "1V8",
                    "parameters": {"OUTPUT_VOLTAGE": "1.8"},
                    "layout": "compact",
                },
            ],
        )

        fields = [
            SimpleNamespace(name="KB_PROFILE", text=SimpleNamespace(value="")),
            SimpleNamespace(name="KB_VARIANT", text=SimpleNamespace(value="")),
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
            get_project=lambda: SimpleNamespace(
                expand_text_variables=lambda value: value
            ),
            begin_commit=Mock(return_value=commit),
            push_commit=Mock(),
            drop_commit=Mock(),
            save=Mock(),
        )
        parent = plugin_actions.tk.Tk()
        plugin_actions._configure_tk_theme(parent)
        parent.title("KiCad Blocks Configuration Test Parent")
        parent.geometry("380x120+20+20")
        parent.update()

        interaction_errors: list[str] = []
        original_showinfo = plugin_actions.messagebox.showinfo
        original_showerror = plugin_actions.messagebox.showerror
        plugin_actions.messagebox.showinfo = lambda *_args, **_kwargs: None
        plugin_actions.messagebox.showerror = lambda _title, message, **_kwargs: (
            interaction_errors.append(str(message))
        )
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
                    raise TimeoutError(
                        "The Configure Block Instance form did not appear."
                    )
                dialog = dialogs[-1]
                widgets = list(_walk(dialog))
                combos = [
                    widget
                    for widget in widgets
                    if isinstance(widget, plugin_actions.ttk.Combobox)
                ]
                entries = [
                    widget
                    for widget in widgets
                    if isinstance(widget, plugin_actions.ttk.Entry)
                    and not isinstance(widget, plugin_actions.ttk.Combobox)
                ]
                buttons = [
                    widget
                    for widget in widgets
                    if isinstance(widget, plugin_actions.ttk.Button)
                ]
                save_button = next(
                    (
                        button
                        for button in buttons
                        if button.cget("text") == "Save instance"
                    ),
                    None,
                )
                if len(entries) != 1 or len(combos) != 3 or save_button is None:
                    raise AssertionError(
                        f"Unexpected configuration form controls: "
                        f"entries={len(entries)}, combos={len(combos)}, save={save_button is not None}"
                    )
                combos[0].current(1)
                combos[0].event_generate("<<ComboboxSelected>>")
                dialog.update()
                if entries[0].get() != "1.8":
                    raise AssertionError(
                        "Selecting the profile did not apply its typed parameter preset."
                    )
                if combos[1].get() != "1V8":
                    raise AssertionError(
                        "Selecting the profile did not choose its schematic variant."
                    )
                if combos[2].get() != "Compact  [compact]":
                    raise AssertionError(
                        "Selecting the profile did not choose its PCB layout."
                    )
                if screenshot_path is not None:
                    _capture_dialog(dialog, screenshot_path)
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

        update_calls: list[str] = []
        original_update = plugin_actions._update_schematic_item_checked
        plugin_actions._update_schematic_item_checked = lambda _schematic, target: (
            update_calls.append(str(target.reference))
        )
        parent.after(200, drive_form)
        try:
            plugin_actions._show_selected_block_configuration(
                schematic, project, library, parent
            )
        finally:
            plugin_actions._update_schematic_item_checked = original_update
            plugin_actions.messagebox.showinfo = original_showinfo
            plugin_actions.messagebox.showerror = original_showerror
            with contextlib.suppress(plugin_actions.tk.TclError):
                parent.destroy()

        if interaction_errors:
            raise RuntimeError(
                "Instance configuration GUI interaction failed: "
                + interaction_errors[0]
            )
        if update_calls != ["X1"]:
            raise AssertionError(
                f"The form did not submit exactly one update for X1: {update_calls!r}"
            )
        values = {field.name: field.text.value for field in instance.user_fields}
        if values != {
            "KB_PROFILE": "low_voltage_compact",
            "KB_VARIANT": "1V8",
            "KB_PARAM_OUTPUT_VOLTAGE": "1.8",
            "KB_LAYOUT": "compact",
        }:
            raise AssertionError(
                f"Configuration form did not save the edited fields: {values!r}"
            )
        schematic.begin_commit.assert_called_once_with()
        schematic.push_commit.assert_called_once_with(
            commit, "Configure KiCad block instance"
        )
        schematic.save.assert_called_once_with()
        print(
            "Editable block instance configuration GUI passed: "
            "profile variant, parameter and PCB layout saved through one IPC transaction."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Save the rendered configuration dialog before submitting it.",
    )
    main(parser.parse_args().screenshot)
