"""Probe the KiCad PCB IPC primitives used by block-layout application."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from kipy import KiCad


def _find_cli() -> str:
    configured = os.environ.get("KICAD_CLI")
    candidates = [configured] if configured else []
    candidates.extend(("kicad-cli-nightly", "kicad-cli"))
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise FileNotFoundError("KiCad CLI was not found.")


def _write_minimal_board(path: Path) -> None:
    """Write a small but valid empty board document for an IPC test."""
    path.write_text(
        "(kicad_pcb\n  (version 20250114)\n  (generator \"pcbnew\")\n  (general (thickness 1.6))\n  (paper \"A4\")\n  (layers\n    (0 \"F.Cu\" signal)\n    (31 \"B.Cu\" signal)\n    (32 \"B.Adhes\" user \"B.Adhesive\")\n    (33 \"F.Adhes\" user \"F.Adhesive\")\n    (34 \"B.Paste\" user)\n    (35 \"F.Paste\" user)\n    (36 \"B.SilkS\" user)\n    (37 \"F.SilkS\" user)\n    (38 \"B.Mask\" user)\n    (39 \"F.Mask\" user)\n    (40 \"Dwgs.User\" user)\n    (41 \"Cmts.User\" user)\n    (42 \"Eco1.User\" user)\n    (43 \"Eco2.User\" user)\n    (44 \"Edge.Cuts\" user)\n    (45 \"Margin\" user)\n    (46 \"B.CrtYd\" user)\n    (47 \"F.CrtYd\" user)\n    (48 \"B.Fab\" user)\n    (49 \"F.Fab\" user)\n  )\n  (setup)\n  (net 0 \"\")\n  (footprint \"Test:BlockResistor\"\n    (layer \"F.Cu\")\n    (at 100 100 0)\n    (path \"/00000000-0000-0000-0000-000000000001\" (reference \"R1\") (unit 1))\n    (property \"Reference\" \"R1\" (at 100 98 0) (layer \"F.SilkS\") (effects (font (size 1 1))))\n    (property \"Value\" \"10k\" (at 100 102 0) (layer \"F.Fab\") (effects (font (size 1 1))))\n    (fp_text reference \"R1\" (at 0 -1.5 0) (layer \"F.SilkS\") (effects (font (size 1 1))))\n    (fp_text value \"10k\" (at 0 1.5 0) (layer \"F.Fab\") (effects (font (size 1 1))))\n    (pad \"1\" smd rect (at -0.75 0 0) (size 0.8 1.2) (layers \"F.Cu\" \"F.Paste\" \"F.Mask\") (net 0 \"\"))\n    (pad \"2\" smd rect (at 0.75 0 0) (size 0.8 1.2) (layers \"F.Cu\" \"F.Paste\" \"F.Mask\") (net 0 \"\"))\n  )\n)\n",
        encoding="utf-8",
    )


def main() -> None:
    cli = _find_cli()
    with tempfile.TemporaryDirectory(prefix="kicad-blocks-pcb-ipc-") as temp:
        board_path = Path(temp) / "target.kicad_pcb"
        _write_minimal_board(board_path)

        kicad = KiCad(
            headless=True,
            kicad_cli_path=cli,
            file_path=str(board_path),
            timeout_ms=30000,
        )
        try:
            board = kicad.get_board()
            if board is None:
                raise RuntimeError("KiCad IPC did not load the test board.")

            existing_nets = {item.name for item in board.get_nets()}
            if "" not in existing_nets:
                raise AssertionError(f"Empty net missing from board: {existing_nets!r}")

            # The fixture contains a fully embedded footprint. The block
            # importer copies existing footprint instances; it should not
            # depend on a global footprint library being installed.
            footprints = list(board.get_footprints())
            footprint = next(
                (
                    item
                    for item in footprints
                    if item.reference_field.text.value == "R1"
                ),
                None,
            )
            if footprint is None:
                raise AssertionError(
                    "Embedded fixture footprint was not loaded: "
                    + repr([item.reference_field.text.value for item in footprints])
                )

            pads = [pad for pad in board.get_pads() if pad.parent == footprint.id]
            if not pads:
                raise AssertionError("The placed footprint has no pads in the board IPC view.")
            target_pad = next((pad for pad in pads if pad.number == "1"), None)
            if target_pad is None:
                raise AssertionError("The test footprint has no pad number 1.")

            # KiCad PCB import expects the KiCad S-expression netlist format,
            # not the XML intermediate netlist. The importer chooses its parser
            # from the first line: (export (version ...)).
            netlist_path = Path(temp) / "block-internal.net"
            netlist_path.write_text(
                """(export (version "E")
  (design
    (source "kicad-blocks PCB IPC fixture")
    (date "2026-11-10")
    (tool "kicad-blocks"))
  (components
    (comp (ref "R1")
      (value "10k")
      (footprint "Test:BlockResistor")
      (libsource (lib "Test") (part "BlockResistor") (description ""))
      (property (name "Sheetname") (value ""))
      (property (name "Sheetfile") (value "pcb-ipc-fixture.kicad_sch"))
      (sheetpath (names "/") (tstamps "/"))
      (tstamps "00000000-0000-0000-0000-000000000001")))
  (libparts
    (libpart (lib "Test") (part "BlockResistor")
      (footprints (fp "Test:BlockResistor"))
      (fields
        (field (name "Reference") "R")
        (field (name "Value") "10k"))
      (pins
        (pin (num "1") (name "1") (type "passive"))
        (pin (num "2") (name "2") (type "passive")))))
  (libraries
    (library (logical "Test") (uri "embedded://Test")))
  (nets
    (net (code "1") (name "BLOCK_INTERNAL")
      (node (ref "R1") (pin "1") (pintype "passive")))))
""",
                encoding="utf-8",
            )
            if not netlist_path.is_file() or netlist_path.stat().st_size == 0:
                raise AssertionError("The test netlist was not written to disk.")
            netlist_text = netlist_path.read_text(encoding="utf-8")
            if not netlist_text.lstrip().startswith('(export (version "E")'):
                raise AssertionError("The file is not in KiCad PCB-import netlist format.")
            if "BLOCK_INTERNAL" not in netlist_text or '(ref "R1")' not in netlist_text:
                raise AssertionError("The PCB-import netlist fixture is missing the expected net/component.")

            result = board.import_netlist(
                str(netlist_path.resolve()),
                delete_extra_footprints=False,
                update_footprints=False,
            )
            if result.error_count:
                raise AssertionError(
                    f"KiCad netlist import reported {result.error_count} errors: {result.report}"
                )
            board.save()

            created_nets = {item.name for item in board.get_nets()}
            if "BLOCK_INTERNAL" not in created_nets:
                raise AssertionError(
                    "KiCad netlist import did not add the block-internal net: "
                    + repr(sorted(created_nets))
                )
            refreshed = {
                pad.number: pad.net.name
                for pad in board.get_pads()
                if pad.parent == footprint.id
            }
            if refreshed.get("1") != "BLOCK_INTERNAL":
                raise AssertionError(
                    "Netlist import did not assign the pad to the block net: "
                    + repr(refreshed)
                )

            print(
                "KiCad PCB IPC passed: create internal net, place footprint, "
                "and assign a pad to the new net."
            )
        finally:
            kicad.close()


if __name__ == "__main__":
    main()
