"""End-to-end validation for stored PCB block layout application."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from kipy import KiCad

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.pcb_layout import merge_block_layout, parse_sexpr


def _find_cli() -> str:
    configured = os.environ.get("KICAD_CLI")
    for candidate in (configured, "kicad-cli-nightly", "kicad-cli"):
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise FileNotFoundError("KiCad CLI was not found.")


def _minimal_board(path: Path) -> None:
    path.write_text(
        "(kicad_pcb\n"
        "  (version 20250114)\n"
        "  (generator \"pcbnew\")\n"
        "  (general (thickness 1.6))\n"
        "  (paper \"A4\")\n"
        "  (layers\n"
        "    (0 \"F.Cu\" signal) (31 \"B.Cu\" signal)\n"
        "    (32 \"B.Adhes\" user \"B.Adhesive\") (33 \"F.Adhes\" user \"F.Adhesive\")\n"
        "    (34 \"B.Paste\" user) (35 \"F.Paste\" user)\n"
        "    (36 \"B.SilkS\" user) (37 \"F.SilkS\" user)\n"
        "    (38 \"B.Mask\" user) (39 \"F.Mask\" user)\n"
        "    (40 \"Dwgs.User\" user) (41 \"Cmts.User\" user)\n"
        "    (42 \"Eco1.User\" user) (43 \"Eco2.User\" user)\n"
        "    (44 \"Edge.Cuts\" user) (45 \"Margin\" user)\n"
        "    (46 \"B.CrtYd\" user) (47 \"F.CrtYd\" user)\n"
        "    (48 \"B.Fab\" user) (49 \"F.Fab\" user)\n"
        "  )\n"
        "  (setup)\n"
        "  (net 0 \"\")\n"
        "  (footprint \"Test:BlockResistor\"\n"
        "    (layer \"F.Cu\") (uuid \"00000000-0000-0000-0000-000000000001\") (at 100 100 0)\n"
        "    (path \"/00000000-0000-0000-0000-000000000001\")\n"
        "    (property \"Reference\" \"R1\" (at 100 98 0) (layer \"F.SilkS\") (effects (font (size 1 1))))\n"
        "    (property \"Value\" \"10k\" (at 100 102 0) (layer \"F.Fab\") (effects (font (size 1 1))))\n"
        "    (fp_text reference \"R1\" (at 0 -1.5 0) (layer \"F.SilkS\") (effects (font (size 1 1))))\n"
        "    (fp_text value \"10k\" (at 0 1.5 0) (layer \"F.Fab\") (effects (font (size 1 1))))\n"
        "    (pad \"1\" smd rect (at -0.75 0 0) (size 0.8 1.2) (layers \"F.Cu\" \"F.Paste\" \"F.Mask\") (uuid \"00000000-0000-0000-0000-000000000011\") (net 0 \"\"))\n"
        "    (pad \"2\" smd rect (at 0.75 0 0) (size 0.8 1.2) (layers \"F.Cu\" \"F.Paste\" \"F.Mask\") (uuid \"00000000-0000-0000-0000-000000000012\") (net 0 \"\"))\n"
        "  )\n"
        ")\n",
        encoding="utf-8",
    )


def main() -> None:
    cli = _find_cli()
    with tempfile.TemporaryDirectory(prefix="kicad-block-layout-") as temp:
        root = Path(temp)
        target = root / "project.kicad_pcb"
        source = root / "layout.kicad_pcb"
        output = root / "project-with-block.kicad_pcb"
        block_dir = root / "TestBlock.kicad_block"
        block_dir.mkdir()
        _minimal_board(target)
        _minimal_board(source)

        # Verify the fixture boards are valid KiCad files before testing the
        # serializer/merge path; otherwise a malformed fixture could masquerade
        # as a merge failure.
        baseline_svg_dir = root / "baseline-svg"
        baseline_svg_dir.mkdir()
        subprocess.run(
            [cli, "pcb", "export", "svg", "--mode-multi", "--layers", "F.Cu,B.Cu", "--output", str(baseline_svg_dir), str(target)],
            check=True,
        )
        if not list(baseline_svg_dir.glob("*.svg")):
            raise AssertionError("KiCad CLI did not accept the baseline project board.")

        source_text = source.read_text(encoding="utf-8")
        source_text = source_text.replace(
            '  (net 0 "")',
            '  (net 0 "")\n  (net 1 "VIN")\n  (net 2 "GND")\n  (net 3 "SW")',
            1,
        )
        source_text = source_text.replace(
            '(uuid "00000000-0000-0000-0000-000000000011") (net 0 "")',
            '(uuid "00000000-0000-0000-0000-000000000011") (net 1 "VIN")',
            1,
        )
        source_text = source_text.replace(
            '(uuid "00000000-0000-0000-0000-000000000012") (net 0 "")',
            '(uuid "00000000-0000-0000-0000-000000000012") (net 2 "GND")',
            1,
        )
        source_text = source_text.rsplit(")", 1)[0] + (
            '  (segment (start 100 100) (end 105 100) (width 0.25) '
            '(layer "F.Cu") (net 3) (uuid "00000000-0000-0000-0000-000000000002"))\n)\n'
        )
        if '(uuid "00000000-0000-0000-0000-000000000011") (net 1 "VIN")' not in source_text:
            raise AssertionError("Test fixture did not assign VIN to source pad 1.")
        if '(uuid "00000000-0000-0000-0000-000000000012") (net 2 "GND")' not in source_text:
            raise AssertionError("Test fixture did not assign GND to source pad 2.")
        source.write_text(source_text, encoding="utf-8")
        (block_dir / "layout.kicad_pcb").write_text(source_text, encoding="utf-8")

        target_project = target.with_suffix(".kicad_pro")
        target_project.write_text(
            json.dumps(
                {
                    "meta": {"filename": target_project.name, "version": 3},
                    "net_settings": {
                        "classes": [
                            {"name": "Default", "priority": 2147483647, "clearance": 0.2},
                            {"name": "LowNoise", "priority": 0, "clearance": 0.3, "track_width": 0.3},
                        ],
                        "netclass_assignments": {"VIN_BOARD": ["LowNoise"]},
                        "netclass_patterns": [],
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        source_project = block_dir / "layout.kicad_pro"
        source_project.write_text(
            json.dumps(
                {
                    "meta": {"filename": source_project.name, "version": 3},
                    "net_settings": {
                        "classes": [
                            {"name": "Default", "priority": 2147483647, "clearance": 0.2},
                            {"name": "HighSpeed", "priority": 0, "clearance": 0.1, "track_width": 0.15, "via_diameter": 0.5, "via_drill": 0.25},
                        ],
                        "netclass_assignments": {
                            "VIN": ["HighSpeed"],
                            "SW": ["HighSpeed"],
                        },
                        "netclass_patterns": [{"pattern": ".*", "netclass": "HighSpeed"}],
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        original_target_project = target_project.read_bytes()
        original_source_project = source_project.read_bytes()

        source_svg_dir = root / "source-layout-svg"
        source_svg_dir.mkdir()
        subprocess.run(
            [
                cli, "pcb", "export", "svg", "--mode-multi", "--layers", "F.Cu,B.Cu",
                "--output", str(source_svg_dir), str(source),
            ],
            check=True,
        )
        if not list(source_svg_dir.glob("*.svg")):
            raise AssertionError("KiCad did not accept/export the stored block layout.")

        block = SimpleNamespace(
            path=block_dir,
            name="Test Block",
            manifest={
                "layout_file": "layout.kicad_pcb",
                "pins": [{"name": "VIN"}, {"name": "GND"}],
                "layout_ports": {"VIN": "VIN", "GND": "GND"},
                "layout_project_file": "layout.kicad_pro",
            },
        )
        result = merge_block_layout(
            target,
            block,
            output,
            instance_reference="X1",
            position_mm=(120.0, 130.0),
            layout_anchor_mm=(100.0, 100.0),
            pin_to_board_net={"VIN": "VIN_BOARD", "GND": "GND_BOARD"},
            rotation_deg=90.0,
        )
        if not output.is_file() or target.read_bytes() == output.read_bytes():
            raise AssertionError("Merged board output was not written separately from the source board.")
        output_project = output.with_suffix(".kicad_pro")
        if not output_project.is_file():
            raise AssertionError("Merged output did not receive a matching KiCad project file.")
        if target_project.read_bytes() != original_target_project or source_project.read_bytes() != original_source_project:
            raise AssertionError("Project-settings merge modified one of its input project files.")
        merged_project = json.loads(output_project.read_text(encoding="utf-8"))
        net_settings = merged_project["net_settings"]
        class_names = {item["name"] for item in net_settings["classes"]}
        if "X1_HighSpeed" not in class_names or "LowNoise" not in class_names:
            raise AssertionError(f"Netclass definitions were not merged: {sorted(class_names)}")
        assignments = net_settings["netclass_assignments"]
        if assignments.get("VIN_BOARD") != ["LowNoise", "X1_HighSpeed"]:
            raise AssertionError(f"External netclass assignments were not merged: {assignments!r}")
        if assignments.get("X1_SW") != ["X1_HighSpeed"]:
            raise AssertionError(f"Internal namespaced netclass assignment missing: {assignments!r}")
        if merged_project["meta"]["filename"] != output_project.name:
            raise AssertionError("Merged project metadata filename was not updated.")
        if result["merged_netclasses"] != ["X1_HighSpeed"]:
            raise AssertionError(f"Unexpected netclass merge summary: {result!r}")
        if not result["ignored_project_patterns"]:
            raise AssertionError("Broad project netclass patterns should be reported as skipped.")
        if (
            result["footprints"] != ["X1_R1"]
            or result["new_nets"] != ["VIN_BOARD", "GND_BOARD", "X1_SW"]
            or result["rotation_deg"] != 90.0
        ):
            raise AssertionError(f"Unexpected merge report: {result!r}")

        # Parse result independently and assert net codes, reference and translated geometry.
        parsed = parse_sexpr(output.read_text(encoding="utf-8"))
        nets = {int(str(item[1])): str(item[2]) for item in parsed[1:] if isinstance(item, list) and item and item[0] == "net"}
        internal_net_code = next(code for code, name in nets.items() if name == "X1_SW")
        merged_segment = next(
            item for item in parsed[1:]
            if isinstance(item, list) and item and item[0] == "segment"
        )
        segment_net = next(
            item for item in merged_segment[1:]
            if isinstance(item, list) and item and item[0] == "net"
        )
        if len(segment_net) != 2 or int(str(segment_net[1])) != internal_net_code:
            raise AssertionError(f"KiCad track net must contain only its mapped code: {segment_net!r}")
        if "X1_SW" not in nets.values() or "VIN_BOARD" not in nets.values():
            raise AssertionError(f"Project board nets were not merged: {nets!r}")
        imported_footprints = []
        imported_segments = []
        for item in parsed[1:]:
            if not isinstance(item, list) or not item:
                continue
            if item[0] == "footprint":
                ref_prop = next((x for x in item[1:] if isinstance(x, list) and x and x[0] == "property" and str(x[1]).casefold() == "reference"), None)
                if ref_prop and str(ref_prop[2]) == "X1_R1":
                    imported_footprints.append(item)
            if item[0] == "segment" and any(
                isinstance(x, list) and x and x[0] == "net"
                and len(x) == 2 and int(str(x[1])) == internal_net_code
                for x in item[1:]
            ):
                imported_segments.append(item)
        if len(imported_footprints) != 1 or len(imported_segments) != 1:
            raise AssertionError("Imported footprint or routed segment was not found.")
        copied_pad_nets = {}
        for pad in imported_footprints[0][1:]:
            if isinstance(pad, list) and pad and pad[0] == "pad":
                net_node = next(
                    (x for x in pad[1:] if isinstance(x, list) and x and x[0] == "net"),
                    None,
                )
                if net_node is None or len(net_node) != 3:
                    raise AssertionError(f"Imported pad does not have a named net: {pad!r}")
                copied_pad_nets[str(pad[1])] = (int(str(net_node[1])), str(net_node[2]))
        if copied_pad_nets != {"1": (1, "VIN_BOARD"), "2": (2, "GND_BOARD")}:
            raise AssertionError(f"Imported pad nets were not mapped: {copied_pad_nets!r}")
        fp_at = next(x for x in imported_footprints[0][1:] if isinstance(x, list) and x and x[0] == "at")
        if (float(fp_at[1]), float(fp_at[2]), float(fp_at[3])) != (120.0, 130.0, 90.0):
            raise AssertionError(f"Footprint was not rotated/translated: {fp_at!r}")
        ref_property = next(
            x for x in imported_footprints[0][1:]
            if isinstance(x, list) and x and x[0] == "property"
            and str(x[1]).casefold() == "reference"
        )
        ref_at = next(x for x in ref_property[1:] if isinstance(x, list) and x and x[0] == "at")
        if (float(ref_at[1]), float(ref_at[2]), float(ref_at[3])) != (118.0, 130.0, 90.0):
            raise AssertionError(f"Footprint reference field was not rotated: {ref_at!r}")
        merged_segment = next(
            item for item in parsed[1:]
            if isinstance(item, list) and item and item[0] == "segment"
        )
        segment_start = next(x for x in merged_segment[1:] if isinstance(x, list) and x and x[0] == "start")
        segment_end = next(x for x in merged_segment[1:] if isinstance(x, list) and x and x[0] == "end")
        if (float(segment_start[1]), float(segment_start[2])) != (120.0, 130.0):
            raise AssertionError(f"Track start was not transformed: {segment_start!r}")
        if (float(segment_end[1]), float(segment_end[2])) != (120.0, 125.0):
            raise AssertionError(f"Track end was not transformed: {segment_end!r}")

        # KiCad CLI parser/export validates the complete generated board.
        svg_dir = root / "svg"
        svg_dir.mkdir()
        export_result = subprocess.run(
            [cli, "pcb", "export", "svg", "--mode-multi", "--layers", "F.Cu,B.Cu", "--output", str(svg_dir), str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
        if export_result.returncode != 0:
            serialized = output.read_text(encoding="utf-8")
            offset_match = re.search(r"offset (\d+)", export_result.stderr)
            offset = int(offset_match.group(1)) if offset_match else -1
            snippet = serialized[max(0, offset - 180):offset + 180] if offset >= 0 else serialized[:500]
            try:
                parsed_output = parse_sexpr(serialized)
                top_level = [
                    str(item[0])
                    for item in parsed_output[1:]
                    if isinstance(item, list) and item
                ]
                parser_status = f"custom parser accepted; top-level items={top_level!r}"
            except ValueError as parse_error:
                parser_status = f"custom parser rejected: {parse_error!r}"
            raise RuntimeError(
                "KiCad rejected the merged PCB board: "
                + (export_result.stderr or export_result.stdout)
                + f"\nSerialized text near parser location {offset}: {snippet!r}"
                + f"\nSerialized board length={len(serialized)}; "
                + f"parentheses balance={serialized.count('(') - serialized.count(')')}; "
                + parser_status
                + f"\nFULL MERGED BOARD S-EXPRESSION:\n{serialized}"
            )
        if not list(svg_dir.glob("*.svg")):
            raise AssertionError("KiCad did not export the merged board to SVG.")

        # Load the result through the real PCB IPC API and inspect footprint/nets.
        kicad = KiCad(headless=True, kicad_cli_path=cli, file_path=str(output), timeout_ms=30000)
        try:
            board = kicad.get_board()
            if board is None:
                raise RuntimeError("KiCad IPC did not load the merged PCB board.")
            names = {item.name for item in board.get_nets()}
            if not {"VIN_BOARD", "GND_BOARD", "X1_SW"}.issubset(names):
                raise AssertionError(f"Missing merged PCB nets: {sorted(names)}")
            refs = {item.reference_field.text.value for item in board.get_footprints()}
            if not {"R1", "X1_R1"}.issubset(refs):
                raise AssertionError(f"Imported footprint refs missing: {sorted(refs)}")
            tracks = list(board.get_tracks())
            if not tracks:
                raise AssertionError("The stored block track was not present in the merged board.")
        finally:
            kicad.close()

        print(
            "PCB block merge passed: copied board, namespaced footprint and internal net, "
            "mapped external nets, translated routed segment, KiCad CLI export and IPC reload."
        )


if __name__ == "__main__":
    main()
