"""Capture reproducible KiCad Blocks documentation images from the real UI and CLI."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import create_block, revision_for_hashes

from scripts.visual_asset_inventory import ASSET_NAMES

sys.path.insert(0, str(ROOT / 'examples' / 'stusb4500'))
from generate_reference import generate_reference_schematic


def find_executable(*names: str) -> str:
    for name in names:
        executable = shutil.which(name)
        if executable:
            return executable
    raise FileNotFoundError(f"Missing executable; tried: {', '.join(names)}")


def walk_widgets(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk_widgets(child)


def window_id(xdotool: str, title: str = "Place Functional Block") -> str | None:
    # X11 window managers may normalize or omit Unicode punctuation in titles.
    # Match the distinctive suffix/prefix as a fallback.
    candidates = [title]
    for separator in ("—", "–", "-"):
        parts = [part.strip() for part in title.split(separator) if part.strip()]
        if len(parts) > 1:
            candidates.extend((parts[-1], parts[0]))
    for candidate in dict.fromkeys(candidates):
        result = subprocess.run(
            [xdotool, "search", "--onlyvisible", "--name", candidate],
            capture_output=True, text=True, check=False, timeout=5,
        )
        ids = [line for line in result.stdout.splitlines() if line.strip().isdigit()]
        if ids:
            return ids[-1]
    return None


def capture_window(
    xdotool: str,
    identifier: str,
    destination: Path,
    *,
    minimum_size: tuple[int, int] = (600, 400),
) -> None:
    from PIL import ImageGrab

    geometry = subprocess.run(
        [xdotool, "getwindowgeometry", "--shell", identifier],
        capture_output=True, text=True, check=True, timeout=5,
    ).stdout
    values = {}
    for line in geometry.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = int(value)
    bbox = (
        values["X"], values["Y"],
        values["X"] + values["WIDTH"], values["Y"] + values["HEIGHT"],
    )
    image = ImageGrab.grab(bbox=bbox)
    if image.width < minimum_size[0] or image.height < minimum_size[1]:
        raise RuntimeError(
            f"Window capture is too small: {image.width}x{image.height}; "
            f"expected at least {minimum_size[0]}x{minimum_size[1]}"
        )
    image.save(destination, format="PNG")


def capture_picker(records, output_dir: Path) -> dict:
    """Capture initial, filtered and profile-selected states of the production chooser."""
    from PIL import Image, ImageStat

    xdotool = find_executable("xdotool")
    plugin_actions._load_tkinter()
    tk = plugin_actions.tk
    parent = tk.Tk()
    parent.title("KiCad Blocks visual fixture host")
    plugin_actions._configure_tk_theme(parent)
    parent.geometry("320x100+10+10")
    parent.update()

    frame_paths = [
        output_dir / "block-picker-initial.png",
        output_dir / "block-picker-filtered.png",
        output_dir / "block-picker-profile.png",
    ]
    errors: list[str] = []
    completed = [False]
    deadline = time.monotonic() + 20

    def drive() -> None:
        try:
            identifier = window_id(xdotool)
            if identifier is None:
                if time.monotonic() < deadline:
                    parent.after(100, drive)
                    return
                raise TimeoutError("The Place Functional Block chooser did not appear.")
            dialog = next(
                (
                    child for child in parent.winfo_children()
                    if isinstance(child, tk.Toplevel) and child.title() == "Place Functional Block"
                ),
                None,
            )
            if dialog is None:
                raise RuntimeError("The visible chooser window has no Tk dialog owner.")

            # Let Tk finish the first paint before recording the initial frame.
            dialog.update_idletasks()
            dialog.update()
            time.sleep(0.35)
            dialog.update()
            capture_window(xdotool, identifier, frame_paths[0])
            widgets = list(walk_widgets(dialog))
            entries = [item for item in widgets if item.winfo_class() in ("TEntry", "Entry")]
            combos = [item for item in widgets if item.winfo_class() == "TCombobox"]
            if not entries or len(combos) < 2:
                raise RuntimeError(f"Unexpected chooser controls: entries={len(entries)}, combos={len(combos)}")

            entries[0].delete(0, "end")
            entries[0].insert(0, "usb")
            dialog.update()
            capture_window(xdotool, identifier, frame_paths[1])

            combos[1].set("5 V / 2.4 A (PDO1)")
            combos[1].event_generate("<<ComboboxSelected>>")
            dialog.update()
            if combos[0].get() != "5V":
                raise AssertionError(f"Profile did not select the 5V implementation: {combos[0].get()!r}")
            capture_window(xdotool, identifier, frame_paths[2])

            place = next(
                (
                    item for item in widgets
                    if item.winfo_class() == "TButton" and item.cget("text") == "Place Block"
                ),
                None,
            )
            if place is None:
                raise RuntimeError("The Place Block button is missing.")
            place.invoke()
            completed[0] = True
        except (
            AssertionError,
            ImportError,
            KeyError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
            TimeoutError,
            tk.TclError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            for child in parent.winfo_children():
                if isinstance(child, tk.Toplevel):
                    with contextlib.suppress(tk.TclError):
                        if child.title() == "Place Functional Block":
                            child.destroy()

    parent.after(250, drive)
    try:
        selected = plugin_actions._pick_block_for_placement(records, parent)
    finally:
        parent.destroy()

    if errors:
        raise RuntimeError("Real chooser capture failed: " + errors[0])
    if not completed[0] or selected is None:
        raise AssertionError("The placement chooser did not complete the scripted selection.")
    if (
        selected["record"].name != "STUSB4500 USB-PD Sink"
        or selected["variant"] != "5V"
        or selected["profile"] != "pdo1_5v_2a4"
        or float(selected["parameters"]["PDO_CURRENT_A"]) != 2.4
    ):
        raise AssertionError(f"Unexpected profile-driven placement result: {selected!r}")

    for path in frame_paths:
        with Image.open(path) as image:
            sample = image.convert("RGB").resize((48, 32))
            if max(ImageStat.Stat(sample).stddev) < 2.0:
                raise AssertionError(f"Screenshot appears blank or uniform: {path.name}")
    if len({hashlib.sha256(path.read_bytes()).hexdigest() for path in frame_paths}) != 3:
        raise AssertionError("The three chooser states were not captured as distinct images.")
    return selected




def _capture_visible_window(
    xdotool: str,
    title: str,
    destination: Path,
    *,
    minimum_size: tuple[int, int] = (600, 400),
) -> None:
    identifier = window_id(xdotool, title)
    if identifier is None:
        raise TimeoutError(f"Window {title!r} was not visible for capture.")
    time.sleep(0.3)
    capture_window(
        xdotool, identifier, destination, minimum_size=minimum_size
    )


def _capture_modal(
    parent,
    opener,
    title: str,
    destination: Path,
    xdotool: str,
    *,
    minimum_size: tuple[int, int] = (600, 400),
    prepare=None,
) -> None:
    """Capture a real modal window and close it without invoking an action."""
    errors: list[str] = []
    existing_windows = {
        child.winfo_id()
        for child in parent.winfo_children()
        if isinstance(child, plugin_actions.tk.Toplevel)
    }

    def capture_and_close() -> None:
        dialog = None
        try:
            parent.update_idletasks()
            windows = [
                child for child in parent.winfo_children()
                if isinstance(child, plugin_actions.tk.Toplevel)
            ]
            dialog = next(
                (child for child in windows if child.title() == title),
                None,
            )
            if dialog is None:
                # A stale expected title must not strand opener() inside
                # wait_window(). Close the new modal and report the mismatch.
                dialog = next(
                    (
                        child for child in windows
                        if child.winfo_id() not in existing_windows
                    ),
                    None,
                )
                actual_title = dialog.title() if dialog is not None else "no new Tk modal"
                raise RuntimeError(
                    f"Expected modal {title!r}, but Tk created {actual_title!r}."
                )
            if prepare is not None:
                prepare(dialog)
            dialog.update_idletasks()
            dialog.update()
            _capture_visible_window(
                xdotool, title, destination, minimum_size=minimum_size
            )
        except (
            ImportError,
            KeyError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
            TimeoutError,
            plugin_actions.tk.TclError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            if dialog is not None:
                with contextlib.suppress(plugin_actions.tk.TclError):
                    dialog.destroy()

    parent.after(400, capture_and_close)
    opener()
    if errors:
        raise RuntimeError(f"Could not capture {title!r}: {errors[0]}")


def _make_themed_root(theme: str, title: str):
    plugin_actions._load_tkinter()
    root = plugin_actions.tk.Tk()
    os.environ["KICAD_BLOCKS_THEME"] = theme
    plugin_actions._configure_tk_theme(root)
    root.title(title)
    root.update_idletasks()
    root.update()
    return root


def _select_default_implementation(dialog) -> None:
    tree = next(
        (
            widget for widget in walk_widgets(dialog)
            if widget.winfo_class() == "Treeview"
        ),
        None,
    )
    if tree is None or "0" not in tree.get_children():
        raise RuntimeError("The implementations dialog does not contain its Default row.")
    tree.selection_set("0")
    tree.focus("0")
    tree.event_generate("<<TreeviewSelect>>")
    dialog.update_idletasks()
    dialog.update()

    buttons = [
        widget for widget in walk_widgets(dialog)
        if widget.winfo_class() == "TButton"
        and widget.cget("text") in {"Edit default", "Edit variant"}
    ]
    edit_button = next(
        (widget for widget in buttons if widget.cget("text") == "Edit default"),
        None,
    )
    if edit_button is None or not edit_button.instate(["!disabled"]):
        selection = tree.selection()
        state = [
            (widget.cget("text"), widget.cget("state"))
            for widget in buttons
        ]
        raise RuntimeError(
            "The Default row edit action is unavailable: "
            f"rows={tree.get_children()!r}, selection={selection!r}, buttons={state!r}."
        )


def _select_existing_pcb_file(dialog) -> None:
    combos = [
        widget for widget in walk_widgets(dialog)
        if widget.winfo_class() == "TCombobox"
    ]
    if len(combos) < 2:
        raise RuntimeError("Add PCB layout is missing its source and implementation selectors.")
    combos[0].set("Choose an existing PCB file")
    combos[0].event_generate("<<ComboboxSelected>>")
    dialog.update_idletasks()
    fields = [
        widget for widget in walk_widgets(dialog)
        if widget.winfo_class() in {"TEntry", "Entry"}
    ]
    choose_button = next(
        (
            widget for widget in walk_widgets(dialog)
            if widget.winfo_class() == "TButton" and widget.cget("text") == "Choose file…"
        ),
        None,
    )
    if (
        len(fields) < 2
        or not fields[1].winfo_ismapped()
        or choose_button is None
        or not choose_button.winfo_ismapped()
    ):
        raise RuntimeError("Selecting an existing PCB file did not reveal its file picker.")


def _assert_layout_manager_footer(dialog) -> None:
    labels = {"Save layouts", "Cancel"}
    visible = {
        widget.cget("text")
        for widget in walk_widgets(dialog)
        if widget.winfo_class() == "TButton" and widget.winfo_ismapped()
    }
    missing = sorted(labels - visible)
    if missing:
        raise RuntimeError("PCB layout manager footer buttons are not visible: " + ", ".join(missing))


def _assert_blank_pcb_file_picker_hidden(dialog) -> None:
    fields = [
        widget for widget in walk_widgets(dialog)
        if widget.winfo_class() in {"TEntry", "Entry"}
    ]
    choose_button = next(
        (
            widget for widget in walk_widgets(dialog)
            if widget.winfo_class() == "TButton" and widget.cget("text") == "Choose file…"
        ),
        None,
    )
    if len(fields) < 2 or fields[1].winfo_ismapped() or (
        choose_button is not None and choose_button.winfo_ismapped()
    ):
        raise RuntimeError("The blank-board mode should hide its PCB file entry and Choose file button.")
    _assert_add_layout_footer(dialog)


def _assert_add_layout_footer(dialog) -> None:
    labels = {"Add layout", "Cancel"}
    visible = {
        widget.cget("text")
        for widget in walk_widgets(dialog)
        if widget.winfo_class() == "TButton" and widget.winfo_ismapped()
    }
    missing = sorted(labels - visible)
    if missing:
        raise RuntimeError("Add PCB layout footer buttons are not visible: " + ", ".join(missing))


def capture_ui_workflows(library_roots, record, project_dir: Path, output_dir: Path) -> None:
    """Export actual app windows, using the same theme initialization as run_action."""
    from types import SimpleNamespace

    xdotool = find_executable("xdotool")
    original_theme = os.environ.get("KICAD_BLOCKS_THEME")
    original_library_override = os.environ.pop("KICAD_BLOCKS_LIBRARY", None)
    original_configured_roots = plugin_actions.configured_library_roots
    original_default_root = plugin_actions.default_library_root
    roots = [Path(item).resolve() for item in library_roots]
    plugin_actions.configured_library_roots = lambda: roots
    plugin_actions.default_library_root = lambda: roots[0]
    schematic = SimpleNamespace(get_selection=list)
    kicad = SimpleNamespace()

    try:
        for theme in ("light", "dark"):
            root = _make_themed_root(theme, "Functional Block Library")
            try:
                plugin_actions._show_library_browser(
                    root, schematic, kicad, project_dir, roots, "0x1"
                )
                root.update_idletasks()
                root.update()
                _capture_visible_window(
                    xdotool,
                    "Functional Block Library",
                    output_dir / f"library-browser-{theme}.png",
                    minimum_size=(1000, 650),
                )
                browser_entries = [
                    widget for widget in walk_widgets(root)
                    if widget.winfo_class() in {"TEntry", "Entry"}
                ]
                if not browser_entries:
                    raise RuntimeError("The production library browser search field is missing.")
                browser_entries[0].delete(0, "end")
                browser_entries[0].insert(0, "STUSB")
                root.update_idletasks()
                root.update()
                _capture_visible_window(
                    xdotool,
                    "Functional Block Library",
                    output_dir / f"library-browser-details-{theme}.png",
                    minimum_size=(1000, 650),
                )
                _capture_modal(
                    root,
                    lambda target=root: plugin_actions._show_library_roots_dialog(target),
                    "Manage Libraries",
                    output_dir / f"library-folders-{theme}.png",
                    xdotool,
                    minimum_size=(900, 550),
                    prepare=lambda dialog: _select_first_table_row(dialog),
                )
            finally:
                root.destroy()

            root = _make_themed_root(theme, "KiCad Blocks — Tasks")
            try:
                notebook = plugin_actions._show_action_hub(root)
                root.update_idletasks()
                root.update()
                _capture_visible_window(
                    xdotool,
                    "KiCad Blocks — Tasks",
                    output_dir / f"task-hub-{theme}.png",
                    minimum_size=(700, 500),
                )
                tabs = notebook.tabs()
                if len(tabs) != 3:
                    raise RuntimeError(
                        f"Expected all three task-hub tabs, found {len(tabs)}."
                    )
                for index, suffix in ((1, "edit-maintain"), (2, "checks-pcb")):
                    notebook.select(tabs[index])
                    root.update_idletasks()
                    root.update()
                    _capture_visible_window(
                        xdotool,
                        "KiCad Blocks — Tasks",
                        output_dir / f"task-hub-{suffix}-{theme}.png",
                        minimum_size=(700, 500),
                    )
            finally:
                root.destroy()

            # This dedicated GUI smoke test drives the production configuration
            # form, changes a named profile, and captures the rendered dialog
            # before submitting the single undoable schematic update.
            configuration_screenshot = output_dir / f"instance-configuration-{theme}.png"
            capture_environment = os.environ.copy()
            capture_environment["KICAD_BLOCKS_THEME"] = theme
            configuration_run = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tests" / "acceptance_instance_configuration_gui.py"),
                    "--screenshot",
                    str(configuration_screenshot),
                ],
                env=capture_environment,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            if "configuration gui passed" not in configuration_run.stdout.casefold():
                raise RuntimeError(
                    "Instance-configuration GUI capture did not report success: "
                    f"{configuration_run.stdout}\n{configuration_run.stderr}"
                )

            root = _make_themed_root(theme, "KiCad Blocks dialog fixture")
            try:
                _capture_modal(
                    root,
                    lambda target=root: plugin_actions._show_block_variant_editor(record, target),
                    f"Implementations — {record.name}",
                    output_dir / f"implementation-variants-{theme}.png",
                    xdotool,
                    minimum_size=(800, 500),
                    prepare=_select_default_implementation,
                )
                _capture_modal(
                    root,
                    lambda target=root: plugin_actions._show_block_layout_editor(record, target),
                    f"PCB layouts — {record.name}",
                    output_dir / f"pcb-layout-manager-{theme}.png",
                    xdotool,
                    minimum_size=(900, 600),
                    prepare=lambda dialog: (
                        _select_first_table_row(dialog),
                        _assert_layout_manager_footer(dialog),
                    ),
                )
                _capture_modal(
                    root,
                    lambda target=root: plugin_actions._ask_layout_details(target, record),
                    "Add PCB layout",
                    output_dir / f"add-pcb-layout-blank-{theme}.png",
                    xdotool,
                    minimum_size=(640, 460),
                    prepare=_assert_blank_pcb_file_picker_hidden,
                )
                _capture_modal(
                    root,
                    lambda target=root: plugin_actions._ask_layout_details(target, record),
                    "Add PCB layout",
                    output_dir / f"add-pcb-layout-file-{theme}.png",
                    xdotool,
                    minimum_size=(640, 460),
                    prepare=_select_existing_pcb_file,
                )
                _capture_modal(
                    root,
                    lambda target=root: plugin_actions._ask_implementation_details(
                        target,
                        value="5V",
                        source=str(record.implementation),
                    ),
                    "Implementation details",
                    output_dir / f"implementation-details-{theme}.png",
                    xdotool,
                    minimum_size=(600, 240),
                )
                _capture_modal(
                    root,
                    lambda target=root: plugin_actions._ask_create_block_details(
                        target, Path("stusb4500_reference.kicad_sch")
                    ),
                    "Create Functional Block",
                    output_dir / f"create-block-form-{theme}.png",
                    xdotool,
                    minimum_size=(560, 320),
                )
            finally:
                root.destroy()
    finally:
        plugin_actions.configured_library_roots = original_configured_roots
        plugin_actions.default_library_root = original_default_root
        if original_theme is None:
            os.environ.pop("KICAD_BLOCKS_THEME", None)
        else:
            os.environ["KICAD_BLOCKS_THEME"] = original_theme
        if original_library_override is not None:
            os.environ["KICAD_BLOCKS_LIBRARY"] = original_library_override


def _select_first_table_row(dialog) -> None:
    tree = next(
        (
            widget for widget in walk_widgets(dialog)
            if widget.winfo_class() == "Treeview"
        ),
        None,
    )
    if tree is None or not tree.get_children():
        raise RuntimeError("The table contains no rows to select for the UI capture.")
    first = tree.get_children()[0]
    tree.selection_set(first)
    tree.focus(first)
    tree.event_generate("<<TreeviewSelect>>")
    dialog.update_idletasks()
    dialog.update()


def build_gif(output_dir: Path) -> None:
    from PIL import Image

    names = ("block-picker-initial.png", "block-picker-filtered.png", "block-picker-profile.png")
    frames = [Image.open(output_dir / name).convert("RGB") for name in names]
    try:
        size = (max(frame.width for frame in frames), max(frame.height for frame in frames))
        normalized = []
        for frame in frames:
            canvas = Image.new("RGB", size, (248, 248, 248))
            canvas.paste(frame, ((size[0] - frame.width) // 2, (size[1] - frame.height) // 2))
            normalized.append(canvas)
        destination = output_dir / "block-picker-workflow.gif"
        normalized[0].save(
            destination, save_all=True, append_images=normalized[1:],
            duration=1100, loop=0, optimize=True, disposal=2,
        )
        with Image.open(destination) as gif:
            if gif.n_frames != 3:
                raise AssertionError(f"Expected three animation frames, found {gif.n_frames}.")
    finally:
        for frame in frames:
            frame.close()


def render_native_svgs(cli: str, record, output_dir: Path) -> None:
    symbol_dir = output_dir / "_symbol"
    schematic_dir = output_dir / "_schematic"
    symbol_dir.mkdir()
    schematic_dir.mkdir()
    # Preserve the editable source even if KiCad's renderer rejects it; the
    # always-uploaded CI artifact then contains the exact failing input.
    shutil.copy2(record.implementation, output_dir / "functional-block-implementation.kicad_sch")

    symbol_export = subprocess.run(
        [
            cli, "sym", "export", "svg", "--symbol", str(record.manifest["symbol_name"]),
            "--output", str(symbol_dir), str(record.symbol_library),
        ],
        check=False, capture_output=True, text=True, timeout=60,
    )
    if symbol_export.returncode:
        raise RuntimeError(
            f"KiCad symbol SVG export failed ({symbol_export.returncode}):\n"
            f"{symbol_export.stdout}\n{symbol_export.stderr}"
        )
    schematic_export = subprocess.run(
        [cli, "sch", "export", "svg", "--output", str(schematic_dir), str(record.implementation)],
        check=False, capture_output=True, text=True, timeout=60,
    )
    if schematic_export.returncode:
        raise RuntimeError(
            f"KiCad schematic SVG export failed ({schematic_export.returncode}):\n"
            f"{schematic_export.stdout}\n{schematic_export.stderr}"
        )
    symbols = sorted(symbol_dir.glob("*.svg"))
    schematics = sorted(schematic_dir.glob("*.svg"))
    if len(symbols) != 1 or not schematics:
        raise RuntimeError(f"KiCad SVG export failed: symbols={len(symbols)}, schematics={len(schematics)}")
    shutil.copy2(symbols[0], output_dir / "functional-block-facade.svg")
    shutil.copy2(schematics[0], output_dir / "functional-block-implementation.svg")
    for name in ("functional-block-facade.svg", "functional-block-implementation.svg"):
        content = (output_dir / name).read_text(encoding="utf-8")
        if "<svg" not in content or len(content) < 500:
            raise RuntimeError(f"KiCad exported an empty SVG: {name}")
    # Keep only the curated assets in the uploaded documentation bundle.
    shutil.rmtree(symbol_dir)
    shutil.rmtree(schematic_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cli = os.environ.get("KICAD_CLI") or find_executable("kicad-cli-nightly", "kicad-cli")

    import tempfile

    with tempfile.TemporaryDirectory(prefix="kicad-blocks-visual-docs-") as temporary:
        library = Path(temporary) / "library"
        sensor = create_block(
            library, "Sensor Input", parse_pins("SENSOR:input, GND:passive"),
            description="A second library entry used to demonstrate searchable filtering.",
        )
        reference_source = Path(temporary) / "stusb4500-reference.kicad_sch"
        generate_reference_schematic(reference_source)
        layout_source = Path(temporary) / "stusb4500-reference.kicad_pcb"
        layout_source.write_text("(kicad_pcb)\n", encoding="utf-8")
        target = create_block(
            library, "STUSB4500 USB-PD Sink",
            parse_pins(
                "VBUS:input, GND:passive, CC1:bidir, CC2:bidir, VSNK:output, "
                "SCL:input, SDA:bidir, ALERT#:output, RESET:input, VMCU:input"
            ),
            description=(
                "STUSB4500QTR USB Type-C PD sink reference circuit based on "
                "ST DS12499 Rev. 8 Figure 10."
            ),
            parameters=[{
                "name": "PDO_CURRENT_A", "type": "number", "default": "2.4",
                "description": "Current rating of the selected sink PDO in amperes.",
            }],
            variant_files=[
                {"value": "5V", "path": reference_source},
                {"value": "9V", "path": reference_source},
                {"value": "12V", "path": reference_source},
            ],
            layout_files=[{"name": "Reference PCB", "path": layout_source}],
            profiles=[
                {
                    "id": "pdo1_5v_2a4", "name": "5 V / 2.4 A (PDO1)",
                    "description": "Datasheet Table 19 example: fixed 5 V sink PDO.",
                    "variant": "5V", "parameters": {"PDO_CURRENT_A": "2.4"},
                },
                {
                    "id": "pdo2_9v_1a33", "name": "9 V / 1.33 A (PDO2)",
                    "description": "Datasheet Table 19 example: 9 V sink PDO.",
                    "variant": "9V", "parameters": {"PDO_CURRENT_A": "1.33"},
                },
                {
                    "id": "pdo3_12v_1a", "name": "12 V / 1.0 A (PDO3)",
                    "description": "Datasheet Table 19 example: 12 V sink PDO.",
                    "variant": "12V", "parameters": {"PDO_CURRENT_A": "1.0"},
                },
            ],
        )
        shutil.copy2(reference_source, target.implementation)
        manifest_path = target.path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["hashes"]["implementation.kicad_sch"] = hashlib.sha256(
            target.implementation.read_bytes()
        ).hexdigest()
        manifest["revision"] = revision_for_hashes(manifest["hashes"])
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        target.manifest.update(manifest)
        render_native_svgs(cli, target, output_dir)
        extra_library = Path(temporary) / "support-library"
        create_block(
            extra_library,
            "USB Protection",
            parse_pins("VBUS:input, GND:passive, USB_P:bidirectional, USB_N:bidirectional"),
            description="Input ESD protection and common-mode filtering.",
        )
        capture_ui_workflows([library, extra_library], target, Path(temporary), output_dir)
        capture_picker([target, sensor], output_dir)
        build_gif(output_dir)

    assets = {}
    for name in sorted(ASSET_NAMES):
        path = output_dir / name
        if not path.is_file() or not path.stat().st_size:
            raise FileNotFoundError(f"Expected generated asset is missing: {name}")
        assets[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "source_sha": os.environ.get("GITHUB_SHA", "local"),
        "generator": "tests/capture_visual_docs.py",
        "renderer": (
            "KiCad CLI and production Tk windows: all task-hub tabs, functional-block browser, "
            "Manage Libraries, instance configuration, implementation and PCB layout dialogs, "
            "creation form, and placement chooser"
        ),
        "example": {
            "component": "STUSB4500QTR",
            "manufacturer": "STMicroelectronics",
            "datasheet": "https://www.st.com/resource/en/datasheet/stusb4500.pdf",
            "datasheet_revision": "DS12499 Rev. 8",
            "reference": "Figure 10, page 26/42",
        },
        "assets": assets,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(
        f"kicad-visual-docs=passed asset_count={len(assets)} "
        f"source_sha={manifest['source_sha']} output={output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
