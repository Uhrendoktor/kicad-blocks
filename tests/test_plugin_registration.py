"""Verify KiCad itself discovers the installed schematic IPC plugin action."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "plugin" / "kicad-blocks"
PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadblocks"
INSTALLER = ROOT / "scripts" / "install_plugin.py"
sys.path.insert(0, str(PLUGIN_SOURCE))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_pins
from kicad_blocks.repository import (
    LIBRARY_CATALOG_NAME,
    create_block,
    register_project_symbol_library,
)
from kipy import KiCad


def _find_executable(*names: str) -> str:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(f"None of these executables is available: {', '.join(names)}")


def _windows(xdotool: str) -> list[tuple[str, str, str]]:
    result = subprocess.run(
        [xdotool, "search", "--onlyvisible", "--name", ".*"],
        capture_output=True,
        text=True,
        check=False,
    )
    windows = []
    for candidate in result.stdout.splitlines():
        if not candidate.strip().isdigit():
            continue
        title = subprocess.run(
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
        windows.append((candidate, title, window_class))
    return windows


def _wait_for_editor(
    process: subprocess.Popen,
    schematic: Path,
    xdotool: str,
    log_path: Path,
    timeout_s: float = 45.0,
) -> str:
    deadline = time.monotonic() + timeout_s
    setup_steps = 0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Eeschema exited before opening {schematic.name} "
                f"(exit {process.returncode}).\n{log_path.read_text(errors='replace')[-8000:]}"
            )
        windows = _windows(xdotool)
        setup = next(
            (
                item
                for item in windows
                if "kicad setup" in (item[1] + " " + item[2]).casefold()
            ),
            None,
        )
        if setup is not None and setup_steps < 12:
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
                item
                for item in reversed(windows)
                if schematic.stem.casefold() in item[1].casefold()
                and (
                    "schematic editor" in item[1].casefold()
                    or "eeschema" in item[2].casefold()
                )
            ),
            None,
        )
        if editor is not None:
            return editor[0]
        time.sleep(0.25)
    raise TimeoutError(
        f"Eeschema did not open {schematic.name}; process_alive={process.poll() is None}; "
        f"windows={_windows(xdotool)!r}; "
        f"log={log_path.read_text(errors='replace')[-8000:]}"
    )


def _dump_accessibility_tree(process_id: int) -> str:
    """Diagnostic-only probe of KiCad's native accessibility surface."""
    try:
        import xa11y
        app = xa11y.App.by_pid(process_id, timeout=5.0)
        tree = app.dump(max_depth=14)
    except Exception as exc:
        result = f"xa11y accessibility probe unavailable: {type(exc).__name__}: {exc}"
        print(result, flush=True)
        return result
    print("--- xa11y KiCad accessibility tree ---", flush=True)
    print(tree[:24000], flush=True)
    print("--- end xa11y accessibility tree ---", flush=True)
    return tree

def _wait_for_window_title(xdotool: str, predicate, timeout_s: float = 12.0) -> tuple[str, str, str]:
    deadline = time.monotonic() + timeout_s
    last_windows = []
    while time.monotonic() < deadline:
        last_windows = _windows(xdotool)
        match = next((item for item in reversed(last_windows) if predicate(item[1].casefold())), None)
        if match is not None:
            return match
        time.sleep(0.1)
    raise TimeoutError(f"Expected KiCad window did not appear; windows={last_windows!r}")


def _select_native_symbol_in_test_harness(xdotool: str, symbol_name: str) -> None:
    """Use X11 input only in this GUI-test driver, never in plugin runtime code."""
    chooser = _wait_for_window_title(
        xdotool, lambda title: "choose symbol" in title or "select symbol" in title
    )
    subprocess.run([xdotool, "windowfocus", "--sync", chooser[0]], check=True, capture_output=True, text=True)
    subprocess.run([xdotool, "type", "--clearmodifiers", "--delay", "12", symbol_name], check=True, capture_output=True, text=True)
    subprocess.run([xdotool, "key", "--clearmodifiers", "Return"], check=True, capture_output=True, text=True)


