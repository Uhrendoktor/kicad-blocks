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
INSTALLER = ROOT / "scripts" / "install_plugin.py"
sys.path.insert(0, str(PLUGIN_SOURCE))

from kicad_blocks.model import parse_pins
from kicad_blocks.repository import create_block


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

    wheelhouse_value = os.environ.get("KICAD_BLOCKS_WHEELHOUSE")
    if not wheelhouse_value:
        raise RuntimeError("KICAD_BLOCKS_WHEELHOUSE must point to the CI wheelhouse.")
    wheelhouse = Path(wheelhouse_value).expanduser().resolve()
    if not list(wheelhouse.glob("kicad_python-*.whl")):
        raise FileNotFoundError(f"The KiCad Python development wheel is missing in {wheelhouse}.")

    python_interpreter = shutil.which("python3")
    if not python_interpreter:
        raise FileNotFoundError("System python3 was not found for the KiCad plugin manager.")

    with tempfile.TemporaryDirectory(prefix="kicad-blocks-plugin-discovery-") as temp:
        root = Path(temp)
        home = root / "home"
        home.mkdir()
        documents_home = home / ".local" / "share" / "KiCad"
        cache_home = home / ".cache" / "KiCad" / version
        project = root / "project"
        project.mkdir()
        block = create_block(
            root / "source-library",
            "Plugin Registration Smoke Block",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
        )
        schematic = block.implementation

        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
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

        # Install through the same package installer users run. Replace only
        # the test copy's requirements with local CI wheels to keep the test
        # deterministic and avoid another GitLab source build.
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
            documents_home / "kicad" / version / "plugins" / "kicad-blocks"
        )
        plugin_manifest_path = installed_plugin / "plugin.json"
        manifest = json.loads(plugin_manifest_path.read_text(encoding="utf-8"))
        action = next(
            item
            for item in manifest["actions"]
            if item["identifier"] == "kicad-blocks-manager"
        )
        if action.get("scopes") != ["schematic"] or not action.get("show-button"):
            raise AssertionError(
                f"Installed action is not enabled for the Schematic Editor: {action!r}"
            )
        (installed_plugin / "requirements.txt").write_text(
            "--no-index\n"
            f"--find-links {wheelhouse}\n"
            "kicad-python==0.9.0.dev0\n",
            encoding="utf-8",
        )

        plugin_json = plugin_manifest_path.resolve()
        log_path = root / "eeschema-plugin-discovery.log"
        process, window_id = _launch(eeschema, schematic, env, xdotool, log_path)
        try:
            environment = cache_home / "python-environments" / "kicad-blocks"
            env_python = environment / "bin" / "python"
            deadline = time.monotonic() + 180.0
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
                ready_log = "manager: marking kicad-blocks as ready" in folded
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
                    "from kipy import KiCad; print('kicad-python plugin environment ready')",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            if "plugin environment ready" not in package_probe.stdout:
                raise AssertionError(package_probe.stdout)
            print(
                "KiCad plugin registration passed: plugin.json discovered, "
                "managed Python environment created, and schematic action marked ready."
            )
        finally:
            _close_editor(process, window_id, xdotool)


if __name__ == "__main__":
    main()
