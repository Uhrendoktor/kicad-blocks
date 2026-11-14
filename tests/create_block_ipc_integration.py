"""Exercise Create Block from Sheet against a live KiCad schematic editor."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(ROOT / "tests"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.model import parse_pins, render_implementation_schematic
from kicad_blocks.repository import (
    LIBRARY_CATALOG_NAME,
    LIBRARY_NICKNAME,
    list_blocks,
)
from kipy import KiCad
from kipy.errors import ApiError
from pynng.exceptions import NNGException
from test_plugin_registration import _close_editor, _find_executable, _launch


def _find_cli() -> str:
    configured = os.environ.get("KICAD_CLI")
    candidates = [configured] if configured else []
    candidates.extend(("kicad-cli-nightly", "kicad-cli"))
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise RuntimeError("KiCad CLI was not found; set KICAD_CLI or add it to PATH.")


def _schematic_sheet(
    *,
    project_name: str,
    root_uuid: str,
    sheet_uuid: str,
    name: str,
    filename: str,
    pins: tuple[tuple[str, str], ...],
    page: str,
    x: int,
) -> str:
    """Return a real KiCad hierarchical sheet symbol with typed interface pins."""
    pin_lines = []
    for index, (pin_name, electrical_type) in enumerate(pins):
        pin_uuid = str(uuid.uuid4())
        pin_y = 90 + index * 10
        pin_lines.append(
            f'    (pin "{pin_name}" {electrical_type} (at {x} {pin_y} 180) '
            f'(effects (font (size 1.27 1.27)) (justify right)) '
            f'(uuid "{pin_uuid}"))'
        )
    return "\n".join(
        [
            "  (sheet",
            f"    (at {x} 80)",
            "    (size 50 40)",
            "    (fields_autoplaced)",
            "    (stroke (width 0.1524) (type solid))",
            "    (fill (color 0 0 0 0))",
            f'    (uuid "{sheet_uuid}")',
            (
                f'    (property "Sheetname" "{name}" (at {x} 77 0) '
                '(effects (font (size 1.27 1.27)) (justify left bottom)))'
            ),
            (
                f'    (property "Sheetfile" "{filename}" (at {x} 80 0) '
                '(effects (font (size 1.27 1.27)) (justify left bottom)))'
            ),
            *pin_lines,
            "    (instances",
            f'      (project "{project_name}"',
            f'        (path "/{root_uuid}" (page "{page}"))',
            "      )",
            "    )",
            "  )",
        ]
    )


def _write_source_sheets(project_dir: Path) -> tuple[Path, dict[str, Path]]:
    project_name = "CreateBlockFromSheetIPC"
    root_uuid = str(uuid.uuid4())
    sheet_specs = (
        (
            "Power Stage",
            "sheets/power_stage.kicad_sch",
            "VIN:input, GND:passive, VOUT:output",
            (("VIN", "input"), ("GND", "passive"), ("VOUT", "output")),
            "2",
            80,
        ),
        (
            "Control Interface",
            "sheets/control_interface.kicad_sch",
            "SCL:input, SDA:bidirectional, ALERT:output",
            (("SCL", "input"), ("SDA", "bidirectional"), ("ALERT", "output")),
            "3",
            160,
        ),
    )
    sheet_entries = [
        (spec, str(uuid.uuid4()))
        for spec in sheet_specs
    ]
    sheets_directory = project_dir / "sheets"
    sheets_directory.mkdir(parents=True)
    parent_path = project_dir / f"{project_name}.kicad_sch"
    parent_content = [
        "(kicad_sch",
        "  (version 20250114)",
        '  (generator "kicad_blocks_create_sheet_test")',
        f'  (uuid "{root_uuid}")',
        '  (paper "A4")',
        f'  (title_block (title "{project_name}"))',
        "  (lib_symbols)",
    ]
    for spec, sheet_uuid in sheet_entries:
        name, relative_path, _pin_spec, pins, page, x = spec
        parent_content.append(
            _schematic_sheet(
                project_name=project_name,
                root_uuid=root_uuid,
                sheet_uuid=sheet_uuid,
                name=name,
                filename=relative_path,
                pins=pins,
                page=page,
                x=x,
            )
        )
    parent_content.extend([
        '  (sheet_instances (path "/" (page "1")))',
        ")",
    ])
    parent_path.write_text("\n".join(parent_content) + "\n", encoding="utf-8")

    child_paths: dict[str, Path] = {}
    for spec, _sheet_uuid in sheet_entries:
        name, relative_path, pin_spec, _pins, page, _x = spec
        child_path = project_dir / relative_path
        child_path.parent.mkdir(parents=True, exist_ok=True)
        child_content = render_implementation_schematic(
            name,
            str(uuid.uuid4()),
            parse_pins(pin_spec),
        )
        # KiCad stores the root sheet_instances block only in the root
        # project schematic. Child files have no root sheet-instance block;
        # their hierarchy is represented by the parent sheet symbol and by
        # hierarchical paths on symbol instances inside the child.
        old_instances = '(sheet_instances (path "/" (page "1")))'
        if old_instances not in child_content:
            raise AssertionError("The generated child template has no root sheet-instance placeholder.")
        child_path.write_text(
            child_content.replace(old_instances, ""),
            encoding="utf-8",
        )
        child_paths[name] = child_path

    (project_dir / f"{project_name}.kicad_pro").write_text("{}\n", encoding="utf-8")
    return parent_path, child_paths

def _open_live_eeschema(
    schematic_path: Path,
    root: Path,
) -> tuple[KiCad, subprocess.Popen, str, str]:
    """Launch real Eeschema with IPC enabled and return its live schematic client."""
    eeschema = os.environ.get("KICAD_EESCHEMA") or _find_executable(
        "eeschema-nightly", "eeschema"
    )
    xdotool = _find_executable("xdotool")
    if not os.environ.get("DISPLAY"):
        raise RuntimeError("Run the Create Block IPC test under xvfb-run; DISPLAY is not set.")

    home = root / "eeschema-home"
    home.mkdir()
    documents_home = home / ".local" / "share"
    cache_home = home / ".cache"
    version_output = subprocess.run(
        [_find_cli(), "version"], capture_output=True, text=True, check=True
    ).stdout
    version_match = re.search(r"(\d+\.\d+)", version_output)
    if version_match is None:
        raise RuntimeError(f"Could not determine KiCad MAJOR.MINOR version: {version_output!r}")
    version = version_match.group(1)
    python_interpreter = shutil.which("python3")
    if not python_interpreter:
        raise FileNotFoundError("System python3 was not found for KiCad IPC configuration.")

    env = os.environ.copy()
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
            "GDK_BACKEND": "x11",
            "DISPLAY": os.environ["DISPLAY"],
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(documents_home),
            "XDG_CACHE_HOME": str(cache_home),
            "KICAD_DOCUMENTS_HOME": str(documents_home),
            "KICAD_CACHE_HOME": str(cache_home),
        }
    )

    # First launch creates KiCad's version-specific profile and resolves any
    # first-run setup dialog. Enable the IPC API in that isolated profile,
    # then launch the actual test project with Eeschema's GUI server.
    first_log = root / "create-block-first-run.log"
    first_process, first_window = _launch(
        eeschema, schematic_path, env, xdotool, first_log
    )
    _close_editor(first_process, first_window, xdotool)
    config_files = list(home.rglob("kicad_common.json"))
    if not config_files:
        raise FileNotFoundError(
            "Eeschema did not create kicad_common.json during first launch."
        )
    common_path = next(
        (item for item in config_files if item.parent.name == version),
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

    editor_log = root / "create-block-editor.log"
    process, window_id = _launch(
        eeschema, schematic_path, env, xdotool, editor_log
    )
    deadline = time.monotonic() + 30.0
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Eeschema exited before exposing the IPC API (exit {process.returncode}). "
                f"{editor_log.read_text(errors='replace')[-8000:]}"
            )
        client = None
        try:
            client = KiCad(timeout_ms=30000)
            schematic = client.get_schematic()
            if schematic is not None:
                return client, process, window_id, xdotool
        except (ApiError, NNGException, OSError, TimeoutError) as exc:
            last_error = exc
            if client is not None:
                close_client = getattr(client, "close", None)
                if callable(close_client):
                    with contextlib.suppress(ApiError, NNGException, OSError):
                        close_client()
        time.sleep(0.25)
    raise TimeoutError(
        "Eeschema opened but its schematic IPC endpoint did not become ready. "
        f"Last error: {last_error!r}; "
        f"log: {editor_log.read_text(errors='replace')[-8000:]}"
    )


def _assert_cli_exports(cli: str, record, parent_path: Path, output_root: Path) -> None:
    symbol_output = output_root / (record.path.name + "-symbol")
    schematic_output = output_root / (record.path.name + "-schematic")
    symbol_output.mkdir()
    schematic_output.mkdir()
    subprocess.run(
        [cli, "sym", "export", "svg", "--output", str(symbol_output), str(record.symbol_library)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [cli, "sch", "export", "svg", "--output", str(schematic_output), str(record.implementation)],
        check=True,
        capture_output=True,
        text=True,
    )
    if not list(symbol_output.glob("*.svg")):
        raise AssertionError(f"KiCad CLI did not export facade symbol for {record.name}.")
    if not list(schematic_output.glob("*.svg")):
        raise AssertionError(f"KiCad CLI did not export implementation for {record.name}.")

    parent_output = output_root / (record.path.name + "-source-project")
    parent_output.mkdir()
    subprocess.run(
        [cli, "sch", "export", "svg", "--output", str(parent_output), str(parent_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    if not list(parent_output.glob("*.svg")):
        raise AssertionError("KiCad CLI did not export the original multi-sheet project.")


def _validate_hierarchy_fixture(cli: str, parent_path: Path, child_paths: dict[str, Path], output_root: Path) -> None:
    """Fail with KiCad's parser diagnostics before starting the IPC server."""
    for label, source in [("parent", parent_path), *child_paths.items()]:
        destination = output_root / f"fixture-{label}-svg"
        destination.mkdir()
        result = subprocess.run(
            [
                cli,
                "sch",
                "export",
                "svg",
                "--output",
                str(destination),
                str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not list(destination.glob("*.svg")):
            raise RuntimeError(
                f"KiCad CLI rejected the {label} source-sheet fixture {source}. "
                f"exit={result.returncode}; stdout={result.stdout}; stderr={result.stderr}"
            )


def main() -> None:
    cli = _find_cli()
    with tempfile.TemporaryDirectory(prefix="kicad-blocks-create-sheet-ipc-") as temp:
        root = Path(temp)
        project_dir = root / "project"
        library_root = root / "shared-library"
        project_dir.mkdir()
        library_root.mkdir()
        parent_path, child_paths = _write_source_sheets(project_dir)
        project_path = project_dir / "CreateBlockFromSheetIPC.kicad_pro"
        _validate_hierarchy_fixture(cli, parent_path, child_paths, root)

        original_files = {
            path: path.read_bytes()
            for path in (parent_path, project_path, *child_paths.values())
        }
        kicad = None
        process = None
        window_id = None
        xdotool = None
        try:
            kicad, process, window_id, xdotool = _open_live_eeschema(
                parent_path, root
            )
            schematic = kicad.get_schematic()
            if schematic is None:
                raise RuntimeError("Live Eeschema IPC returned no schematic document.")

            hierarchy = schematic.get_hierarchy()
            root_sheet = getattr(hierarchy, "root", None)
            if root_sheet is None:
                raise RuntimeError("KiCad IPC returned no root sheet hierarchy.")
            sheet_instances = list(getattr(root_sheet, "children", ()) or ())
            if len(sheet_instances) != 2:
                raise AssertionError(
                    f"Expected two child sheet instances, found {len(sheet_instances)}."
                )

            typed_sheets = list(schematic.get_sheet_symbols())
            if len(typed_sheets) != 2:
                raise AssertionError(
                    f"Expected two real SheetSymbol objects from IPC, found {len(typed_sheets)}."
                )
            selected = next(
                item for item in typed_sheets
                if str(item.name_field.text.value) == "Power Stage"
            )
            filename_value = str(selected.filename_field.text.value)
            if filename_value.replace("\\", "/") != "sheets/power_stage.kicad_sch":
                raise AssertionError(
                    f"Unexpected relative Sheetfile value from KiCad IPC: {filename_value!r}"
                )
            # AddToSelection is intentionally exercised against the live
            # Eeschema editor, not a headless API server or a fake selection.
            schematic.add_to_selection(selected)
            live_selection = list(schematic.get_selection())
            if len(live_selection) != 1 or not hasattr(live_selection[0], "filename_field"):
                raise AssertionError(
                    "KiCad did not preserve the selected hierarchical sheet as a typed SheetSymbol."
                )

            def create_details(name: str, description: str):
                return lambda _parent, _source: (name, description)

            def run_creation(parent_schematic, name: str, description: str):
                with (
                    patch.object(
                        plugin_actions,
                        "configured_library_roots",
                        return_value=[library_root],
                    ),
                    patch.object(
                        plugin_actions, "_reload_symbol_library", return_value=True
                    ),
                    patch.object(
                        plugin_actions,
                        "_ask_create_block_details",
                        side_effect=create_details(name, description),
                    ),
                    patch.object(
                        plugin_actions,
                        "messagebox",
                        SimpleNamespace(showinfo=lambda *args, **kwargs: None),
                    ),
                ):
                    plugin_actions._create_and_place(
                        library_root, parent_schematic, kicad, project_dir, object()
                    )

            run_creation(
                schematic,
                "Power Stage Block",
                "Created from selected SheetSymbol",
            )

            records = list_blocks(library_root)
            if len(records) != 1:
                raise AssertionError(
                    f"Expected exactly one new block from the selected sheet, found {len(records)}."
                )
            selected_record = records[0]
            selected_manifest = selected_record.manifest
            if selected_manifest["interface_source"] != "hierarchical_labels":
                raise AssertionError("The selected-sheet block did not retain its extraction provenance.")
            if [item["name"] for item in selected_manifest["pins"]] != ["VIN", "GND", "VOUT"]:
                raise AssertionError(
                    f"Selected-sheet facade pins were not derived from its labels: {selected_manifest['pins']!r}"
                )
            project_catalog = project_dir / ".kicad_blocks" / LIBRARY_CATALOG_NAME
            if (
                not project_catalog.is_file()
                or selected_manifest["symbol_name"] not in project_catalog.read_text(encoding="utf-8")
            ):
                raise AssertionError("Create Block did not rebuild the project's unified facade catalog.")
            symbol_table = project_dir / "sym-lib-table"
            if (
                not symbol_table.is_file()
                or LIBRARY_NICKNAME not in symbol_table.read_text(encoding="utf-8")
            ):
                raise AssertionError("Create Block did not register the facade catalog in sym-lib-table.")

            # Scope a real IPC schematic object to the other child sheet. This
            # is the same path-bound view the action receives when a user has
            # opened that subsheet with no canvas selection.
            other_instance = next(
                item for item in sheet_instances
                if str(getattr(item, "name", "")) == "Control Interface"
            )
            open_sheet = schematic.for_sheet(other_instance)
            active_source, active_sheet_uuid = plugin_actions._active_sheet_source(
                open_sheet, project_dir
            )
            if active_source != child_paths["Control Interface"].resolve():
                raise AssertionError(
                    f"The subsheet IPC view resolved to the wrong source: {active_source}."
                )
            if not active_sheet_uuid:
                raise AssertionError("The active subsheet IPC view did not expose its instance UUID.")
            schematic.clear_selection()
            open_selection = list(open_sheet.get_selection())
            if open_selection:
                raise AssertionError("Subsheet IPC view unexpectedly retained the previous sheet selection.")

            run_creation(
                open_sheet,
                "Control Interface Block",
                "Created from active sheet view",
            )

            records = list_blocks(library_root)
            if len(records) != 2:
                raise AssertionError(
                    f"Expected one additional block from the active subsheet, found {len(records)} total."
                )
            active_record = next(item for item in records if item.name == "Control Interface Block")
            if [item["name"] for item in active_record.manifest["pins"]] != ["SCL", "SDA", "ALERT"]:
                raise AssertionError(
                    f"Active-sheet facade pins came from the wrong sheet: {active_record.manifest['pins']!r}"
                )

            if not all(path.read_bytes() == original for path, original in original_files.items()):
                raise AssertionError("Create Block modified one or more original project sheet files.")

            _assert_cli_exports(cli, selected_record, parent_path, root)
            _assert_cli_exports(cli, active_record, parent_path, root)
            print(
                "Create Block from Sheet passed against live KiCad IPC: selected typed SheetSymbol, "
                "active subsheet with empty selection, facade/catalog registration, extraction "
                "metadata, unchanged source files, and KiCad CLI exports."
            )
        finally:
            if kicad is not None:
                with contextlib.suppress(ApiError, NNGException, OSError):
                    kicad.close()
            if process is not None and process.poll() is None and window_id and xdotool:
                _close_editor(process, window_id, xdotool)


if __name__ == "__main__":
    main()
