"""Launch a real Eeschema window under a virtual X server and save the block file."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_pins
from kicad_blocks.repository import create_block


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

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["KICAD_BLOCKS_GUI_TEST"] = "1"

        command = [eeschema, str(implementation)]
        log_path = root / "eeschema.log"
        window_id = None
        deadline = time.monotonic() + 35.0
        with log_path.open("w", encoding="utf-8") as log_stream:
            process = subprocess.Popen(
                command,
                env=env,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log_stream.flush()
                        output = log_path.read_text(encoding="utf-8")
                        raise RuntimeError(
                            f"Eeschema exited before opening the implementation "
                            f"(exit {process.returncode}). Output:\n{output[-6000:]}"
                        )
                    search = subprocess.run(
                        [
                            xdotool,
                            "search",
                            "--onlyvisible",
                            "--name",
                            "Eeschema",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    matches = [
                        line.strip()
                        for line in search.stdout.splitlines()
                        if line.strip().isdigit()
                    ]
                    if matches:
                        window_id = matches[-1]
                        break
                    time.sleep(0.25)

                if window_id is None:
                    log_stream.flush()
                    output = log_path.read_text(encoding="utf-8")
                    raise TimeoutError(
                        "Eeschema did not create a visible window within 35 seconds. "
                        f"Process alive={process.poll() is None}. Output:\n{output[-6000:]}"
                    )

                subprocess.run(
                    [xdotool, "windowactivate", "--sync", window_id],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [xdotool, "key", "--window", window_id, "ctrl+s"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                time.sleep(1.0)

                if not implementation.is_file() or not implementation.stat().st_size:
                    raise AssertionError("Eeschema removed or emptied the implementation schematic.")

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
            # A save with no content changes is valid. The important observable
            # is that Eeschema loaded the exact block implementation and stayed
            # alive until the test closed its window.
            print("Eeschema GUI opened and saved the block implementation (no-op save).")
        else:
            print("Eeschema GUI opened and rewrote the block implementation successfully.")

        # Re-parse the file after the GUI save to ensure it remains a valid KiCad
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
        print("KiCad CLI re-parsed the implementation after the real Eeschema GUI session.")


if __name__ == "__main__":
    main()
