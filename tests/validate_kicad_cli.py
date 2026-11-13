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
sys.path.insert(0, str(ROOT / "examples" / "stusb4500"))

from generate_reference import generate_reference_schematic
from kicad_blocks.model import parse_pins
from kicad_blocks.operations import export_block_bom, validate_block_tree
from kicad_blocks.pcb_layout import _all, _direct, parse_sexpr
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



def _validate_stusb4500_netlist(cli: str, output_dir: Path) -> None:
    schematic = generate_reference_schematic(output_dir / "stusb4500.kicad_sch")
    netlist_path = output_dir / "stusb4500.net"
    subprocess.run(
        [
            cli,
            "sch",
            "export",
            "netlist",
            "--output",
            str(netlist_path),
            str(schematic),
        ],
        check=True,
    )
    tree = parse_sexpr(netlist_path.read_text(encoding="utf-8"))
    actual: dict[tuple[str, str], str] = {}
    nets_container = _direct(tree, "nets")
    if nets_container is None:
        raise RuntimeError("KiCad netlist export has no nets section.")
    for net in _all(nets_container, "net"):
        name_node = _direct(net, "name")
        if name_node is None:
            continue
        # KiCad may qualify local labels with their sheet path. Compare the
        # actual electrical name while retaining the pin's true net identity.
        net_name = str(name_node[1]).rsplit("/", 1)[-1]
        for node in _all(net, "node"):
            ref_node = _direct(node, "ref")
            pin_node = _direct(node, "pin")
            if ref_node is not None and pin_node is not None:
                actual[(str(ref_node[1]), str(pin_node[1]))] = net_name

    expected = {
        ("U1", "1"): "CC1",
        ("U1", "2"): "CC1",
        ("U1", "4"): "CC2",
        ("U1", "5"): "CC2",
        ("U1", "18"): "VBUS_VS_DISCH",
        ("R1", "2"): "VBUS_VS_DISCH",
        ("R4", "2"): "VBUS_VS_DISCH",
        ("U1", "6"): "RESET",
        ("U1", "7"): "SCL",
        ("U1", "8"): "SDA",
        ("U1", "9"): "DISCH",
        ("U1", "10"): "GND",
        ("U1", "12"): "ADDR0",
        ("U1", "13"): "ADDR1",
        ("U1", "16"): "VBUS_EN_SNK",
        ("U1", "19"): "ALERT#",
        ("U1", "21"): "VREG_1V2",
        ("C1", "1"): "VREG_1V2",
        ("U1", "22"): "GND",
        ("U1", "23"): "VREG_2V7",
        ("C2", "1"): "VREG_2V7",
        ("C3", "1"): "VREG_2V7",
        ("R4", "1"): "VREG_2V7",
        ("U1", "24"): "VBUS",
        ("C4", "1"): "VBUS_FILTERED",
        ("R1", "1"): "VBUS_FILTERED",
        ("U1", "25"): "GND",
        # USB-C receptacle contacts and the input protection network.
        ("J1", "A1"): "GND",
        ("J1", "A4"): "VBUS",
        ("J1", "A5"): "CC1",
        ("J1", "A9"): "VBUS",
        ("J1", "A12"): "GND",
        ("J1", "B1"): "GND",
        ("J1", "B4"): "VBUS",
        ("J1", "B5"): "CC2",
        ("J1", "B9"): "VBUS",
        ("J1", "B12"): "GND",
        ("C50", "1"): "VBUS",
        ("C50", "2"): "GND",
        ("D1", "1"): "CC1",
        ("D1", "2"): "CC2",
        ("D1", "3"): "GND",
        # Back-to-back power switch and protected sink output.
        ("T4", "1"): "VBUS_EN_SNK",
        ("T4", "2"): "VBUS",
        ("T4", "3"): "FET_MID",
        ("T1", "1"): "VSNK_GATE",
        ("T1", "2"): "FET_MID",
        ("T1", "3"): "VSNK",
        ("R10", "1"): "FET_MID",
        ("R10", "2"): "VSNK_GATE",
        ("R11", "1"): "VSNK_GATE",
        ("R11", "2"): "GND",
        ("R13", "1"): "VSNK_GATE",
        ("R13", "2"): "VBUS_EN_SNK",
        ("J10", "1"): "VSNK",
        ("J10", "2"): "GND",
        # Optional host interface connector and its pull-up network.
        ("J2", "1"): "SCL",
        ("J2", "2"): "SDA",
        ("J2", "3"): "ALERT#",
        ("J2", "4"): "RESET",
        ("J2", "5"): "VMCU",
        ("J2", "6"): "GND",
        ("R3", "1"): "VMCU",
        ("R3", "2"): "SCL",
        ("R5", "1"): "VMCU",
        ("R5", "2"): "SDA",
        ("R6", "1"): "VMCU",
        ("R6", "2"): "ALERT#",
        ("R51", "1"): "VREG_2V7",
        ("R52", "1"): "VREG_2V7",
        ("D4", "1"): "VBUS_FILTERED",
        ("D4", "2"): "VBUS",
        ("C10", "1"): "VSNK",
        ("C10", "2"): "GND",
        ("D50", "1"): "VBUS",
        ("D50", "2"): "GND",
        ("D11", "1"): "VSNK",
        ("D11", "2"): "GND",
        ("T4", "2"): "VBUS",
        ("T4", "3"): "FET_MID",
        ("T1", "2"): "FET_MID",
        ("T1", "3"): "VSNK",
        ("R2", "1"): "DISCH",
        ("R2", "2"): "VSNK",
        ("R3", "1"): "VMCU",
        ("R3", "2"): "SCL",
        ("R5", "1"): "VMCU",
        ("R5", "2"): "SDA",
        ("R6", "1"): "VMCU",
        ("R6", "2"): "ALERT#",
    }
    mismatches = {
        pin: (expected_name, actual.get(pin))
        for pin, expected_name in expected.items()
        if actual.get(pin) != expected_name
    }
    if mismatches:
        raise RuntimeError(
            f"STUSB4500 KiCad netlist connectivity mismatch: {mismatches}"
        )
    print(
        f"KiCad netlist validated {len(expected)} critical STUSB4500 pin-to-net assignments."
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
        _validate_stusb4500_netlist(cli, Path(temp))
        print(
            f"KiCad CLI validated symbol ({symbol_svgs[0].name}), "
            f"implementation ({schematic_svgs[0].name}), recursive BOM, ERC, "
            "and the STUSB4500 reference netlist."
        )


if __name__ == "__main__":
    main()
