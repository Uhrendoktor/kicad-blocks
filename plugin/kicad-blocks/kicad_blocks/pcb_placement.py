"""Persistent mapping from imported PCB block instances to their board-item KIIDs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, TypeGuard

from .json_types import JsonObject


class _BoardItemId(Protocol):
    """Minimal KiCad KIID surface required by the native move API."""

    value: str


def _is_board_item_id(value: object) -> TypeGuard[_BoardItemId]:
    """Narrow a dynamic KiCad binding result to an object with a string KIID."""

    return isinstance(getattr(value, "value", None), str)


PLACEMENT_SCHEMA_VERSION = 1
PLACEMENT_SIDECAR_SUFFIX = ".kicad-blocks-placement.json"


def placement_manifest_path(board_path: Path) -> Path:
    """Return the sidecar path associated with a generated PCB board."""
    board_path = Path(board_path).expanduser().resolve()
    return Path(str(board_path) + PLACEMENT_SIDECAR_SUFFIX)


def _merge_entries(result: JsonObject) -> list[JsonObject]:
    entries = result.get("placements")
    if entries is None:
        entries = [result]
    if not isinstance(entries, list) or not entries:
        raise ValueError("PCB merge result does not contain any block placements.")

    normalized: list[JsonObject] = []
    references: set[str] = set()
    item_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("PCB merge result contains a malformed placement entry.")
        reference = str(entry.get("instance_reference", "")).strip()
        if not reference:
            raise ValueError("PCB placement is missing its schematic instance reference.")
        if reference.casefold() in references:
            raise ValueError(f"PCB placement reference {reference!r} occurs more than once.")
        references.add(reference.casefold())

        raw_ids = entry.get("interactive_item_uuids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError(
                f"PCB placement {reference!r} has no imported board-item UUIDs to move."
            )
        placement_ids: list[str] = []
        for raw_id in raw_ids:
            item_id = str(raw_id).strip()
            if not item_id:
                raise ValueError(f"PCB placement {reference!r} contains an empty board-item UUID.")
            folded = item_id.casefold()
            if folded in item_ids:
                raise ValueError(f"PCB board-item UUID {item_id!r} is assigned more than once.")
            item_ids.add(folded)
            placement_ids.append(item_id)

        normalized.append(
            {
                "id": reference,
                "instance_reference": reference,
                "block": str(entry.get("block", "block")),
                "layout_name": str(entry.get("layout_name", "layout")),
                "item_uuids": placement_ids,
            }
        )
    return normalized


def write_pcb_placement_manifest(board_path: Path, merge_result: JsonObject) -> Path:
    """Write an exclusive sidecar binding each imported block to its root item IDs."""
    board_path = Path(board_path).expanduser().resolve()
    if not board_path.is_file():
        raise FileNotFoundError(f"Generated PCB board does not exist: {board_path}")
    sidecar = placement_manifest_path(board_path)
    if sidecar.exists():
        raise FileExistsError(f"PCB placement sidecar already exists: {sidecar}")

    payload = {
        "schema_version": PLACEMENT_SCHEMA_VERSION,
        "board_path": str(board_path),
        "placements": _merge_entries(merge_result),
    }
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    try:
        with sidecar.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
    except FileExistsError:
        raise FileExistsError(f"PCB placement sidecar already exists: {sidecar}") from None
    return sidecar


def read_pcb_placement_manifest(board_path: Path) -> JsonObject:
    """Load and validate a sidecar only when it belongs to the active board."""
    board_path = Path(board_path).expanduser().resolve()
    sidecar = placement_manifest_path(board_path)
    if not sidecar.is_file():
        raise FileNotFoundError(
            f"No KiCad Blocks placement manifest was found next to this board: {sidecar}"
        )
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read PCB placement manifest {sidecar}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError("PCB placement manifest must be a JSON object.")
    if payload.get("schema_version") != PLACEMENT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported PCB placement manifest schema: {payload.get('schema_version')!r}."
        )
    recorded_path = payload.get("board_path")
    if (
        not isinstance(recorded_path, str)
        or Path(recorded_path).expanduser().resolve() != board_path
    ):
        raise ValueError(
            "PCB placement manifest belongs to a different board path; refusing to move items."
        )
    placements = payload.get("placements")
    if not isinstance(placements, list) or not placements:
        raise ValueError("PCB placement manifest contains no block placements.")

    seen_references: set[str] = set()
    seen_items: set[str] = set()
    for placement in placements:
        if not isinstance(placement, dict):
            raise TypeError("PCB placement manifest contains a malformed placement.")
        reference = str(placement.get("instance_reference", "")).strip()
        if not reference or reference.casefold() in seen_references:
            raise ValueError(f"PCB placement reference is missing or duplicated: {reference!r}.")
        seen_references.add(reference.casefold())
        item_ids = placement.get("item_uuids")
        if not isinstance(item_ids, list) or not item_ids:
            raise ValueError(f"PCB placement {reference!r} has no board-item UUIDs.")
        for value in item_ids:
            item_id = str(value).strip()
            if not item_id or item_id.casefold() in seen_items:
                raise ValueError(f"PCB board-item UUID is empty or duplicated: {item_id!r}.")
            seen_items.add(item_id.casefold())
    return payload


def resolve_placement_item_ids(board, placement: JsonObject) -> list[_BoardItemId]:
    """Resolve one block's recorded UUIDs to KIID objects on the active board."""
    requested = placement.get("item_uuids")
    if not isinstance(requested, list) or not requested:
        raise ValueError("Selected PCB block placement has no board-item UUIDs.")
    requested_ids = [str(value).strip() for value in requested]
    if any(not value for value in requested_ids):
        raise ValueError("Selected PCB block placement contains an empty board-item UUID.")
    if len({value.casefold() for value in requested_ids}) != len(requested_ids):
        raise ValueError("Selected PCB block placement contains duplicate board-item UUIDs.")

    available: dict[str, _BoardItemId] = {}
    for item in board.get_items():
        item_id = getattr(item, "id", None)
        if not _is_board_item_id(item_id) or not item_id.value.strip():
            raise TypeError("KiCad returned a board item without a readable KIID value.")
        available[item_id.value.casefold()] = item_id

    missing = [value for value in requested_ids if value.casefold() not in available]
    if missing:
        raise ValueError(
            "The imported PCB block items could not all be found on this board. "
            "The board may have been edited, replaced or reverted. Missing UUIDs: "
            + ", ".join(missing)
        )
    return [available[value.casefold()] for value in requested_ids]


def move_placement_at_cursor(board, placement: JsonObject) -> int:
    """Start KiCad's native interactive move for exactly one imported block group."""
    item_ids = resolve_placement_item_ids(board, placement)
    board.interactive_move(item_ids)
    return len(item_ids)
