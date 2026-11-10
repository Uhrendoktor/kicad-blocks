"""Exercise real KiCad 11 schematic IPC placement without launching a GUI."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

from kipy import KiCad
from kipy.geometry import Vector2

from kicad_blocks.model import parse_pins, render_implementation_schematic
from kicad_blocks.repository import (
    create_block,
    register_project_symbol_library,
)


def _find_cli() -> str:
    configured = os.environ.get("KICAD_CLI")
    candidates = [configured] if configured else []
    candidates.extend(("kicad-cli-nightly", "kicad-cli"))
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise RuntimeError("KiCad CLI was not found; set KICAD_CLI or add it to PATH.")


def main() -> None:
    """Start KiCad headlessly, load a project symbol library and place its facade."""
    cli = _find_cli()
    with tempfile.TemporaryDirectory(prefix="kicad-blocks-ipc-") as temp:
        root = Path(temp)
        project_dir = root / "project"
        library_root = root / "shared-library"
        project_dir.mkdir()

        schematic_path = project_dir / "facade_only.kicad_sch"
        schematic_path.write_text(
            render_implementation_schematic("Facade-only IPC Test", "ipc-root", []),
            encoding="utf-8",
        )
        block = create_block(
            library_root,
            "Headless IPC Smoke Block",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
        )
        nickname = block.manifest["library_nickname"]
        symbol_name = block.manifest["symbol_name"]

        kicad = KiCad(
            headless=True,
            kicad_cli_path=cli,
            file_path=str(schematic_path),
            timeout_ms=30000,
        )
        try:
            schematic = kicad.get_schematic()
            if schematic is None:
                raise RuntimeError("KiCad IPC did not load the test schematic.")

            # In headless mode libraries load asynchronously. Initialize the
            # symbol libraries, then register and reload the test library.
            kicad.load_all_libraries(types=[1])
            kicad.wait_for_libraries(types=[1], timeout_s=120.0)
            register_project_symbol_library(
                project_dir,
                nickname,
                block.symbol_library,
                description="Headless IPC test facade",
            )
            kicad.reload_library(type=1, scope=2, nicknames=[nickname])
            kicad.wait_for_libraries(types=[1], scope=2, timeout_s=60.0)

            instance = schematic.place_symbol_from_library(
                f"{nickname}:{symbol_name}",
                Vector2.from_xy_mm(100.0, 100.0),
                reference="X1",
            )
            schematic.save()

            symbols = list(schematic.get_symbols())
            sheet_symbols = list(schematic.get_sheet_symbols())
            if len(symbols) != 1:
                raise AssertionError(f"Expected one facade symbol; found {len(symbols)}.")
            if len(sheet_symbols) != 0:
                raise AssertionError(
                    f"Expected zero hierarchical sheet symbols; found {len(sheet_symbols)}."
                )
            if instance.reference != "X1":
                raise AssertionError(f"Unexpected reference designator: {instance.reference!r}.")
            if (
                instance.definition.id.library_nickname != nickname
                or instance.definition.id.entry_name != symbol_name
            ):
                raise AssertionError("Placed facade has the wrong KiCad library identifier.")
        finally:
            kicad.close()

        project_schematics = sorted(path.name for path in project_dir.glob("*.kicad_sch"))
        if project_schematics != [schematic_path.name]:
            raise AssertionError(
                "Block placement created an unexpected project schematic file: "
                + repr(project_schematics)
            )
        saved_text = schematic_path.read_text(encoding="utf-8")
        expected_lib_id = f'(lib_id "{nickname}:{symbol_name}")'
        if expected_lib_id not in saved_text:
            raise AssertionError("Saved project schematic does not contain the facade library ID.")
        if "(sheet " in saved_text:
            raise AssertionError("Saved parent schematic unexpectedly contains a child sheet.")

        print(
            "KiCad IPC headless placement passed: one facade symbol, zero sheet symbols, "
            "and no project-local child schematic."
        )


if __name__ == "__main__":
    main()
