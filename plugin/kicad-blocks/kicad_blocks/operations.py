"""Recursive BOM and ERC utilities for opaque library-backed blocks."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .report_types import (
    BlockErcReport,
    BlockValidationItem,
    BomExportReport,
    LibraryValidationReport,
)
from .model import (
    resolve_profile_configuration,
    revision_for_hashes,
    sha256_file,
    slugify,
    substitute_parameter_templates,
)
from .repository import (
    BlockOccurrence,
    BlockRecord,
    read_manifest,
    resolve_dependency_occurrences,
)


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


def _implementation_for_export(
    occurrence: BlockOccurrence,
    temporary_directory: Path,
    index: int,
    root_parameter_values: dict[str, str] | None = None,
) -> Path:
    """Materialize a parameter-expanded schematic without touching library assets."""
    record = occurrence.record
    source = Path(record.implementation)
    content = source.read_text(encoding="utf-8")
    overrides = root_parameter_values if not occurrence.instance_path else None
    rendered = substitute_parameter_templates(
        content, record.manifest.get("parameters", []), overrides
    )
    if rendered == content:
        return source
    output = temporary_directory / (
        f"{index:03d}_{slugify(record.name)}_{record.manifest['block_id'][:8]}.kicad_sch"
    )
    output.write_text(rendered, encoding="utf-8")
    return output


def export_block_bom(
    block: BlockRecord,
    output_file: Path,
    kicad_cli: str | Path | None = None,
    library_root: Path | None = None,
    parameter_values: dict[str, str] | None = None,
) -> BomExportReport:
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
            implementation = _implementation_for_export(
                occurrence, Path(temp), index, parameter_values
            )
            command = [
                cli,
                "sch",
                "export",
                "bom",
                "--output",
                str(block_csv),
                "--fields",
                "Reference,Value,Footprint,QUANTITY,DNP",
                "--labels",
                "Reference,Value,Footprint,Qty,DNP",
                str(implementation),
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
                nested_facade_references = {
                    str(dependency.get("reference", "")).strip()
                    for dependency in record.manifest.get("dependencies", [])
                    if dependency.get("reference")
                }
                for row in reader:
                    item = {name: value or "" for name, value in row.items() if name}
                    reference = item.get(reference_column, "").strip()
                    # A child facade in the parent implementation is a
                    # composition placeholder, not a separate BOM line. Its
                    # real components are expanded from the child block below.
                    if reference in nested_facade_references:
                        continue
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


def validate_block_library(
    library_root: Path,
    report_directory: Path,
    kicad_cli: str | Path | None = None,
    *,
    run_erc: bool = True,
) -> LibraryValidationReport:
    """Validate every shared block's manifest, assets, profiles and dependencies.

    Optionally run recursive KiCad ERC for every block. The machine-readable
    report is written even when individual block manifests or assets are bad.
    """
    library_root = Path(library_root).expanduser().resolve()
    report_directory = Path(report_directory).expanduser().resolve()
    if not library_root.is_dir():
        raise FileNotFoundError(f"Block library directory does not exist: {library_root}")

    report_directory.mkdir(parents=True, exist_ok=True)
    report_path = report_directory / "library-validation.json"
    block_reports: list[BlockValidationItem] = []
    discovered = sorted(library_root.glob("*.kicad_block"))

    for block_path in discovered:
        item: BlockValidationItem = {
            "path": str(block_path),
            "dependency_count": 0,
            "name": block_path.stem,
            "block_id": "",
            "status": "passed",
            "errors": [],
            "warnings": [],
            "missing_assets": [],
            "hash_mismatches": [],
            "erc": [],
        }
        try:
            manifest = read_manifest(block_path)
            item["name"] = str(manifest.get("name", block_path.stem))
            item["block_id"] = str(manifest.get("block_id", ""))
            record = BlockRecord(path=block_path, manifest=manifest)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            item["status"] = "failed"
            item["errors"].append(f"Manifest: {exc}")
            block_reports.append(item)
            continue

        declared_files = {str(name) for name in manifest.get("files", [])}
        declared_hashes = manifest.get("hashes", {})
        actual_hashes: dict[str, str] = {}
        for file_name in sorted(declared_files):
            asset_path = block_path / file_name
            if not asset_path.is_file():
                item["missing_assets"].append(file_name)
                continue
            actual_hashes[file_name] = sha256_file(asset_path)
            if declared_hashes.get(file_name) != actual_hashes[file_name]:
                item["hash_mismatches"].append(file_name)

        undeclared_hashes = sorted(set(declared_hashes) - declared_files)
        if undeclared_hashes:
            item["errors"].append(
                "Hash entries are not listed in manifest.files: "
                + ", ".join(undeclared_hashes)
            )
        untracked_files = sorted(declared_files - set(declared_hashes))
        if untracked_files:
            item["errors"].append(
                "Manifest files have no corresponding hash: " + ", ".join(untracked_files)
            )
        if item["missing_assets"]:
            item["errors"].append(
                "Missing block assets: " + ", ".join(item["missing_assets"])
            )
        if item["hash_mismatches"]:
            item["errors"].append(
                "Asset checksum mismatch: " + ", ".join(item["hash_mismatches"])
            )
        if (
            not item["missing_assets"]
            and not item["hash_mismatches"]
            and not undeclared_hashes
            and not untracked_files
        ):
            calculated_revision = revision_for_hashes(actual_hashes)
            if manifest.get("revision") != calculated_revision:
                item["errors"].append(
                    "Manifest revision does not match the hashes of the current block assets."
                )

        profile_errors = []
        for profile in manifest.get("profiles", []):
            try:
                resolve_profile_configuration(manifest, str(profile["id"]))
            except (KeyError, TypeError, ValueError) as exc:
                profile_errors.append(f"{profile.get('name', profile.get('id'))}: {exc}")
        if profile_errors:
            item["errors"].append(
                "Invalid configuration profile(s): " + "; ".join(profile_errors)
            )

        try:
            occurrences = resolve_dependency_occurrences(record, library_root)
            item["dependency_count"] = max(0, len(occurrences) - 1)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            item["errors"].append(f"Dependency graph: {exc}")

        if not item["errors"] and run_erc:
            try:
                erc_reports = validate_block_tree(
                    record,
                    report_directory / "erc" / str(manifest["block_id"]),
                    kicad_cli,
                    library_root,
                )
                item["erc"] = erc_reports
                violations = [
                    result["block"] for result in erc_reports if result["violations"]
                ]
                if violations:
                    item["warnings"].append(
                        "ERC violations reported by KiCad for: " + ", ".join(violations)
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                item["errors"].append(f"Recursive ERC: {exc}")

        if item["errors"]:
            item["status"] = "failed"
        elif item["warnings"]:
            item["status"] = "warnings"
        block_reports.append(item)

    failed = [item for item in block_reports if item["status"] == "failed"]
    warning_blocks = [item for item in block_reports if item["status"] == "warnings"]
    summary = {
        "library": str(library_root),
        "report": str(report_path),
        "block_count": len(block_reports),
        "passed_count": len(block_reports) - len(failed) - len(warning_blocks),
        "warning_count": len(warning_blocks),
        "failed_count": len(failed),
        "status": "failed" if failed else ("warnings" if warning_blocks else "passed"),
        "blocks": block_reports,
    }
    report_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


def validate_block_tree(
    block: BlockRecord,
    report_directory: Path,
    kicad_cli: str | Path | None = None,
    library_root: Path | None = None,
    parameter_values: dict[str, str] | None = None,
) -> list[BlockErcReport]:
    """Run KiCad ERC against the root implementation and every nested block.

    KiCad exit code 5 means violations were found; other non-zero exit codes
    indicate an execution or input error and abort the operation.
    """
    cli = str(kicad_cli or find_kicad_cli())
    blocks = _ordered_block_tree(block, library_root)
    report_directory = Path(report_directory).expanduser().resolve()
    report_directory.mkdir(parents=True, exist_ok=True)

    reports: list[BlockErcReport] = []
    with tempfile.TemporaryDirectory(prefix="kicad-blocks-erc-") as temp:
        temporary_directory = Path(temp)
        for index, occurrence in enumerate(blocks):
            record = occurrence.record
            owner = "_".join(occurrence.instance_path + (record.name,))
            report_path = report_directory / (
                f"{index:03d}_{slugify(owner)}_{record.manifest['block_id'][:8]}.erc.json"
            )
            implementation = _implementation_for_export(
                occurrence, temporary_directory, index, parameter_values
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
                str(implementation),
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
