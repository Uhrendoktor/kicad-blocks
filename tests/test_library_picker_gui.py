"""Smoke-test the searchable block picker with real Tk and X11 keyboard events."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN_SOURCE))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import create_block


def main() -> None:
    xdotool = shutil.which("xdotool")
    if not xdotool:
        raise FileNotFoundError("xdotool is required for the picker GUI test.")
    plugin_actions.load_tkinter()
    with tempfile.TemporaryDirectory(prefix="kicad-block-picker-") as temp:
        library = Path(temp) / "library"
        first = create_block(
            library, "Plugin Registration Smoke Block", parse_pins("VIN:input, GND:passive")
        )
        target = create_block(
            library,
            "USB Interface Picker Target",
            parse_pins("VBUS:input, GND:passive, USB_P:bidirectional"),
        )

        parent = plugin_actions.tk.Tk()
        parent.title("KiCad Blocks Picker Test Parent")
        parent.geometry("360x120+20+20")
        parent.update()
        picker_title = "KiCad Blocks Picker Smoke Test"
        interaction_errors: list[str] = []
        deadline = time.monotonic() + 8.0

        def drive_picker() -> None:
            try:
                matching = subprocess.run(
                    [xdotool, "search", "--onlyvisible", "--name", picker_title],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.splitlines()
                if not matching:
                    if time.monotonic() < deadline:
                        parent.after(100, drive_picker)
                        return
                    raise TimeoutError(f"Picker window {picker_title!r} did not appear.")
                subprocess.run(
                    [xdotool, "windowfocus", "--sync", matching[-1]],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [xdotool, "type", "--clearmodifiers", "--delay", "20", "usb"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [xdotool, "key", "--clearmodifiers", "Down"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [xdotool, "key", "--clearmodifiers", "Return"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError) as exc:
                interaction_errors.append(str(exc))
                for child in parent.winfo_children():
                    with contextlib.suppress(Exception):
                        child.destroy()

        parent.after(250, drive_picker)
        try:
            selected = plugin_actions._pick_record([first, target], parent, picker_title)
        finally:
            parent.destroy()
        if interaction_errors:
            raise RuntimeError(
                "Searchable block picker keyboard flow failed: " + interaction_errors[0]
            )
        if selected is None:
            raise AssertionError("The picker was cancelled instead of selecting a row.")
        if selected.manifest["block_id"] != target.manifest["block_id"]:
            raise AssertionError(
                f"The picker returned {selected.name!r} instead of {target.name!r}."
            )
        preview_root = plugin_actions.tk.Tk()
        preview_root.withdraw()
        canvas = plugin_actions.tk.Canvas(preview_root, width=680, height=300)
        canvas.pack()
        try:
            native_image = plugin_actions._native_symbol_preview_photo(target)
            if native_image.width() <= 0 or native_image.height() <= 0:
                raise AssertionError("The native KiCad preview image is empty.")
            plugin_actions._draw_facade_preview(canvas, target)
            preview_root.update_idletasks()
            image_items = [item for item in canvas.find_all() if canvas.type(item) == "image"]
            if not image_items:
                raise AssertionError("The native KiCad symbol renderer produced no preview image.")
            image = getattr(canvas, "_native_preview_photo", None)
            if image is None or image.width() <= 0 or image.height() <= 0:
                raise AssertionError("The native KiCad preview image is empty.")
        finally:
            preview_root.destroy()
        print("Searchable block picker and KiCad-native SVG facade preview passed.")


if __name__ == "__main__":
    main()
