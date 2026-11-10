"""Probe the KiCad PCB IPC primitives used by block-layout application."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from kipy import KiCad
from kipy.geometry import Vector2


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
        """(kicad_pcb
  (version 20250114)
  (generator "pcbnew")
  (general (thickness 1.6))
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (32 "B.Adhes" user "B.Adhesive")
    (33 "F.Adhes" user "F.Adhesive")
    (34 "B.Paste" user)
    (35 "F.Paste" user)
    (36 "B.SilkS" user)
    (37 "F.SilkS" user)
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (40 "Dwgs.User" user)
    (41 "Cmts.User" user)
    (42 "Eco1.User" user)
    (43 "Eco2.User" user)
    (44 "Edge.Cuts" user)
    (45 "Margin" user)
    (46 "B.CrtYd" user)
    (47 "F.CrtYd" user)
    (48 "B.Fab" user)
    (49 "F.Fab" user)
  )
  (setup)
  (net 0 "")
)
""",
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

            # Exercise library loading and footprint insertion through IPC.
            kicad.load_all_libraries(types=[2])
            kicad.wait_for_libraries(types=[2], timeout_s=120.0)
            footprint = board.place_footprint_from_library(
                "Resistor_SMD:R_0603_1608Metric",
                Vector2.from_xy_mm(100.0, 100.0),
            )
            footprint.reference_field.text.value = "R1"
            board.save()

            footprints = list(board.get_footprints())
            references = {item.reference_field.text.value for item in footprints}
            if "R1" not in references:
                raise AssertionError(f"Placed footprint reference missing: {references!r}")

            pads = [pad for pad in board.get_pads() if pad.parent == footprint.id]
            if not pads:
                raise AssertionError("The placed footprint has no pads in the board IPC view.")
            target_pad = next((pad for pad in pads if pad.number == "1"), None)
            if target_pad is None:
                raise AssertionError("The test footprint has no pad number 1.")

            # Net is board-level state, not a regular BoardItem. Use KiCad's
            # native netlist-import operation to add a named internal net and
            # attach the already-placed footprint pad to it.
            netlist_path = Path(temp) / "block-internal.net"
            netlist_path.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<export version="D">
  <design>
    <source>kicad-blocks PCB IPC fixture</source>
    <date>2026-11-10</date>
    <tool>kicad-blocks</tool>
  </design>
  <components>
    <comp ref="R1">
      <value>10k</value>
      <libsource lib="Device" part="R"/>
      <sheetpath names="/" tstamps="/"/>
      <tstamps>00000000-0000-0000-0000-000000000001</tstamps>
    </comp>
  </components>
  <nets>
    <net code="1" name="BLOCK_INTERNAL">
      <node ref="R1" pin="1"/>
    </net>
  </nets>
</export>
""",
                encoding="utf-8",
            )
            board.import_netlist(
                str(netlist_path),
                delete_extra_footprints=False,
                update_footprints=False,
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
