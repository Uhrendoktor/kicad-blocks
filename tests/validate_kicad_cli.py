"""Generate a block fixture and validate it with KiCad's own command-line parser."""

import copy
import json
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
sys.path.insert(0, str(ROOT / "scripts"))

from generate_reference import ROOT_UUID, generate_reference_schematic
from kicad_blocks.model import parse_pins
from kicad_blocks.operations import export_block_bom, validate_block_tree
from kicad_blocks.pcb_layout import _all, _direct, _dump, _Quoted, parse_sexpr
from kicad_blocks.plugin import _new_blank_pcb_source
from kicad_blocks.repository import add_dependency, create_block
from validate_schematic_style import validate_schematic_style


def _find_kicad_cli() -> str:
    configured = os.environ.get("KICAD_CLI")
    candidates = [configured] if configured else []
    candidates.extend(("kicad-cli-nightly", "kicad-cli"))
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise RuntimeError("KiCad CLI was not found. Set KICAD_CLI or add kicad-cli(-nightly) to PATH.")


def _validate_stusb4500_netlist(cli: str, output_dir: Path) -> None:
    schematic = generate_reference_schematic(output_dir / "stusb4500.kicad_sch")
    style_counts = validate_schematic_style(schematic)
    print(
        "STUSB4500 schematic style audit passed: "
        + ", ".join(f"{key}={value}" for key, value in style_counts.items())
    )
    schematic_text = schematic.read_text(encoding="utf-8")
    if "(global_label" in schematic_text:
        raise RuntimeError("The STUSB4500 block must not contain global labels.")
    if '(hierarchical_label "VSYS" (shape input)' not in schematic_text:
        raise RuntimeError("The STUSB4500 block must expose VSYS as a hierarchical input.")
    if '(hierarchical_label "GND" (shape passive)' not in schematic_text:
        raise RuntimeError("The STUSB4500 block must expose GND as a hierarchical parent port.")
    if '(label "GND"' in schematic_text:
        raise RuntimeError("The STUSB4500 block must not use sheet-local GND labels.")
    netlist_path = output_dir / "stusb4500.net"
    export_command = [
        cli,
        "sch",
        "export",
        "netlist",
        "--output",
        str(netlist_path),
        str(schematic),
    ]
    loaded = subprocess.run(export_command, capture_output=True, text=True, check=False)
    if loaded.returncode:
        # KiCad CLI reports only "Failed to load schematic" for parser errors.
        # Probe copies with one top-level object class removed to pinpoint the
        # offending construct; keep the real source untouched.
        tree_for_probe = parse_sexpr(schematic_text)
        probes = {
            "without-graphics": {
                "rectangle", "polyline", "text", "circle", "arc", "bezier",
            },
            "without-labels-and-wires": {
                "label", "hierarchical_label", "global_label", "wire",
                "junction", "no_connect",
            },
            "without-hierarchical-labels": {"hierarchical_label"},
            "without-local-labels": {"label"},
            "without-wires": {"wire", "junction"},
            "without-symbol-instances": {"symbol"},
            "without-library-symbols": {"lib_symbols"},
            "without-title-block": {"title_block"},
            "without-sheet-instances": {"sheet_instances"},
            "without-libraries-and-instances": {"lib_symbols", "symbol"},
        }
        print("KiCad rejected the generated STUSB4500 sheet. Loader isolation probes:")
        print(f"baseline rc={loaded.returncode}; stderr={loaded.stderr.strip()!r}")
        for probe_name, excluded in probes.items():
            variant = copy.deepcopy(tree_for_probe)
            variant[1:] = [
                child for child in variant[1:]
                if not isinstance(child, list) or not child
                or str(child[0]) not in excluded
            ]
            variant_path = output_dir / f"stusb4500-{probe_name}.kicad_sch"
            variant_path.write_text(_dump(variant) + "\n", encoding="utf-8")
            variant_netlist = output_dir / f"stusb4500-{probe_name}.net"
            result = subprocess.run(
                [
                    cli, "sch", "export", "netlist", "--output",
                    str(variant_netlist), str(variant_path),
                ],
                capture_output=True, text=True, check=False,
            )
            print(
                f"{probe_name}: rc={result.returncode}; "
                f"stderr={result.stderr.strip()!r}"
            )
        # A hand-built empty sheet distinguishes invalid root metadata from
        # an invalid embedded symbol library or instance graph.
        minimal = [
            "kicad_sch", ["version", "20250114"],
            ["generator", _Quoted("kicad-blocks")],
            ["uuid", _Quoted("4d83d47b-34f6-52b9-a6da-6d2c4794dc61")],
            ["paper", _Quoted("A3")],
            ["lib_symbols"],
            ["sheet_instances", ["path", _Quoted("/"), ["page", _Quoted("1")]]],
            ["embedded_fonts", "no"],
        ]
        minimal_path = output_dir / "stusb4500-minimal-root.kicad_sch"
        minimal_path.write_text(_dump(minimal) + "\n", encoding="utf-8")
        minimal_result = subprocess.run(
            [cli, "sch", "export", "netlist", "--output",
             str(output_dir / "minimal-root.net"), str(minimal_path)],
            capture_output=True, text=True, check=False,
        )
        print(
            "minimal-empty-sheet: "
            f"rc={minimal_result.returncode}; "
            f"stderr={minimal_result.stderr.strip()!r}"
        )
        root_heads = {
            "version", "generator", "uuid", "paper",
            "title_block", "sheet_instances", "embedded_fonts",
        }
        root_only = [
            "kicad_sch",
            *copy.deepcopy([
                child for child in tree_for_probe[1:]
                if isinstance(child, list) and child
                and str(child[0]) in root_heads
            ]),
        ]
        top_level = [
            child for child in tree_for_probe[1:]
            if isinstance(child, list) and child
        ]
        print(
            "top-level nodes: "
            f"{[(str(child[0]), sum(str(item[0]) == str(child[0]) for item in top_level if item)) for child in top_level if child]}"
        )
        def with_heads(name: str, heads: set[str]) -> tuple[Path, int, str]:
            candidate = copy.deepcopy(root_only)
            candidate.extend(copy.deepcopy([
                child for child in top_level if str(child[0]) in heads
            ]))
            candidate_path = output_dir / f"stusb4500-{name}.kicad_sch"
            candidate_path.write_text(_dump(candidate) + "\n", encoding="utf-8")
            candidate_result = subprocess.run(
                [
                    cli, "sch", "export", "netlist", "--output",
                    str(output_dir / f"{name}.net"), str(candidate_path),
                ],
                capture_output=True, text=True, check=False,
            )
            return candidate_path, candidate_result.returncode, candidate_result.stderr.strip()

        library_heads = {"lib_symbols"}
        component_heads = {"symbol"}
        connectivity_heads = {
            "wire", "label", "hierarchical_label", "global_label", "junction", "no_connect",
        }
        graphic_heads = {"rectangle", "polyline", "text", "circle", "arc", "bezier"}
        for name, heads in (
            ("components-only", root_heads | library_heads | component_heads),
            ("graphics-only", root_heads | graphic_heads),
            ("connectivity-only", root_heads | library_heads | connectivity_heads),
            ("components-and-connectivity", root_heads | library_heads | component_heads | connectivity_heads),
            ("components-and-graphics", root_heads | library_heads | component_heads | graphic_heads),
        ):
            _, returncode, stderr = with_heads(
                name, heads - root_heads,
            )
            print(f"{name}: rc={returncode}; stderr={stderr!r}")
        for name, include_title, include_libraries in (
            ("root-metadata-with-title", True, False),
            ("root-metadata-no-title", False, False),
            ("root-metadata-with-libraries", True, True),
            ("root-metadata-libraries-no-title", False, True),
        ):
            candidate = copy.deepcopy(root_only)
            if not include_title:
                candidate[1:] = [
                    child for child in candidate[1:]
                    if not isinstance(child, list) or not child
                    or str(child[0]) != "title_block"
                ]
            if include_libraries:
                libraries = next(
                    child for child in tree_for_probe[1:]
                    if isinstance(child, list) and child and str(child[0]) == "lib_symbols"
                )
                candidate.append(copy.deepcopy(libraries))
            candidate_path = output_dir / f"stusb4500-{name}.kicad_sch"
            candidate_path.write_text(_dump(candidate) + "\n", encoding="utf-8")
            candidate_result = subprocess.run(
                [
                    cli, "sch", "export", "netlist", "--output",
                    str(output_dir / f"{name}.net"), str(candidate_path),
                ],
                capture_output=True, text=True, check=False,
            )
            print(
                f"{name}: rc={candidate_result.returncode}; "
                f"stderr={candidate_result.stderr.strip()!r}"
            )
        raise subprocess.CalledProcessError(
            loaded.returncode, export_command, loaded.stdout, loaded.stderr,
        )
    tree = parse_sexpr(netlist_path.read_text(encoding="utf-8"))
    actual: dict[tuple[str, str], str] = {}
    nets_container = sexpr_direct(tree, "nets")
    if nets_container is None:
        raise RuntimeError("KiCad netlist export has no nets section.")
    for net in sexpr_all(nets_container, "net"):
        name_node = sexpr_direct(net, "name")
        if name_node is None:
            continue
        # KiCad may qualify local labels with their sheet path. Compare the
        # actual electrical name while retaining the pin's true net identity.
        net_name = str(name_node[1]).rsplit("/", 1)[-1]
        for node in sexpr_all(net, "node"):
            ref_node = sexpr_direct(node, "ref")
            pin_node = sexpr_direct(node, "pin")
            if ref_node is not None and pin_node is not None:
                actual[(str(ref_node[1]), str(pin_node[1]))] = net_name

    expected: dict[tuple[str, str], str | None] = {
        # STUSB4500 functional pins and explicit parent-level interfaces.
        ("U1", "1"): "CC1",
        ("U1", "2"): "CC1",
        ("U1", "4"): "CC2",
        ("U1", "5"): "CC2",
        ("U1", "6"): "RESET",
        ("U1", "7"): "SCL",
        ("U1", "8"): "SDA",
        ("U1", "9"): "DISCH",
        ("U1", "10"): "GND",
        ("U1", "12"): "ADDR0",
        ("U1", "13"): "ADDR1",
        ("U1", "16"): "VBUS_EN_SNK",
        ("U1", "18"): "VBUS_VS_DISCH",
        ("U1", "19"): "ALERT#",
        ("U1", "21"): None,
        ("U1", "22"): "VSYS",
        ("U1", "23"): "VREG_2V7",
        ("U1", "24"): "VBUS",
        ("U1", "25"): "GND",
        # USB-C receptacle contacts and CC protection array.
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
        ("D1", "1"): "CC1",
        ("D1", "2"): "CC2",
        ("D1", "3"): "GND",
        # USB VBUS entry and surge protection.
        ("C50", "1"): "VBUS",
        ("C50", "2"): "GND",
        ("D50", "1"): "VBUS",
        ("D50", "2"): "GND",
        # VBUS sense/filter network.
        ("D4", "1"): "VBUS_FILTERED",
        ("D4", "2"): "VBUS",
        ("C4", "1"): "VBUS_FILTERED",
        ("C4", "2"): "GND",
        ("R1", "1"): "VBUS_FILTERED",
        ("R1", "2"): "VBUS_VS_DISCH",
        ("R4", "1"): "VREG_2V7",
        ("R4", "2"): "VBUS_VS_DISCH",
        # Controller bypass capacitors and address/reset straps.
        ("C1", "1"): None,
        ("C1", "2"): "GND",
        ("C2", "1"): "VREG_2V7",
        ("C2", "2"): "GND",
        ("C3", "1"): "VREG_2V7",
        ("C3", "2"): "GND",
        ("R51", "1"): "VREG_2V7",
        ("R51", "2"): "ADDR1",
        ("R52", "1"): "VREG_2V7",
        ("R52", "2"): "ADDR0",
        ("R53", "1"): "ADDR1",
        ("R53", "2"): "GND",
        ("R54", "1"): "ADDR0",
        ("R54", "2"): "GND",
        ("R55", "1"): "RESET",
        ("R55", "2"): "GND",
        # Back-to-back PMOS power path and local discharge/output parts.
        ("T4", "1"): "VBUS_EN_SNK",
        ("T4", "2"): "VBUS",
        ("T4", "3"): None,
        ("T1", "1"): "VSNK_GATE",
        ("T1", "2"): None,
        ("T1", "3"): "VSNK",
        ("R10", "1"): None,
        ("R10", "2"): "VSNK_GATE",
        ("R11", "1"): "VSNK_GATE",
        ("R11", "2"): "GND",
        ("R13", "1"): "VSNK_GATE",
        ("R13", "2"): "VBUS_EN_SNK",
        ("R2", "1"): "DISCH",
        ("R2", "2"): "VSNK",
        ("C10", "1"): "VSNK",
        ("C10", "2"): "GND",
        ("D11", "1"): "VSNK",
        ("D11", "2"): "GND",
    }
    mismatches = {
        pin: (expected_name, actual.get(pin))
        for pin, expected_name in expected.items()
        if expected_name is not None and actual.get(pin) != expected_name
    }
    # These nets are intentionally physical-only: the style rules require no
    # labels on regulator decouplers or the back-to-back FET midpoint. Compare
    # their component memberships rather than demanding a synthetic net name.
    physical_nets = {
        "VREG_1V2": (("U1", "21"), ("C1", "1")),
        "FET common-source midpoint": (("T4", "3"), ("T1", "2"), ("R10", "1")),
    }
    for net_description, pins in physical_nets.items():
        names = {actual.get(pin) for pin in pins}
        if None in names or len(names) != 1:
            mismatches[("physical-net", net_description)] = (
                "all pins share one physical net", sorted(str(name) for name in names),
            )
        elif next(iter(names)) in {"VREG_1V2", "FET_MID"}:
            mismatches[("physical-net", net_description)] = (
                "unnamed physical net", next(iter(names)),
            )
    if mismatches:
        schematic_tree = parse_sexpr(schematic_text)
        all_wires = []
        for wire in _all(schematic_tree, "wire"):
            points = _direct(wire, "pts")
            coordinates = [
                (round(float(point[1]), 3), round(float(point[2]), 3))
                for point in _all(points, "xy")
            ]
            all_wires.append(coordinates)
        all_labels = []
        for head in ("label", "hierarchical_label"):
            for label in _all(schematic_tree, head):
                at = _direct(label, "at")
                all_labels.append((
                    head, str(label[1]), round(float(at[1]), 3), round(float(at[2]), 3)
                ))
        raw_nets = []
        for net in _all(nets_container, "net"):
            name_node = _direct(net, "name")
            if name_node is None:
                continue
            name = str(name_node[1]).rsplit("/", 1)[-1]
            nodes = []
            for node in _all(net, "node"):
                ref_node = _direct(node, "ref")
                pin_node = _direct(node, "pin")
                if ref_node is not None and pin_node is not None:
                    nodes.append((str(ref_node[1]), str(pin_node[1])))
            if name in {"CC1", "CC2", "GND"} or any(
                pin in mismatches for pin in nodes
            ):
                raw_nets.append((name, nodes))
        raise RuntimeError(
            "STUSB4500 KiCad netlist connectivity mismatch: "
            f"{mismatches}; raw nets={raw_nets}; "
            f"all wires={all_wires}; all labels={all_labels}"
        )
    # This sheet is intentionally a child-block fixture. In isolation it
    # has no parent sheet to drive its hierarchical ports, no external MCU to
    # drive SCL, and no project symbol/footprint tables. Apply a dedicated
    # validation profile that suppresses only those expected boundary checks;
    # all internal pin conflicts, dangling wires/labels, and other ERC rules
    # retain KiCad's default severities.
    project_file = output_dir / "stusb4500.kicad_pro"
    project_settings = {
        "meta": {"filename": project_file.name, "version": 3},
        "erc": {
            "erc_exclusions": [],
            "meta": {"version": 0},
            "rule_severities": {
                "pin_not_connected": "ignore",
                "pin_not_driven": "ignore",
                "power_pin_not_driven": "ignore",
                "isolated_pin_label": "ignore",
                "lib_symbol_issues": "ignore",
                "footprint_link_issues": "ignore",
            },
        },
        "schematic": {
            "meta": {"version": 1},
            "top_level_sheets": [{
                "filename": schematic.name,
                "name": "Root",
                "uuid": ROOT_UUID,
            }],
        },
        "sheets": [[ROOT_UUID, "Root"]],
    }
    project_file.write_text(
        json.dumps(project_settings, indent=2) + "\n",
        encoding="utf-8",
    )

    erc_report = output_dir / "stusb4500-erc.rpt"
    erc_result = subprocess.run(
        [
            cli, "sch", "erc", "--output", str(erc_report),
            "--severity-all", "--exit-code-violations", str(schematic),
        ],
        capture_output=True, text=True, check=False,
    )
    if erc_result.returncode:
        report_text = (
            erc_report.read_text(encoding="utf-8", errors="replace")
            if erc_report.is_file() else erc_result.stderr.strip()
        )
        raise RuntimeError(f"STUSB4500 ERC reported violations:\n{report_text}")
    if not erc_report.is_file():
        raise RuntimeError("KiCad ERC did not write its report.")
    print("KiCad ERC: zero unignored violations under the standalone child-block profile.")
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
        if len(erc_reports) != 2 or not all(Path(item["report"]).is_file() for item in erc_reports):
            raise RuntimeError("Recursive KiCad CLI ERC did not report both block implementations.")
        # Validate the exact empty-board document used by Add PCB Layout.
        # A file-copy/unit assertion alone cannot prove that PCBNEW can open it.
        blank_board = _new_blank_pcb_source()
        try:
            blank_board_svg = Path(temp) / "blank-board-svg"
            blank_board_svg.mkdir()
            subprocess.run(
                [
                    cli,
                    "pcb",
                    "export",
                    "svg",
                    "--mode-multi",
                    "--layers",
                    "F.Cu,B.Cu",
                    "--output",
                    str(blank_board_svg),
                    str(blank_board),
                ],
                check=True,
            )
            if not list(blank_board_svg.glob("*.svg")):
                raise RuntimeError(
                    "KiCad CLI accepted the blank PCB but did not export any board-layer SVGs."
                )
        finally:
            blank_board.unlink(missing_ok=True)

        _validate_stusb4500_netlist(cli, Path(temp))
        print(
            f"KiCad CLI validated symbol ({symbol_svgs[0].name}), "
            f"implementation ({schematic_svgs[0].name}), recursive BOM, ERC, "
            "and the STUSB4500 reference netlist; the Add PCB Layout blank board also exported successfully."
        )


if __name__ == "__main__":
    main()
