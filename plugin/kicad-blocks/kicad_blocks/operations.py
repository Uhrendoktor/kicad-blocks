"""Recursive BOM and ERC utilities for opaque library-backed blocks."""

from __future__ import annotations

import csv
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from .model import slugify
from .repository import BlockOccurrence, BlockRecord, resolve_dependency_occurrences


def find_kicad_cli() -> str:
    """Locate the KiCad command-line executable."""
    configured = os.environ.get("KICAD_CLI")
    candidates = [configured] if configured else []
    candidates.extend(("kicad-cli-nightly", "kicad-cli"))
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise FileNotFoundError(
        "KiCad CLI was not found. Set KICAD_CLI or add kicad-cli(-nightly) to PATH."
    )


def _ordered_block_tree(
    block: BlockRecord, library_root: Path | None
) -> list[BlockOccurrence]:
    return resolve_dependency_occurrences(block, library_root)


def export_block_bom(
    block: BlockRecord,
    output_file: Path,
    kicad_cli: str | Path | None = None,
    library_root: Path | None = None,
) -> dict[str, Any]:
    """Export one BOM containing the root block and its recursive dependencies.

    Each internal schematic is exported by KiCad itself. References are
    prefixed with the owning block name to keep separate implementation files
    from producing duplicate designators in the merged CSV.
    """
    cli = str(kicad_cli or find_kicad_cli())
    blocks = _ordered_block_tree(block, library_root)
    output_file = Path(output_file).expanduser().resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []
    with tempfile.TemporaryDirectory(prefix="kicad-blocks-bom-") as temp:
        for index, occurrence in enumerate(blocks):
            record = occurrence.record
            block_csv = Path(temp) / f"{index:03d}_{record.manifest['block_id'][:8]}.csv"
            command = [
                cli,
                "sch",
                "export",
                "bom",
                "--output",
                str(block_csv),
                str(record.implementation),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(
                    f"KiCad BOM export failed for block {record.name!r} "
                    f"(exit {result.returncode}): {result.stderr or result.stdout}"
                )
            if not block_csv.is_file():
                raise RuntimeError(
                    f"KiCad did not create a BOM export for block {record.name!r}."
                )

            with block_csv.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                headers = list(reader.fieldnames or [])
                if not headers:
                    raise RuntimeError(
                        f"KiCad produced an empty BOM header for block {record.name!r}."
                    )
                reference_column = next(
                    (name for name in headers if name.strip().casefold() == "reference"),
                    None,
                )
                if reference_column is None:
                    raise RuntimeError(
                        f"KiCad BOM for block {record.name!r} has no Reference column."
                    )
                for header in headers:
                    if header not in fieldnames:
                        fieldnames.append(header)
                for row in reader:
                    item = {name: value or "" for name, value in row.items() if name}
                    reference = item.get(reference_column, "").strip()
                    if reference:
                        instance_prefix = "/".join(occurrence.instance_path)
                        owner = f"{instance_prefix}/{record.name}" if instance_prefix else record.name
                        item[reference_column] = f"{owner}::{reference}"
                    item["Block"] = (
                        "/".join(occurrence.instance_path + (record.name,))
                        if occurrence.instance_path
                        else record.name
                    )
                    rows.append(item)

    if "Block" not in fieldnames:
        fieldnames.append("Block")
    with output_file.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return {
        "output": str(output_file),
        "blocks": [
            "/".join(item.instance_path + (item.record.name,))
            if item.instance_path
            else item.record.name
            for item in blocks
        ],
        "block_count": len(blocks),
        "row_count": len(rows),
    }


def validate_block_tree(
    block: BlockRecord,
    report_directory: Path,
    kicad_cli: str | Path | None = None,
    library_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Run KiCad ERC against the root implementation and every nested block.

    KiCad exit code 5 means violations were found; other non-zero exit codes
    indicate an execution or input error and abort the operation.
    """
    cli = str(kicad_cli or find_kicad_cli())
    blocks = _ordered_block_tree(block, library_root)
    report_directory = Path(report_directory).expanduser().resolve()
    report_directory.mkdir(parents=True, exist_ok=True)

    reports: list[dict[str, Any]] = []
    for index, occurrence in enumerate(blocks):
        record = occurrence.record
        owner = "_".join(occurrence.instance_path + (record.name,))
        report_path = report_directory / (
            f"{index:03d}_{slugify(owner)}_{record.manifest['block_id'][:8]}.erc.json"
        )
        command = [
            cli,
            "sch",
            "erc",
            "--format",
            "json",
            "--output",
            str(report_path),
            "--exit-code-violations",
            str(record.implementation),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode not in (0, 5):
            raise RuntimeError(
                f"KiCad ERC could not check block {record.name!r} "
                f"(exit {result.returncode}): {result.stderr or result.stdout}"
            )
        if not report_path.is_file():
            raise RuntimeError(
                f"KiCad did not create an ERC report for block {record.name!r}."
            )
        reports.append(
            {
                "block": record.name,
                "block_id": record.manifest["block_id"],
                "exit_code": result.returncode,
                "violations": result.returncode == 5,
                "report": str(report_path),
            }
        )
    return reports
