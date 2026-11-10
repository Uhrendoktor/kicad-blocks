"""Compose child block facade symbols into parent library implementations."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .model import (
    BlockPin,
    facade_pin_connection_points,
    revision_for_hashes,
    sha256_file,
)
from .repository import (
    BlockRecord,
    _dependency_path,
    _record,
    atomic_write,
    list_blocks,
    read_manifest,
    refresh_library_block,
    register_project_symbol_library,
)


def _child_record(
    parent: BlockRecord, dependency: dict[str, Any], records: dict[str, BlockRecord]
) -> BlockRecord:
    block_id = str(dependency.get("block_id", ""))
    child = records.get(block_id)
    if child is not None:
        return child
    path = _dependency_path(parent.path, parent.manifest, dependency)
    if path is None or not path.is_dir():
        raise FileNotFoundError(
            f"Nested block {dependency.get('name', block_id)!r} ({block_id}) was not found."
        )
    child = _record(path)
    if child.manifest["block_id"] != block_id:
        raise ValueError(f"Nested dependency path for {block_id} resolves to another block.")
    records[block_id] = child
    return child


def _pin_records(record: BlockRecord) -> list[BlockPin]:
    return [
        BlockPin(
            name=str(pin["name"]),
            electrical_type=str(pin.get("electrical_type", "passive")),
            number=str(pin.get("number", index + 1)),
        )
        for index, pin in enumerate(record.manifest.get("pins", []))
    ]


def _point_key(position) -> tuple[int, int]:
    return int(position.x), int(position.y)


def _refresh_local_revision(record: BlockRecord) -> None:
    manifest = read_manifest(record.path)
    hashes = {
        name: sha256_file(record.path / name)
        for name in manifest.get("files", [])
        if (record.path / name).is_file()
    }
    manifest["hashes"] = hashes
    manifest["revision"] = revision_for_hashes(hashes)
    manifest["version"] = int(manifest.get("version", 1)) + 1
    atomic_write(
        record.path / "manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )


def _compose_direct_children(
    parent: BlockRecord,
    dependencies: list[tuple[dict[str, Any], BlockRecord]],
    kicad_cli: str,
) -> dict[str, Any]:
    if not dependencies:
        return {"placed": [], "labels": 0}

    for dependency, _ in dependencies:
        if int(dependency.get("orientation", 0)) != 0:
            raise NotImplementedError(
                "Nested facade placement currently supports orientation 0 only."
            )

    backup_dir = Path(
        tempfile.mkdtemp(prefix=".kicad-block-compose-backup-", dir=parent.path)
    )
    schematic_path = parent.implementation
    table_path = parent.path / "sym-lib-table"
    shutil.copy2(schematic_path, backup_dir / schematic_path.name)
    if table_path.is_file():
        shutil.copy2(table_path, backup_dir / table_path.name)
    else:
        (backup_dir / "sym-lib-table.absent").write_text("", encoding="utf-8")

    kicad = None
    try:
        from kipy import KiCad
        from kipy.geometry import Vector2
        from kipy.schematic import GlobalLabel

        for _, child in dependencies:
            register_project_symbol_library(
                parent.path,
                child.manifest["library_nickname"],
                child.symbol_library,
                description=f"Nested KiCad block: {child.name}",
                prefer_relative=True,
                replace_existing=True,
            )

        kicad = KiCad(
            headless=True,
            kicad_cli_path=kicad_cli,
            file_path=str(schematic_path),
            timeout_ms=30000,
        )
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError(f"KiCad could not load block implementation {schematic_path}.")
        kicad.load_all_libraries(types=[1])
        kicad.wait_for_libraries(types=[1], timeout_s=120.0)

        symbols_by_reference = {
            item.reference: item for item in schematic.get_symbols()
        }
        labels_by_position = {
            _point_key(item.position): item
            for item in schematic.get_labels()
            if hasattr(item, "position") and hasattr(item, "text")
        }
        new_labels = []
        placed: list[str] = []
        for dependency, child in dependencies:
            reference = str(dependency.get("reference") or "B1")
            nickname = child.manifest["library_nickname"]
            symbol_name = child.manifest["symbol_name"]
            existing = symbols_by_reference.get(reference)
            if existing is not None:
                lib_id = existing.definition.id
                if (
                    lib_id.library != nickname
                    or lib_id.name != symbol_name
                ):
                    raise ValueError(
                        f"Reference {reference!r} already belongs to another symbol in "
                        f"{parent.name!r}; refusing to replace it."
                    )
            else:
                position = dependency.get("position_mm", [100.0, 160.0])
                if len(position) != 2:
                    raise ValueError(f"Invalid position for nested instance {reference!r}.")
                existing = schematic.place_symbol_from_library(
                    f"{nickname}:{symbol_name}",
                    Vector2.from_xy_mm(float(position[0]), float(position[1])),
                    orientation=0,
                    reference=reference,
                )
                symbols_by_reference[reference] = existing
                placed.append(reference)

            pin_map = dependency.get("pin_map", {})
            pin_points = facade_pin_connection_points(_pin_records(child))
            missing = sorted(set(pin_points) - set(pin_map))
            unknown = sorted(set(pin_map) - set(pin_points))
            if missing or unknown:
                raise ValueError(
                    f"Invalid pin map for {reference}: missing {missing}; unknown {unknown}."
                )
            position = dependency.get("position_mm", [100.0, 160.0])
            origin_x, origin_y = float(position[0]), float(position[1])
            for pin_name, (offset_x, offset_y, spin_style) in pin_points.items():
                net_name = str(pin_map[pin_name])
                point = Vector2.from_xy_mm(origin_x + offset_x, origin_y + offset_y)
                key = _point_key(point)
                current = labels_by_position.get(key)
                if current is not None:
                    current_text = getattr(current.text, "value", "")
                    if current_text != net_name:
                        raise ValueError(
                            f"Label position for {reference}.{pin_name} is already occupied "
                            f"by net {current_text!r}, not {net_name!r}."
                        )
                    continue
                label = GlobalLabel()
                label.position = point
                label.text.value = net_name
                label.spin_style = spin_style
                new_labels.append(label)
                labels_by_position[key] = label

        if new_labels:
            # create_items expects a complete KiCad schematic-item proto.
            # It does not generate an item's KIID as place_symbol_from_library does.
            for label in new_labels:
                label.id.value = str(uuid.uuid4())
                label.shape = 5  # SLSH_PASSIVE
            try:
                schematic.create_items(new_labels)
            except AttributeError as exc:
                # Some KiCad development builds have returned a CreateItem
                # result with no packed Any payload even though the object
                # was applied to the document. Verify document state before
                # deciding whether this was a harmless response issue.
                if "type_url" not in str(exc):
                    raise
                actual_labels = [
                    (
                        str(getattr(item.text, "value", "")),
                        _point_key(item.position),
                    )
                    for item in schematic.get_labels()
                ]
                missing_labels = [
                    (str(label.text.value), _point_key(label.position))
                    for label in new_labels
                    if (
                        str(label.text.value),
                        _point_key(label.position),
                    ) not in actual_labels
                ]
                if missing_labels:
                    raise RuntimeError(
                        "KiCad did not persist the nested block pin labels: "
                        + ", ".join(name for name, _ in missing_labels)
                    ) from exc
            schematic.save()
        if schematic.get_sheet_symbols():
            raise AssertionError(
                f"Composition of {parent.name!r} created a hierarchical sheet symbol."
            )
        return {"placed": placed, "labels": len(new_labels)}
    except Exception:
        shutil.copy2(backup_dir / schematic_path.name, schematic_path)
        if (backup_dir / "sym-lib-table.absent").exists():
            table_path.unlink(missing_ok=True)
        elif (backup_dir / table_path.name).is_file():
            shutil.copy2(backup_dir / table_path.name, table_path)
        raise
    finally:
        if kicad is not None:
            kicad.close()
        shutil.rmtree(backup_dir, ignore_errors=True)


def compose_block_implementation(
    block: BlockRecord,
    library_root: Path,
    kicad_cli: str | Path,
) -> dict[str, Any]:
    """Recursively compose dependency facade instances into parent implementations."""
    root = Path(library_root).expanduser().resolve()
    cli = str(kicad_cli)
    records = {item.manifest["block_id"]: item for item in list_blocks(root)}
    records[block.manifest["block_id"]] = block
    visiting: set[str] = set()
    completed: set[str] = set()
    reports: list[dict[str, Any]] = []

    def compose(current: BlockRecord) -> None:
        block_id = current.manifest["block_id"]
        if block_id in visiting:
            raise ValueError(f"Nested block dependency cycle detected at {current.name!r}.")
        if block_id in completed:
            return
        visiting.add(block_id)
        current_manifest = read_manifest(current.path)
        direct: list[tuple[dict[str, Any], BlockRecord]] = []
        for dependency in current_manifest.get("dependencies", []):
            child = _child_record(current, dependency, records)
            pinned_revision = dependency.get("revision")
            if pinned_revision and pinned_revision != child.manifest.get("revision"):
                raise RuntimeError(
                    f"Nested block {child.name!r} changed revision since it was linked. "
                    "Update the dependency reference before composing."
                )
            compose(child)
            direct.append((dependency, child))

        if direct:
            refreshed_manifest = read_manifest(current.path)
            revised_dependencies = []
            for dependency in refreshed_manifest.get("dependencies", []):
                child = _child_record(current, dependency, records)
                revised = dict(dependency)
                revised["revision"] = read_manifest(child.path).get("revision")
                revised_dependencies.append(revised)
            if revised_dependencies != refreshed_manifest.get("dependencies", []):
                refreshed_manifest["dependencies"] = revised_dependencies
                refreshed_manifest["version"] = int(refreshed_manifest.get("version", 1)) + 1
                atomic_write(
                    current.path / "manifest.json",
                    json.dumps(refreshed_manifest, indent=2, ensure_ascii=False) + "\n",
                )
            outcome = _compose_direct_children(current, direct, cli)
            if current.manifest.get("local_copy"):
                _refresh_local_revision(current)
            else:
                refresh_library_block(current)
            reports.append({"block": current.name, **outcome})
        visiting.remove(block_id)
        completed.add(block_id)

    compose(block)
    return {"root": block.name, "composed": reports}
