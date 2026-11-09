"""Generate a block fixture and validate it with KiCad's own command-line parser."""

from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_pins  # noqa: E402
from kicad_blocks.repository import create_block  # noqa: E402


def main() -> None:
    cli = "kicad-cli"
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
            [cli, "sym", "export", "svg", "--output", str(symbol_out), str(record.symbol_library)],
            check=True,
        )
        subprocess.run(
            [cli, "sch", "export", "svg", "--output", str(schematic_out), str(record.implementation)],
            check=True,
        )
        symbol_svgs = list(symbol_out.glob("*.svg"))
        schematic_svgs = list(schematic_out.glob("*.svg"))
        if not symbol_svgs:
            raise RuntimeError("KiCad did not export an SVG for the generated block symbol.")
        if not schematic_svgs:
            raise RuntimeError("KiCad did not export an SVG for the generated implementation schematic.")
        print(f"KiCad CLI validated symbol ({symbol_svgs[0].name}) and implementation ({schematic_svgs[0].name}).")


if __name__ == "__main__":
    main()