def _select_facade_in_test_harness(xdotool: str, symbol_name: str) -> None:
    """Complete the native Symbol Editor library-tree search in the X11 test session."""
    editor = _wait_for_window_title(xdotool, lambda title: "symbol editor" in title)
    subprocess.run([xdotool, "windowfocus", "--sync", editor[0]], check=True, capture_output=True, text=True)
    subprocess.run([xdotool, "key", "--clearmodifiers", "ctrl+l"], check=True, capture_output=True, text=True)
    subprocess.run([xdotool, "type", "--clearmodifiers", "--delay", "12", symbol_name], check=True, capture_output=True, text=True)
    time.sleep(0.35)
    subprocess.run([xdotool, "key", "--clearmodifiers", "Return"], check=True, capture_output=True, text=True)


def _wait_for_symbol_document(kicad, expected_lib_id: str, timeout_s: float = 8.0) -> None:
    from kipy.proto.common.types import DocumentType
    deadline = time.monotonic() + timeout_s
    observed = []
    while time.monotonic() < deadline:
        observed = []
        for document in kicad.get_open_documents(DocumentType.DOCTYPE_SYMBOL):
            identifier = getattr(document, "lib_id", None)
            if identifier is None:
                continue
            nickname = str(getattr(identifier, "library_nickname", "") or "")
            name = str(getattr(identifier, "entry_name", "") or "")
            if nickname and name:
                observed.append(f"{nickname}:{name}")
        if expected_lib_id in observed:
            return
        time.sleep(0.1)
    raise AssertionError(f"Symbol Editor did not report {expected_lib_id!r}; observed={observed!r}")

def _close_editor(process: subprocess.Popen, window_id: str, xdotool: str) -> None:
    subprocess.run(
        [xdotool, "windowclose", window_id],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        process.wait(timeout=8.0)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)


def _launch(
    eeschema: str,
    schematic: Path,
    env: dict[str, str],
    xdotool: str,
    log_path: Path,
) -> tuple[subprocess.Popen, str]:
    with log_path.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [eeschema, str(schematic)],
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
        )
    try:
        window_id = _wait_for_editor(process, schematic, xdotool, log_path)
        return process, window_id
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        raise


