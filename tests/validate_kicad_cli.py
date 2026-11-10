"""Generate a block fixture and validate it with KiCad's own command-line parser."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_pins
from kicad_blocks.operations import export_block_bom, validate_block_tree
from kicad_blocks.repository import add_dependency, create_block


def _find_kicad_cli() -> str:
    configured = os.environ.get("KICAD_CLI")
    candidates = [configured] if configured else []
    candidates.extend(("kicad-cli-nightly", "kicad-cli"))
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise RuntimeError(
        "KiCad CLI was not found. Set KICAD_CLI or add kicad-cli(-nightly) to PATH."
    )


def main() -> None:
    cli = _find_kicad_cli()
    with tempfile.TemporaryDirectory(prefix="kicad-blocks-cli-") as temp:
        record = create_block(
            Path(temp) / "library",
            "CLI Validation Block",
            parse_pins("VIN:input, GND:passive, VOUT:output, SDA:bidir"),
        )
        symbol_out = Path(temp) / "symbol-svg"
        schematic_out = Path(temp) / "schematic-svg"
        symbol_out.mkdir()
        schematic_out.mkdir()
        subprocess.run(
            [
                cli,
                "sym",
                "export",
                "svg",
                "--output",
                str(symbol_out),
                str(record.symbol_library),
            ],
            check=True,
        )
        subprocess.run(
            [
                cli,
                "sch",
                "export",
                "svg",
                "--output",
                str(schematic_out),
                str(record.implementation),
            ],
            check=True,
        )
        symbol_svgs = list(symbol_out.glob("*.svg"))
        schematic_svgs = list(schematic_out.glob("*.svg"))
        if not symbol_svgs:
            raise RuntimeError("KiCad did not export an SVG for the generated block symbol.")
        if not schematic_svgs:
            raise RuntimeError(
                "KiCad did not export an SVG for the generated implementation schematic."
            )
        child = record
        parent = create_block(
            Path(temp) / "library",
            "CLI Validation Parent",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
        )
        parent = add_dependency(parent, child)
        bom_output = Path(temp) / "recursive-bom.csv"
        bom_result = export_block_bom(parent, bom_output, cli, Path(temp) / "library")
        if not bom_output.is_file() or bom_result["block_count"] != 2:
            raise RuntimeError("Recursive KiCad CLI BOM export did not include both blocks.")
        erc_reports = validate_block_tree(
            parent, Path(temp) / "erc-reports", cli, Path(temp) / "library"
        )
        if len(erc_reports) != 2 or not all(
            Path(item["report"]).is_file() for item in erc_reports
        ):
            raise RuntimeError("Recursive KiCad CLI ERC did not report both block implementations.")
        print(
            f"KiCad CLI validated symbol ({symbol_svgs[0].name}), "
            f"implementation ({schematic_svgs[0].name}), recursive BOM, and ERC."
        )


if __name__ == "__main__":
    main()
