"""Launch a real Eeschema window under a virtual X server and save the block file."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import (
    create_block,
    link_instance_to_local_copy,
    make_local_copy,
    read_manifest,
)


def _find_executable(*names: str) -> str:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(f"None of these executables is available: {', '.join(names)}")


def main() -> None:
    """Open a block's implementation in the actual GUI and save it."""
    eeschema = os.environ.get("KICAD_EESCHEMA") or _find_executable(
        "eeschema-nightly", "eeschema"
    )
    xdotool = _find_executable("xdotool")
    if not os.environ.get("DISPLAY"):
        raise RuntimeError(
            "The Eeschema GUI smoke test must run inside xvfb-run so xdotool and "
            "Eeschema share the same DISPLAY."
        )

    with tempfile.TemporaryDirectory(prefix="kicad-blocks-gui-") as temp:
        root = Path(temp)
        home = root / "home"
        home.mkdir()
        library = root / "library"
        block = create_block(
            library,
            "Eeschema GUI Smoke Block",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
        )
        implementation = block.implementation
        before = implementation.read_bytes()
        project_dir = root / "project"
        project_dir.mkdir()

        # Bind a separate, already-existing local instance before editing the
        # shared block. The later Publish -> Sync test must update this copy,
        # not create a new one after the library edit.
        local_instance_id = "00000000-0000-0000-0000-000000000def"
        local_copy = make_local_copy(block, project_dir)
        link_instance_to_local_copy(project_dir, local_instance_id, local_copy)
        local_before = local_copy.implementation.read_bytes()

        log_path = root / "eeschema.log"
        selected_instance = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000abc",
            definition=SimpleNamespace(
                id=SimpleNamespace(
                    library=block.manifest["library_nickname"],
                    name=block.manifest["symbol_name"],
                )
            ),
        )
        schematic = SimpleNamespace(get_selection=lambda: [selected_instance])
        kicad = SimpleNamespace(get_kicad_binary_path=lambda name: eeschema)
        parent_window = object()
        silent_messagebox = SimpleNamespace(showinfo=lambda *args, **kwargs: None)
        window_id = None
        deadline = time.monotonic() + 35.0
        with log_path.open("w", encoding="utf-8") as log_stream:
            # Exercise the plugin's actual selected-library-instance edit
            # helper. The fake instance supplies a real catalog library ID;
            # process creation and the GUI itself are not mocked.
            with (
                patch.dict(
                    os.environ,
                    {
                        "HOME": str(home),
                        "XDG_CONFIG_HOME": str(home / ".config"),
                        "KICAD_BLOCKS_GUI_TEST": "1",
                    },
                ),
                patch.object(plugin_actions, "messagebox", silent_messagebox),
            ):
                process = plugin_actions._edit_block_implementation(
                    library,
                    schematic,
                    kicad,
                    root / "project",
                    parent_window,
                )
            if process.args != [eeschema, str(implementation)]:
                raise AssertionError(
                    f"Plugin edit action opened unexpected command: {process.args!r}"
                )
            try:
                setup_steps = 0
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log_stream.flush()
                        output = log_path.read_text(encoding="utf-8")
                        raise RuntimeError(
                            f"Eeschema exited before opening the implementation "
                            f"(exit {process.returncode}). Output:\\n{output[-6000:]}"
                        )
                    search = subprocess.run(
                        [xdotool, "search", "--onlyvisible", "--name", ".*"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    windows = []
                    for candidate in search.stdout.splitlines():
                        if not candidate.strip().isdigit():
                            continue
                        name = subprocess.run(
                            [xdotool, "getwindowname", candidate],
                            capture_output=True,
                            text=True,
                            check=False,
                        ).stdout.strip()
                        window_class = subprocess.run(
                            [xdotool, "getwindowclassname", candidate],
                            capture_output=True,
                            text=True,
                            check=False,
                        ).stdout.strip()
                        windows.append((candidate, name, window_class))

                    setup = next(
                        (
                            item for item in windows
                            if "kicad setup" in (item[1] + " " + item[2]).casefold()
                        ),
                        None,
                    )
                    if setup is not None and setup_steps < 12:
                        # KiCad shows a first-run setup wizard in a clean HOME.
                        # Accept its default choices to let the requested
                        # implementation schematic finish opening.
                        subprocess.run(
                            [xdotool, "windowfocus", "--sync", setup[0]],
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        subprocess.run(
                            [xdotool, "key", "--clearmodifiers", "Tab"],
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        subprocess.run(
                            [xdotool, "key", "--clearmodifiers", "Return"],
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        setup_steps += 1
                        time.sleep(0.75)
                        continue

                    editor = next(
                        (
                            item for item in reversed(windows)
                            if implementation.stem.casefold() in item[1].casefold()
                            and (
                                "schematic editor" in item[1].casefold()
                                or "eeschema" in item[2].casefold()
                            )
                        ),
                        None,
                    )
                    if editor is not None:
                        window_id = editor[0]
                        break
                    time.sleep(0.25)

                if window_id is None:
                    log_stream.flush()
                    output = log_path.read_text(encoding="utf-8")
                    search_all = subprocess.run(
                        [xdotool, "search", "--name", ".*"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    window_diagnostics = []
                    for candidate in search_all.stdout.splitlines():
                        if not candidate.strip().isdigit():
                            continue
                        name = subprocess.run(
                            [xdotool, "getwindowname", candidate],
                            capture_output=True,
                            text=True,
                            check=False,
                        ).stdout.strip()
                        window_class = subprocess.run(
                            [xdotool, "getwindowclassname", candidate],
                            capture_output=True,
                            text=True,
                            check=False,
                        ).stdout.strip()
                        pid = subprocess.run(
                            [xdotool, "getwindowpid", candidate],
                            capture_output=True,
                            text=True,
                            check=False,
                        ).stdout.strip()
                        window_diagnostics.append(
                            {"id": candidate, "title": name, "class": window_class, "pid": pid}
                        )
                    geometry = subprocess.run(
                        ["xdotool", "getdisplaygeometry"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    raise TimeoutError(
                        "Eeschema did not open the requested implementation in a visible editor "
                        "window within 35 seconds. "
                        f"Process alive={process.poll() is None}; "
                        f"DISPLAY={os.environ.get('DISPLAY')!r}; "
                        f"geometry={geometry.stdout.strip()!r}; "
                        f"setup_steps={setup_steps}; windows={window_diagnostics!r}; "
                        f"search_stderr={search_all.stderr.strip()!r}. Output:\\n{output[-6000:]}"
                    )

                subprocess.run(
                    [xdotool, "windowfocus", "--sync", window_id],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                # Make a real graphical edit: place a global label through
                # Eeschema's interactive drawing tool, rather than editing the
                # S-expression from Python or IPC.
                edit_label = "GUI_EDIT_PROBE"
                subprocess.run(
                    [xdotool, "key", "--clearmodifiers", "ctrl+l"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                time.sleep(0.5)
                dialog_search = subprocess.run(
                    [xdotool, "search", "--onlyvisible", "--name", ".*"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                dialog_id = None
                visible_windows = []
                for candidate in dialog_search.stdout.splitlines():
                    if not candidate.strip().isdigit():
                        continue
                    title = subprocess.run(
                        [xdotool, "getwindowname", candidate],
                        capture_output=True,
                        text=True,
                        check=False,
                    ).stdout.strip()
                    visible_windows.append((candidate, title))
                    if "global label" in title.casefold():
                        dialog_id = candidate
                if dialog_id is None:
                    raise RuntimeError(
                        "Ctrl+L did not open the Global Label Properties dialog. "
                        f"Visible windows: {visible_windows!r}"
                    )
                subprocess.run(
                    [xdotool, "windowfocus", "--sync", dialog_id],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [xdotool, "key", "--clearmodifiers", "ctrl+a"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [xdotool, "type", "--clearmodifiers", "--delay", "25", edit_label],
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
                time.sleep(0.5)
                # The global-label dialog may have stolen the active window.
                # Restore focus to the editor before moving/clicking in its canvas.
                subprocess.run(
                    [xdotool, "windowfocus", "--sync", window_id],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                geometry = subprocess.run(
                    [xdotool, "getwindowgeometry", "--shell", window_id],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                dimensions = {
                    key: int(value)
                    for line in geometry.splitlines()
                    if "=" in line
                    for key, value in [line.split("=", 1)]
                    if key in {"X", "Y", "WIDTH", "HEIGHT"}
                }
                if not {"X", "Y", "WIDTH", "HEIGHT"}.issubset(dimensions):
                    raise RuntimeError(f"Could not read Eeschema window geometry: {geometry!r}")
                # Use absolute desktop coordinates derived from the actual window
                # origin, rather than mixing window-relative canvas coordinates
                # with the outer-frame dimensions.
                target_x = dimensions["X"] + int(dimensions["WIDTH"] * 0.55)
                target_y = dimensions["Y"] + int(dimensions["HEIGHT"] * 0.55)
                subprocess.run(
                    [
                        xdotool,
                        "mousemove",
                        "--sync",
                        str(target_x),
                        str(target_y),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [xdotool, "click", "1"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                time.sleep(0.35)
                subprocess.run(
                    [xdotool, "key", "--clearmodifiers", "Escape"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [xdotool, "key", "--clearmodifiers", "ctrl+s"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                time.sleep(1.0)

                if not implementation.is_file() or not implementation.stat().st_size:
                    raise AssertionError("Eeschema removed or emptied the implementation schematic.")
                if edit_label not in implementation.read_text(encoding="utf-8"):
                    raise AssertionError(
                        "The global label placed through the Eeschema GUI was not saved "
                        f"to the implementation ({edit_label!r} missing)."
                    )

                subprocess.run(
                    [xdotool, "windowclose", window_id],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                try:
                    process.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5.0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5.0)

        if implementation.read_bytes() == before:
            raise AssertionError("The Eeschema GUI session did not persist a content edit.")
        edited_source = implementation.read_text(encoding="utf-8")
        if edit_label not in edited_source:
            raise AssertionError("The GUI-edited label was lost before Publish.")

        # Invoke the same Publish helper used by the plugin's manager action.
        # The existing local instance deliberately remains on its old baseline
        # until the following Sync action.
        reload_calls = []

        def reload_library(*, type, scope, nicknames):
            reload_calls.append((type, scope, tuple(nicknames)))

        def wait_for_libraries(*, types, scope=None, timeout_s=None):
            return None

        kicad_runtime = SimpleNamespace(
            get_kicad_binary_path=lambda name: eeschema,
            reload_library=reload_library,
            wait_for_libraries=wait_for_libraries,
        )
        publish_messagebox = SimpleNamespace(showinfo=lambda *args, **kwargs: None)
        version_before_publish = read_manifest(block.path)["version"]
        with (
            patch.object(plugin_actions, "_pick_record", return_value=block),
            patch.object(plugin_actions, "messagebox", publish_messagebox),
        ):
            plugin_actions._publish_block(
                library,
                kicad_runtime,
                project_dir,
                parent_window,
            )
        published_manifest = read_manifest(block.path)
        if published_manifest["version"] <= version_before_publish:
            raise AssertionError("Plugin Publish did not advance the library block revision.")
        if "implementation.kicad_sch" not in published_manifest["hashes"]:
            raise AssertionError("Published library manifest does not hash the edited implementation.")
        if edit_label not in block.implementation.read_text(encoding="utf-8"):
            raise AssertionError("Plugin Publish lost the Eeschema GUI edit.")
        if local_copy.implementation.read_bytes() != local_before:
            raise AssertionError("Publishing the library unexpectedly modified the existing local copy.")
        if not reload_calls:
            raise AssertionError("Plugin Publish did not refresh the stable KiCad symbol catalog.")

        # Invoke the same selected-instance Sync helper used by the plugin.
        # This KIID was bound to the project-local copy before the GUI edit.
        local_instance = SimpleNamespace(
            id=local_instance_id,
            definition=SimpleNamespace(
                id=SimpleNamespace(
                    library=block.manifest["library_nickname"],
                    name=block.manifest["symbol_name"],
                )
            ),
        )
        local_schematic = SimpleNamespace(get_selection=lambda: [local_instance])
        sync_messagebox = SimpleNamespace(showinfo=lambda *args, **kwargs: None)
        with patch.object(plugin_actions, "messagebox", sync_messagebox):
            sync_result = plugin_actions.sync_selected_local(
                local_schematic,
                kicad_runtime,
                project_dir,
            )
        if "implementation.kicad_sch" not in sync_result["pulled"]:
            raise AssertionError(
                f"Plugin Sync did not pull the published GUI edit: {sync_result!r}"
            )
        if edit_label not in local_copy.implementation.read_text(encoding="utf-8"):
            raise AssertionError("The pre-existing local copy did not receive the published GUI edit.")
        if read_manifest(local_copy.path)["source_revision"] != published_manifest["revision"]:
            raise AssertionError("The local sync baseline was not advanced to the published revision.")
        print(
            "Eeschema GUI edit -> plugin Publish -> existing KIID-bound local copy Sync passed."
        )

        # Re-parse the shared and synchronized local implementation after their
        # GUI edit and synchronization to ensure both remain valid KiCad files.
        # schematic document.
        cli = os.environ.get("KICAD_CLI") or _find_executable(
            "kicad-cli-nightly", "kicad-cli"
        )
        output_dir = root / "svg"
        output_dir.mkdir()
        subprocess.run(
            [
                cli,
                "sch",
                "export",
                "svg",
                "--output",
                str(output_dir),
                str(implementation),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if not list(output_dir.glob("*.svg")):
            raise AssertionError("KiCad CLI could not export the implementation after GUI save.")
        local_output = root / "local-svg"
        local_output.mkdir()
        subprocess.run(
            [
                cli,
                "sch",
                "export",
                "svg",
                "--output",
                str(local_output),
                str(local_copy.implementation),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if not list(local_output.glob("*.svg")):
            raise AssertionError("KiCad CLI could not export the synchronized local implementation.")
        print("KiCad CLI re-parsed both published and synchronized implementations.")


if __name__ == "__main__":
    main()