def main() -> None:
    eeschema = os.environ.get("KICAD_EESCHEMA") or _find_executable(
        "eeschema-nightly", "eeschema"
    )
    cli = os.environ.get("KICAD_CLI") or _find_executable(
        "kicad-cli-nightly", "kicad-cli"
    )
    xdotool = _find_executable("xdotool")
    if not os.environ.get("DISPLAY"):
        raise RuntimeError("Run this test under xvfb-run; DISPLAY is not set.")

    version_output = subprocess.run(
        [cli, "version"], capture_output=True, text=True, check=True
    ).stdout
    version_match = re.search(r"(\d+\.\d+)", version_output)
    if version_match is None:
        raise RuntimeError(f"Could not determine KiCad MAJOR.MINOR version: {version_output!r}")
    version = version_match.group(1)

    python_interpreter = shutil.which("python3")
    if not python_interpreter:
        raise FileNotFoundError("System python3 was not found for the KiCad plugin manager.")

    with tempfile.TemporaryDirectory(prefix="kicad-blocks-plugin-discovery-") as temp:
        root = Path(temp)
        home = root / "home"
        home.mkdir()
        documents_home = home / ".local" / "share"
        cache_home = home / ".cache"
        project = root / "project"
        project.mkdir()
        block = create_block(
            root / "source-library",
            "Plugin Registration Smoke Block",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
        )
        # The placement test must not rely on global/default symbol libraries
        # being installed in this isolated HOME. Mirror the production flow:
        # register this block's shared catalog in the opened project's table.
        (project / "placement-test.kicad_pro").write_text("{}\n", encoding="utf-8")
        schematic = project / "placement-test.kicad_sch"
        shutil.copy2(block.implementation, schematic)
        register_project_symbol_library(
            project,
            str(block.manifest["library_nickname"]),
            block.symbol_library,
            description="KiCad Blocks GUI placement test",
        )
        placement_symbol_name = str(block.manifest["symbol_name"])

        env = os.environ.copy()
        # Ensure the test does not inherit a runner-local pip mirror or
        # find-links override; KiCad must install from the published wheel URL
        # declared in the unmodified production requirements.txt.
        for key in (
            "PIP_FIND_LINKS",
            "PIP_NO_INDEX",
            "PIP_EXTRA_INDEX_URL",
            "PIP_INDEX_URL",
            "PIP_CONFIG_FILE",
        ):
            env.pop(key, None)
        env.update(
            {
                "HOME": str(home),
                # Force GTK/wxWidgets onto the virtual X11 display. Some CI
                # containers inherit a stale WAYLAND_DISPLAY from the runner.
                "GDK_BACKEND": "x11",
                "DISPLAY": os.environ["DISPLAY"],
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "KICAD_DOCUMENTS_HOME": str(documents_home),
                "KICAD_CACHE_HOME": str(cache_home),
                "KICAD_ENABLE_WXTRACE": "1",
                "WXTRACE": "KICAD_API",
                "KICAD_ALLOC_CONSOLE": "1",
            }
        )

        # Let KiCad create its version-specific first-run profile, and accept
        # its default setup wizard before enabling the API preference.
        first_log = root / "eeschema-first-run.log"
        first_process, first_window = _launch(
            eeschema, schematic, env, xdotool, first_log
        )
        _close_editor(first_process, first_window, xdotool)

        config_files = list(home.rglob("kicad_common.json"))
        if not config_files:
            raise FileNotFoundError(
                "Eeschema did not create kicad_common.json after first launch. "
                f"HOME contents: {[str(p.relative_to(home)) for p in home.rglob('*')]!r}"
            )
        common_path = next(
            (path for path in config_files if path.parent.name == version),
            config_files[0],
        )
        common = json.loads(common_path.read_text(encoding="utf-8"))
        api_settings = common.setdefault("api", {})
        api_settings["enable_server"] = True
        api_settings["interpreter_path"] = python_interpreter
        common_path.write_text(
            json.dumps(common, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Install through the same package installer users run. Keep the
        # production wheel URL unchanged: this test verifies the actual
        # binary-only requirements path used by KiCad on first discovery.
        subprocess.run(
            [
                sys.executable,
                str(INSTALLER),
                "--version",
                version,
                "--documents-home",
                str(documents_home),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        installed_plugin = (
            documents_home / "kicad" / version / "plugins" / PLUGIN_IDENTIFIER
        )
        plugin_manifest_path = installed_plugin / "plugin.json"
        manifest = json.loads(plugin_manifest_path.read_text(encoding="utf-8"))
        if manifest.get("runtime", {}).get("min_version") != "3.12":
            raise AssertionError(
                "Plugin metadata must retain the CPython 3.12 minimum; the requirements select the matching supported ABI wheel."
            )
        expected_schematic_actions = {
            "open-hub": ("entrypoints/action_hub.py", False),
            "manage-library": ("entrypoints/browse_blocks.py", True),
            "create-block": ("entrypoints/create_block.py", True),
            "open-implementation": ("entrypoints/open_implementation.py", True),
        }
        expected_action_ids = set(expected_schematic_actions) | {"place-imported-layout"}
        if {item["identifier"] for item in manifest["actions"]} != expected_action_ids:
            raise AssertionError(
                "The plugin must expose the task-hub menu command, three standalone "
                "schematic actions, and the PCB placement action."
            )
        for identifier, (entrypoint, show_button) in expected_schematic_actions.items():
            action = next(
                item for item in manifest["actions"] if item["identifier"] == identifier
            )
            if action.get("scopes") != ["schematic"]:
                raise AssertionError(f"{identifier} is not scoped to Eeschema: {action!r}")
            if action.get("show-button", False) is not show_button:
                raise AssertionError(f"Unexpected toolbar visibility for {identifier}: {action!r}")
            if action.get("entrypoint") != entrypoint or action.get("args", []) != []:
                raise AssertionError(f"Invalid no-argument entrypoint for {identifier}: {action!r}")
            if not (installed_plugin / entrypoint).is_file():
                raise FileNotFoundError(f"Installed action entrypoint is missing: {entrypoint}")
        pcb_action = next(
            item
            for item in manifest["actions"]
            if item["identifier"] == "place-imported-layout"
        )
        if pcb_action.get("scopes") != ["pcb"] or not pcb_action.get("show-button"):
            raise AssertionError(
                f"Imported-layout action is not enabled for PCB Editor: {pcb_action!r}"
            )
        if pcb_action.get("args", []) != []:
            raise AssertionError(
                "The PCB action must not pass its action identifier as a Python script argument."
            )
        pcb_entrypoint = installed_plugin / pcb_action["entrypoint"]
        if not pcb_entrypoint.is_file():
            raise FileNotFoundError(
                f"Installed PCB Editor action entrypoint is missing: {pcb_entrypoint}"
            )
        hub_entrypoint = installed_plugin / expected_schematic_actions["open-hub"][0]
        installed_requirements = (installed_plugin / "requirements.txt").read_text(encoding="utf-8")
        abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
        expected_wheel_url = (
            "https://github.com/Uhrendoktor/kicad-blocks/releases/download/"
            f"kicad-python-76b47d25/kicad_python-0.9.0.dev0-{abi}-{abi}-linux_x86_64.whl"
        )
        if expected_wheel_url not in installed_requirements or "git+" in installed_requirements:
            raise AssertionError(
                "Installed plugin requirements must use the published prebuilt wheel, "
                "not a source checkout."
            )
        plugin_json = plugin_manifest_path.resolve()
        log_path = root / "eeschema-plugin-discovery.log"
        process, window_id = _launch(eeschema, schematic, env, xdotool, log_path)
        placement_client = None
        try:
            environment = (
                cache_home / "kicad" / version / "python-environments" / PLUGIN_IDENTIFIER
            )
            env_python = environment / "bin" / "python"
            # A cold KiCad-managed environment may need several minutes to download
            # ABI-specific wheels (notably the CPython 3.14 Pillow wheel). Keep the
            # readiness assertion, but allow a realistic installation window.
            deadline = time.monotonic() + 420.0
            ready_log = False
            loaded_log = False
            last_log = ""
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Eeschema exited during plugin setup (exit {process.returncode}).\n"
                        f"{log_path.read_text(errors='replace')[-12000:]}"
                    )
                last_log = log_path.read_text(encoding="utf-8", errors="replace")
                folded = last_log.casefold()
                loaded_log = (
                    "manager: loading plugin from" in folded
                    and str(plugin_json).casefold() in folded
                )
                ready_log = f"manager: marking {PLUGIN_IDENTIFIER} as ready" in folded
                if ready_log and env_python.is_file():
                    break
                time.sleep(0.5)

            if not loaded_log or not ready_log or not env_python.is_file():
                raise TimeoutError(
                    "KiCad did not load and mark the installed KiCad Blocks action ready. "
                    f"plugin_json={plugin_json}; env_python={env_python}; "
                    f"loaded_trace={loaded_log}; ready_trace={ready_log}; "
                    f"log_tail={last_log[-14000:]}"
                )

            package_probe = subprocess.run(
                [
                    str(env_python),
                    "-c",
                    (
                        "import importlib.metadata, pathlib, sys, kipy; "
                        "from kipy import KiCad; "
                        "package_path = pathlib.Path(kipy.__file__).resolve(); "
                        "environment = pathlib.Path(sys.prefix).resolve(); "
                        "assert environment in package_path.parents, (package_path, environment); "
                        "print('kicad-python plugin environment ready', "
                        "importlib.metadata.version('kicad-python'), package_path)"
                    ),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            if "plugin environment ready" not in package_probe.stdout:
                raise AssertionError(package_probe.stdout)

            # Run the installed entrypoint's deterministic GUI smoke mode.
            # It builds the actual Tk widgets and asserts all three groups and
            # all task buttons without depending on X11 window-manager PIDs.
            hub_probe = subprocess.run(
                [str(env_python), str(hub_entrypoint), "--smoke-test"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=20.0,
            )
            if (
                hub_probe.returncode != 0
                or "task hub ui passed" not in hub_probe.stdout.casefold()
            ):
                raise RuntimeError(
                    "The KiCad Blocks task hub UI smoke test failed. "
                    f"exit={hub_probe.returncode}; "
                    f"stdout={hub_probe.stdout}; stderr={hub_probe.stderr}"
                )
            print(hub_probe.stdout.strip())

            # Exercise the picker in KiCad's managed plugin environment so
            # CairoSVG and the other declared plugin dependencies are available.
            # The managed interpreter shares the system Tk installation.
            try:
                picker_probe = subprocess.run(
                    [str(env_python), str(ROOT / "tests" / "test_library_picker_gui.py")],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=60.0,
                )
            except subprocess.TimeoutExpired as exc:
                partial_stdout = exc.stdout or ""
                partial_stderr = exc.stderr or ""
                if isinstance(partial_stdout, bytes):
                    partial_stdout = partial_stdout.decode("utf-8", errors="replace")
                if isinstance(partial_stderr, bytes):
                    partial_stderr = partial_stderr.decode("utf-8", errors="replace")
                raise TimeoutError(
                    "The KiCad-managed block-picker smoke test exceeded 60 seconds. "
                    f"Partial stdout: {partial_stdout[-4000:]!r}; "
                    f"partial stderr: {partial_stderr[-4000:]!r}"
                ) from exc
            if "kicad-native svg facade preview passed" not in picker_probe.stdout.casefold():
                raise AssertionError(
                    "The native-rendered Tk picker smoke test did not report success. "
                    f"stdout: {picker_probe.stdout}; stderr: {picker_probe.stderr}"
                )

            # Run the additional GUI smoke test in the same managed plugin
            # environment as KiCad's installed entrypoints so every declared
            # runtime dependency is available.
            configuration_probe = subprocess.run(
                [
                    str(env_python),
                    str(ROOT / "tests" / "test_instance_configuration_gui.py"),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=20.0,
            )
            if configuration_probe.returncode != 0:
                raise RuntimeError(
                    "The instance configuration GUI smoke test failed "
                    f"(exit {configuration_probe.returncode}). "
                    f"stdout: {configuration_probe.stdout}; stderr: {configuration_probe.stderr}"
                )
            if "instance configuration gui passed" not in configuration_probe.stdout.casefold():
                raise AssertionError(
                    "The instance configuration GUI smoke test did not report success. "
                    f"stdout: {configuration_probe.stdout}; stderr: {configuration_probe.stderr}"
                )
            print(picker_probe.stdout.strip())

            # Edit Facade must open the exact shared library symbol rendered
            # in the browser preview, even when the active schematic contains
            # no placed instance of that block.
            facade_editor_client = KiCad(timeout_ms=30000)
            _dump_accessibility_tree(process.pid)
            try:
                live_schematic = facade_editor_client.get_schematic()
                if live_schematic is None:
                    raise RuntimeError("Live Eeschema IPC returned no schematic for Edit Facade.")
                live_selection = plugin_actions._safe_schematic_selection(live_schematic)
                if live_selection:
                    raise AssertionError(
                        "Edit Facade GUI regression requires an empty schematic selection; "
                        f"found {len(live_selection)} selected item(s)."
                    )
                refreshed_record = plugin_actions._register_block(
                    project,
                    block,
                    facade_editor_client,
                    [block.path.parent],
                )
                if not plugin_actions._LAST_LIBRARY_RELOAD_OK:
                    raise RuntimeError(
                        "Could not reload the generated KBlocks project catalog before "
                        "opening Edit Facade: "
                        + str(plugin_actions._LAST_LIBRARY_RELOAD_WARNING)
                    )
                opened = plugin_actions._edit_facade_symbol(
                    refreshed_record,
                    facade_editor_client,
                    project_catalog=project / ".kicad_blocks" / LIBRARY_CATALOG_NAME,
                )
                if opened:
                    raise AssertionError(
                        "Edit Facade must leave library-tree selection to KiCad's native UI; "
                        "the plugin must not use platform-specific keyboard injection."
                    )
                _select_facade_in_test_harness(xdotool, placement_symbol_name)
                expected_facade_id = f"{refreshed_record.manifest['library_nickname']}:{placement_symbol_name}"
                _wait_for_symbol_document(facade_editor_client, expected_facade_id)
                editor_windows = _windows(xdotool)
                matching_editor = [
                    item for item in editor_windows
                    if item[0] != window_id and "symbol editor" in item[1].casefold()
                ]
                if not matching_editor:
                    raise AssertionError(
                        "Edit Facade did not leave a Symbol Editor window open for "
                        f"{expected_facade_id}; visible_windows={editor_windows!r}"
                    )
                print(
                    "Edit Facade opened the generated project library symbol "
                    f"{refreshed_record.manifest['library_nickname']}:{placement_symbol_name} with no placed schematic instance."
                )
            finally:
                facade_editor_client.close()

            placement_client = KiCad(timeout_ms=30000)
            plugin_actions._start_native_symbol_placement(
                placement_client,
                None,
                placement_symbol_name,
            )
            _select_native_symbol_in_test_harness(xdotool, placement_symbol_name)
            placement_focus = subprocess.run(
                [xdotool, "getwindowfocus"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if placement_focus != window_id:
                raise AssertionError(
                    f"Selecting {placement_symbol_name} did not return focus to Eeschema's "
                    f"interactive placement tool; focused={placement_focus!r}; "
                    f"visible_windows={_windows(xdotool)!r}"
                )

            # Commit the facade to the active sheet, leave the placement tool,
            # and save the real schematic so the test proves a block was placed
            # rather than merely proving that the symbol chooser opened.
            geometry_result = subprocess.run(
                [xdotool, "getwindowgeometry", "--shell", window_id],
                capture_output=True,
                text=True,
                check=True,
            )
            geometry = {
                key: value
                for key, value in (
                    line.split("=", 1)
                    for line in geometry_result.stdout.splitlines()
                    if "=" in line
                )
            }
            editor_width = int(geometry["WIDTH"])
            editor_height = int(geometry["HEIGHT"])
            # Use a point relative to the actual editor window, inside the
            # drawing canvas. A fixed root-screen coordinate can land outside
            # Eeschema when Python-version-specific GTK window sizes differ.
            placement_x = str(editor_width // 2)
            placement_y = str((editor_height * 2) // 3)
            subprocess.run(
                [
                    xdotool,
                    "mousemove",
                    "--sync",
                    "--window",
                    window_id,
                    placement_x,
                    placement_y,
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
            time.sleep(0.5)
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
            expected_lib_id = (
                f'{block.manifest["library_nickname"]}:{placement_symbol_name}'
            )
            save_deadline = time.monotonic() + 8.0
            saved_schematic = ""
            while time.monotonic() < save_deadline:
                saved_schematic = schematic.read_text(encoding="utf-8")
                if expected_lib_id in saved_schematic:
                    break
                time.sleep(0.25)
            if expected_lib_id not in saved_schematic:
                focus = subprocess.run(
                    [xdotool, "getwindowfocus"],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.strip()
                mouse = subprocess.run(
                    [xdotool, "getmouselocation", "--shell"],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.strip()
                raise AssertionError(
                    f"Saved Eeschema file does not contain the placed block facade "
                    f"{expected_lib_id!r}. editor_geometry={geometry_result.stdout!r}; "
                    f"focus={focus!r}; mouse={mouse!r}; "
                    f"saved_tail={saved_schematic[-2500:]!r}."
                )
            placement_svg = root / "placed-block-svg"
            placement_svg.mkdir()
            subprocess.run(
                [
                    cli,
                    "sch",
                    "export",
                    "svg",
                    "--output",
                    str(placement_svg),
                    str(schematic),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            if not list(placement_svg.glob("*.svg")):
                raise AssertionError("KiCad CLI could not parse the schematic containing the placed block.")
            print(
                f"Functional Block Library placement helper selected and placed "
                f"{expected_lib_id}; the saved schematic passed KiCad CLI export."
            )

            print(
                "KiCad plugin registration passed: plugin.json discovered, "
                "managed Python environment created, and schematic action marked ready."
            )
        finally:
            if placement_client is not None:
                placement_client.close()
            _close_editor(process, window_id, xdotool)


if __name__ == "__main__":
    main()
