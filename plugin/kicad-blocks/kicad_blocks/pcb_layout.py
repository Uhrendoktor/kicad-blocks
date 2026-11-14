"""Safe file-level merge of a stored KiCad PCB block into a project board.

KiCad's public IPC Board API imports nets and exposes board objects, but has
no general add-existing-track/zone-from-another-board operation. This module
therefore creates a new validated board copy. It never overwrites the project
board currently open in PCB Editor.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .pcb_types import (
    AppliedNetclassPattern,
    BatchBlockLayoutMergeResult,
    BlockLayoutMergeResult,
    KiCadNetclassDefinition,
    KiCadProjectData,
    ProjectSettingsMergeResult,
    ResolvedBlockLayout,
)
from .sexpr import SExpr, SExprList, parse_sexpr
from .sexpr import QuotedSExpr as _Quoted
from .sexpr import dump_sexpr as _dump
from .sexpr import sexpr_all as _all
from .sexpr import sexpr_direct as _direct
from .sexpr import sexpr_head as _head

if TYPE_CHECKING:
    from .repository import BlockRecord

def _num(value: str) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid coordinate/net code token {value!r}.") from exc
    if not math.isfinite(result):
        raise ValueError("Non-finite coordinate in KiCad PCB file.")
    return result


def _fmt(value: float) -> str:
    value = round(value, 6)
    if abs(value) < 0.0000005:
        value = 0.0
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _xy(node: SExprList, dx: float, dy: float) -> None:
    if len(node) < 3:
        raise ValueError(f"Malformed coordinate node {_dump(node)}")
    node[1] = _fmt(_num(node[1]) + dx)
    node[2] = _fmt(_num(node[2]) + dy)


def _get_nets(board: SExprList) -> dict[int, str]:
    result = {0: ""}
    for node in _all(board, "net"):
        if len(node) >= 3:
            try:
                result[int(str(node[1]))] = str(node[2])
            except ValueError as exc:
                raise ValueError(f"Invalid board net declaration: {_dump(node)}") from exc
    return result


def _get_layers(board: SExprList) -> set[str]:
    layers = _direct(board, "layers")
    if layers is None:
        raise ValueError("PCB file does not define a layers list.")
    return {str(row[1]) for row in layers[1:] if isinstance(row, list) and len(row) >= 2}


def _reference(footprint: SExprList) -> str | None:
    for prop in _all(footprint, "property"):
        if len(prop) >= 3 and str(prop[1]).casefold() == "reference":
            return str(prop[2])
    return None


def _refresh_ids(node: SExpr, id_remap: dict[str, str] | None = None) -> None:
    """Regenerate item IDs, optionally retaining old-to-new UUID correspondence."""
    if not isinstance(node, list):
        return
    if _head(node) in ("uuid", "tstamp") and len(node) >= 2:
        old_id = str(node[1])
        new_id = str(uuid.uuid4())
        if id_remap is not None:
            id_remap[old_id] = new_id
        node[1] = _Quoted(new_id)
    for child in node[1:]:
        _refresh_ids(child, id_remap)


def _rewrite_nets(
    node: SExpr,
    mapping: dict[int, tuple[int, str]],
    *,
    include_net_name: bool,
) -> None:
    """Rewrite PCB net references using KiCad's per-object file schema.

    Footprint pads store both (net CODE NAME); routed board objects
    (segments, arcs, vias and zones) store only (net CODE).
    """
    if not isinstance(node, list):
        return
    if _head(node) == "net" and len(node) >= 2:
        try:
            old_code = int(str(node[1]))
        except ValueError as exc:
            raise ValueError(f"Invalid net reference: {_dump(node)}") from exc
        if old_code not in mapping:
            raise ValueError(f"Board item references undefined source net code {old_code}.")
        code, name = mapping[old_code]
        if include_net_name:
            node[1:] = [str(code), _Quoted(name)]
        else:
            node[1:] = [str(code)]
        return
    for child in node[1:]:
        _rewrite_nets(child, mapping, include_net_name=include_net_name)


def _transform_point(
    node: SExprList,
    *,
    anchor_mm: tuple[float, float],
    position_mm: tuple[float, float],
    rotation_deg: float,
) -> None:
    """Rotate a board-absolute point about the stored anchor, then place it."""
    if len(node) < 3:
        raise ValueError(f"Malformed coordinate node {_dump(node)}")
    source_x = _num(node[1]) - anchor_mm[0]
    source_y = _num(node[2]) - anchor_mm[1]
    radians = math.radians(rotation_deg)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    # KiCad board coordinates have positive Y downward. A positive angle
    # therefore uses this sign convention for the documented CCW rotation.
    node[1] = _fmt(position_mm[0] + source_x * cosine + source_y * sine)
    node[2] = _fmt(position_mm[1] - source_x * sine + source_y * cosine)


def _rotate_angle_node(node: SExprList, rotation_deg: float) -> None:
    """Add the block rotation to an item's existing absolute orientation."""
    if len(node) >= 4:
        try:
            angle = _num(node[3])
        except ValueError:
            return
        node[3] = _fmt((angle + rotation_deg) % 360.0)


def _transform_item(
    node: SExprList,
    *,
    anchor_mm: tuple[float, float],
    position_mm: tuple[float, float],
    rotation_deg: float,
) -> None:
    """Rotate and translate board-absolute geometry, preserving footprint-local data."""
    if _head(node) == "footprint":
        at = _direct(node, "at")
        if at is None:
            raise ValueError("Stored PCB footprint has no placement coordinate.")
        _transform_point(
            at,
            anchor_mm=anchor_mm,
            position_mm=position_mm,
            rotation_deg=rotation_deg,
        )
        _rotate_angle_node(at, rotation_deg)
        for prop in _all(node, "property"):
            prop_at = _direct(prop, "at")
            if prop_at is not None:
                _transform_point(
                    prop_at,
                    anchor_mm=anchor_mm,
                    position_mm=position_mm,
                    rotation_deg=rotation_deg,
                )
                _rotate_angle_node(prop_at, rotation_deg)
        return

    absolute_tags = {"at", "start", "mid", "end", "center", "xy", "pts"}

    def walk(item: SExpr) -> None:
        if not isinstance(item, list):
            return
        tag = _head(item)
        if tag in absolute_tags:
            if tag == "pts":
                for point in item[1:]:
                    if _head(point) == "xy":
                        _transform_point(
                            point,
                            anchor_mm=anchor_mm,
                            position_mm=position_mm,
                            rotation_deg=rotation_deg,
                        )
            else:
                _transform_point(
                    item,
                    anchor_mm=anchor_mm,
                    position_mm=position_mm,
                    rotation_deg=rotation_deg,
                )
                if tag == "at":
                    _rotate_angle_node(item, rotation_deg)
            return
        for child in item[1:]:
            walk(child)

    walk(node)


def _rewrite_footprint(
    footprint: SExprList,
    reference: str,
    dx: float,
    dy: float,
    *,
    uuid_remap: dict[str, str] | None = None,
) -> None:
    _transform_item(
        footprint,
        anchor_mm=(0.0, 0.0),
        position_mm=(dx, dy),
        rotation_deg=0.0,
    )
    for prop in _all(footprint, "property"):
        if len(prop) >= 3 and str(prop[1]).casefold() == "reference":
            prop[2] = _Quoted(reference)
    for fp_text in _all(footprint, "fp_text"):
        if len(fp_text) >= 3 and str(fp_text[1]).casefold() == "reference":
            fp_text[2] = _Quoted(reference)
    _refresh_ids(footprint, uuid_remap)
    path_node = _direct(footprint, "path")
    if path_node is None:
        footprint.append(["path", _Quoted("/" + str(uuid.uuid4()))])
    else:
        path_node[1:] = [_Quoted("/" + str(uuid.uuid4()))]


def _unique_reference(base: str, existing: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate.casefold() in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    existing.add(candidate.casefold())
    return candidate


def _merge_net_chain_sections(
    source_board: SExprList,
    target_board: SExprList,
    *,
    source_nets: dict[int, str],
    source_net_to_board_net: dict[str, str],
    pad_uuid_remap: dict[str, str],
    instance_reference: str,
    existing_chain_names: set[str],
) -> dict[str, str]:
    """Copy board net-chain topology with mapped nets, unique names and pad UUIDs."""
    source_section = _direct(source_board, "net_chains")
    if source_section is None:
        return {}

    target_section = _direct(target_board, "net_chains")
    if target_section is None:
        target_section = ["net_chains"]
        target_board.append(target_section)

    # Include names already present in the destination PCB section.
    for chain in target_section[1:]:
        if _head(chain) != "net_chain":
            continue
        name_node = _direct(chain, "name")
        if name_node is not None and len(name_node) >= 2:
            existing_chain_names.add(str(name_node[1]).casefold())

    chain_name_map: dict[str, str] = {}
    for raw_chain in source_section[1:]:
        if _head(raw_chain) != "net_chain":
            continue
        chain = parse_sexpr(_dump(raw_chain))
        name_node = _direct(chain, "name")
        if name_node is None or len(name_node) < 2:
            raise ValueError("Stored PCB net-chain record has no name.")
        source_name = str(name_node[1])
        safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", source_name).strip("_") or "CHAIN"
        destination_name = _unique_reference(
            f"{instance_reference}_{safe_name}",
            existing_chain_names,
        )
        name_node[1] = _Quoted(destination_name)
        chain_name_map[source_name] = destination_name

        members = _direct(chain, "members")
        if members is not None:

            def rewrite_member(node: SExpr, chain_name: str = source_name) -> None:
                if not isinstance(node, list):
                    return
                if _head(node) == "net" and len(node) >= 2:
                    raw_net = str(node[1])
                    try:
                        source_net_name = source_nets.get(int(raw_net), "")
                    except ValueError:
                        source_net_name = raw_net
                    destination_net = source_net_to_board_net.get(source_net_name)
                    if destination_net is None:
                        raise ValueError(
                            f"Net chain {chain_name!r} references source net "
                            f"{source_net_name or raw_net!r}, which is not part of the imported block nets."
                        )
                    node[1] = _Quoted(destination_net)
                    return
                for child in node[1:]:
                    rewrite_member(child)

            rewrite_member(members)

        for terminal_pad in _all(chain, "terminal_pad"):
            if len(terminal_pad) < 2:
                raise ValueError(f"Net chain {source_name!r} has a malformed terminal-pad UUID.")
            source_uuid = str(terminal_pad[1])
            destination_uuid = pad_uuid_remap.get(source_uuid)
            if destination_uuid is None:
                raise ValueError(
                    f"Net chain {source_name!r} refers to terminal pad {source_uuid}, "
                    "but that pad was not included in the imported block footprints."
                )
            terminal_pad[1] = _Quoted(destination_uuid)

        target_section.append(chain)

    return chain_name_map


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def _normalise_class_assignment(value: str | list[str] | None) -> list[str]:
    """Normalize old single-class and current list-valued project assignments."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError(f"Invalid KiCad netclass assignment value: {value!r}")


def _netclass_signature(netclass: KiCadNetclassDefinition) -> str:
    """Compare classes by their electrical properties, excluding identity/order."""
    values = dict(netclass)
    values.pop("name", None)
    values.pop("priority", None)
    values.pop("nets", None)
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def _expand_netclass_bus_pattern(pattern: str) -> list[str] | None:
    """Expand KiCad vector/group bus notation used in netclass pattern rules.

    Returns None when the expression looks like a bus pattern but uses syntax
    this file-level importer does not safely expand.
    """
    vector = re.fullmatch(r"(.*?)\[(\d+)\.\.(\d+)\]([+-PN]*)", pattern)
    if vector:
        prefix, start_text, end_text, suffix = vector.groups()
        start, end = int(start_text), int(end_text)
        if start == end:
            return None
        lo, hi = sorted((start, end))
        width = max(len(start_text), len(end_text))
        padded = (len(start_text) > 1 and start_text.startswith("0")) or (
            len(end_text) > 1 and end_text.startswith("0")
        )
        return [
            f"{prefix}{str(value).zfill(width) if padded else value}{suffix}"
            for value in range(lo, hi + 1)
        ]

    group = re.fullmatch(r"(.*?)\{(.+)\}", pattern)
    if group:
        _prefix, body = group.groups()
        members = [item for item in re.split(r"[\s,]+", body.strip()) if item]
        if not members:
            return None
        expanded: list[str] = []
        for member in members:
            member_expansion = _expand_netclass_bus_pattern(member)
            if member_expansion is None:
                if any(char in member for char in "[]{}"):
                    return None
                expanded.append(member)
            else:
                expanded.extend(member_expansion)
        return expanded

    # An unmatched bracket/brace is not a valid KiCad bus expression. Leave
    # it out of the exact-net expansion rather than changing its semantics.
    if any(char in pattern for char in "[]{}"):
        return None
    return [pattern]


def _matches_netclass_pattern(pattern: str, net_name: str) -> bool:
    """Mirror KiCad's anchored CTX_NETCLASS regex + wildcard matcher.

    KiCad tries a regular expression first and an anchored wildcard matcher
    second. In wildcard patterns, '*' matches any run and '?' matches one
    character; other regex metacharacters are escaped by the wildcard matcher.
    """
    try:
        if re.fullmatch(pattern, net_name):
            return True
    except re.error:
        pass

    wildcard_regex = "".join(
        ".*" if char == "*" else "." if char == "?" else re.escape(char) for char in pattern
    )
    try:
        return re.fullmatch(wildcard_regex, net_name) is not None
    except re.error:
        return False


def _merge_project_net_settings(
    target_project: Path,
    source_project: Path | None,
    output_project: Path,
    *,
    instance_reference: str,
    layout_ports: dict[str, str],
    pin_to_board_net: dict[str, str],
    source_net_to_board_net: dict[str, str] | None = None,
    source_chain_name_map: dict[str, str] | None = None,
) -> ProjectSettingsMergeResult | None:
    """Build a copied project file with block netclasses/assignments merged.

    The target project's settings remain authoritative. Named block netclasses
    are reused if identical, otherwise namespaced on collision. Exact assignments
    are remapped to project net names. Netclass regex/wildcard rules are resolved
    against only the nets physically present in the stored block PCB, then
    converted to exact assignments on the corresponding imported project nets.
    This avoids copying a broad pattern that could affect unrelated project nets.
    """
    if not target_project.is_file() and (source_project is None or not source_project.is_file()):
        return None

    if target_project.is_file():
        raw_target_data = json.loads(target_project.read_text(encoding="utf-8"))
        if not isinstance(raw_target_data, dict):
            raise ValueError(f"Invalid KiCad project JSON: {target_project}")
        target_data = cast(KiCadProjectData, raw_target_data)
    else:
        target_data: KiCadProjectData = {
            "meta": {"filename": output_project.name, "version": 3},
            "net_settings": {"classes": [], "netclass_assignments": {}},
        }

    if source_project is not None and source_project.is_file():
        raw_source_data = json.loads(source_project.read_text(encoding="utf-8"))
        if not isinstance(raw_source_data, dict):
            raise ValueError(f"Invalid block project-settings JSON: {source_project}")
        source_data = cast(KiCadProjectData, raw_source_data)
        source_settings = source_data.get("net_settings", {})
        if not isinstance(source_settings, dict):
            raise ValueError("Block project file has an invalid net_settings section.")
    else:
        source_settings = {}

    target_settings = target_data.setdefault("net_settings", {})
    if not isinstance(target_settings, dict):
        raise TypeError("Target project file has an invalid net_settings section.")
    target_classes = target_settings.setdefault("classes", [])
    source_classes = source_settings.get("classes", [])
    if not isinstance(target_classes, list) or not isinstance(source_classes, list):
        raise TypeError("KiCad net_settings.classes must be arrays.")

    class_by_name: dict[str, KiCadNetclassDefinition] = {
        str(item["name"]): item
        for item in target_classes
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    priorities = [
        int(item.get("priority", 0))
        for item in target_classes
        if isinstance(item, dict)
        and str(item.get("name", "")).casefold() != "default"
        and isinstance(item.get("priority", 0), int)
    ]
    next_priority = max(priorities, default=-1) + 1
    class_map: dict[str, str] = {}
    merged_classes: list[str] = []

    for source_class in source_classes:
        if not isinstance(source_class, dict) or not isinstance(source_class.get("name"), str):
            raise TypeError(f"Malformed block netclass definition: {source_class!r}")
        original_name = source_class["name"]
        if original_name.casefold() == "default":
            class_map[original_name] = "Default"
            continue

        signature = _netclass_signature(source_class)
        current = class_by_name.get(original_name)
        if current is not None and _netclass_signature(current) == signature:
            class_map[original_name] = original_name
            continue

        desired_name = original_name
        if current is not None:
            safe_class = re.sub(r"[^A-Za-z0-9_]+", "_", original_name).strip("_") or "Class"
            base_name = f"{instance_reference}_{safe_class}"
            desired_name = base_name
            suffix = 2
            while desired_name in class_by_name:
                candidate = class_by_name[desired_name]
                if _netclass_signature(candidate) == signature:
                    break
                desired_name = f"{base_name}_{suffix}"
                suffix += 1
        if desired_name not in class_by_name:
            copied = dict(source_class)
            copied.pop("nets", None)
            copied["name"] = desired_name
            copied["priority"] = next_priority
            next_priority += 1
            target_classes.append(copied)
            class_by_name[desired_name] = copied
            merged_classes.append(desired_name)
        class_map[original_name] = desired_name

    source_assignments = source_settings.get("netclass_assignments") or {}
    target_assignments = target_settings.get("netclass_assignments") or {}
    if not isinstance(source_assignments, dict) or not isinstance(target_assignments, dict):
        raise TypeError("KiCad net_settings.netclass_assignments must be an object.")
    target_assignments = {
        str(net_name): _normalise_class_assignment(classes)
        for net_name, classes in target_assignments.items()
    }

    reverse_ports: dict[str, str] = {}
    for pin_name, source_net in layout_ports.items():
        destination = str(pin_to_board_net[pin_name]).strip()
        if source_net in reverse_ports and reverse_ports[source_net] != destination:
            raise ValueError(f"Layout port net {source_net!r} maps to two project nets.")
        reverse_ports[str(source_net)] = destination

    assigned_nets: list[str] = []
    for source_net, classes in source_assignments.items():
        destination_net = reverse_ports.get(str(source_net))
        if destination_net is None:
            safe_net = re.sub(r"[^A-Za-z0-9_./+-]+", "_", str(source_net)).strip("_")
            destination_net = f"{instance_reference}_{safe_net or 'NET'}"
        mapped_classes = []
        for source_class_name in _normalise_class_assignment(classes):
            if source_class_name not in class_map:
                raise ValueError(
                    f"Block net {source_net!r} references undefined netclass {source_class_name!r}."
                )
            target_class_name = class_map[source_class_name]
            if (
                target_class_name.casefold() != "default"
                and target_class_name not in mapped_classes
            ):
                mapped_classes.append(target_class_name)
        if mapped_classes:
            current = target_assignments.setdefault(destination_net, [])
            for class_name in mapped_classes:
                if class_name not in current:
                    current.append(class_name)
            if destination_net not in assigned_nets:
                assigned_nets.append(destination_net)

    applied_patterns: list[AppliedNetclassPattern] = []
    unmatched_patterns: list[str] = []
    source_net_map = source_net_to_board_net or {}
    source_patterns = source_settings.get("netclass_patterns") or []
    if not isinstance(source_patterns, list):
        raise TypeError("KiCad net_settings.netclass_patterns must be an array.")

    for entry in source_patterns:
        if not isinstance(entry, dict):
            unmatched_patterns.append(repr(entry))
            continue
        pattern = entry.get("pattern")
        class_name = entry.get("netclass")
        if not isinstance(pattern, str) or not isinstance(class_name, str):
            unmatched_patterns.append(repr(entry))
            continue
        if class_name not in class_map:
            raise ValueError(
                f"Block netclass pattern {pattern!r} references undefined netclass {class_name!r}."
            )
        expanded_patterns = _expand_netclass_bus_pattern(pattern)
        if expanded_patterns is None:
            unmatched_patterns.append(pattern)
            continue

        resolved_nets: list[str] = []
        for source_net_name, destination_net in source_net_map.items():
            if not any(
                _matches_netclass_pattern(member_pattern, str(source_net_name))
                for member_pattern in expanded_patterns
            ):
                continue
            mapped_class = class_map[class_name]
            if mapped_class.casefold() == "default":
                continue
            current = target_assignments.setdefault(str(destination_net), [])
            if mapped_class not in current:
                current.append(mapped_class)
            if str(destination_net) not in resolved_nets:
                resolved_nets.append(str(destination_net))

        if resolved_nets:
            applied_patterns.append(
                {
                    "pattern": pattern,
                    "netclass": class_name,
                    "resolved_nets": resolved_nets,
                }
            )
        else:
            unmatched_patterns.append(pattern)

    target_settings["netclass_assignments"] = target_assignments
    metadata = target_data.setdefault("meta", {})
    if not isinstance(metadata, dict):
        raise TypeError("KiCad project meta section must be an object.")
    metadata["filename"] = output_project.name
    metadata.setdefault("version", 3)

    ignored_patterns = bool(unmatched_patterns)
    chain_name_map = source_chain_name_map or {}
    source_chain_classes = source_settings.get("net_chain_classes") or {}
    source_chain_netclasses = source_settings.get("net_chain_netclasses") or {}
    target_chain_classes = target_settings.get("net_chain_classes") or {}
    target_chain_netclasses = target_settings.get("net_chain_netclasses") or {}
    target_settings["net_chain_classes"] = target_chain_classes
    target_settings["net_chain_netclasses"] = target_chain_netclasses
    if not all(
        isinstance(value, dict)
        for value in (
            source_chain_classes,
            source_chain_netclasses,
            target_chain_classes,
            target_chain_netclasses,
        )
    ):
        raise TypeError("KiCad net-chain settings must be objects.")

    unmatched_chains: list[str] = []
    merged_chain_names: list[str] = []
    for source_name, source_class in source_chain_classes.items():
        destination_name = chain_name_map.get(str(source_name))
        if destination_name is None:
            unmatched_chains.append(f"net_chain_classes:{source_name}")
            continue
        target_chain_classes[destination_name] = str(source_class)
        if destination_name not in merged_chain_names:
            merged_chain_names.append(destination_name)

    for source_name, source_netclass in source_chain_netclasses.items():
        destination_name = chain_name_map.get(str(source_name))
        if destination_name is None:
            unmatched_chains.append(f"net_chain_netclasses:{source_name}")
            continue
        source_netclass = str(source_netclass)
        if source_netclass not in class_map:
            raise ValueError(
                f"Net chain {source_name!r} references undefined netclass {source_netclass!r}."
            )
        target_chain_netclasses[destination_name] = class_map[source_netclass]
        if destination_name not in merged_chain_names:
            merged_chain_names.append(destination_name)

    ignored_chain_classes = bool(unmatched_chains)
    return {
        "data": target_data,
        "output": str(output_project),
        "merged_classes": merged_classes,
        "assigned_nets": assigned_nets,
        "ignored_patterns": ignored_patterns,
        "applied_patterns": applied_patterns,
        "unmatched_patterns": unmatched_patterns,
        "ignored_chain_classes": ignored_chain_classes,
        "merged_chain_names": merged_chain_names,
        "unmatched_chains": unmatched_chains,
    }


def resolve_block_layout(block: BlockRecord, layout_id: str | None = None) -> ResolvedBlockLayout:
    """Resolve a named layout asset, retaining support for legacy single-layout blocks."""
    layouts = block.manifest.get("layouts") or []
    if layouts:
        if layout_id is None:
            default_layout = block.manifest.get("default_layout")
            selected = next(
                (
                    item
                    for item in layouts
                    if default_layout is not None
                    and str(item.get("id", "")).casefold() == str(default_layout).casefold()
                ),
                None,
            )
            if selected is None and len(layouts) > 1:
                raise ValueError(
                    f"Block {block.name!r} has multiple PCB layouts and no valid default; "
                    "choose one explicitly."
                )
            if selected is None:
                selected = layouts[0]
        else:
            selected = next(
                (
                    item
                    for item in layouts
                    if str(item.get("id", "")).casefold() == str(layout_id).casefold()
                    or str(item.get("name", "")).casefold() == str(layout_id).casefold()
                ),
                None,
            )
            if selected is None:
                available = ", ".join(str(item.get("name", item.get("id", ""))) for item in layouts)
                raise ValueError(
                    f"Unknown PCB layout {layout_id!r} for {block.name!r}. Available: {available}."
                )
        return {
            "id": str(selected["id"]),
            "name": str(selected.get("name", selected["id"])),
            "file": str(selected["file"]),
            "project_file": selected.get("project_file"),
            "ports": selected.get("ports"),
        }

    legacy_file = block.manifest.get("layout_file")
    if not legacy_file:
        raise ValueError(f"Block {block.name!r} has no stored PCB layout.")
    if layout_id not in (None, "", "default"):
        raise ValueError(f"Block {block.name!r} has only its legacy PCB layout.")
    return {
        "id": None,
        "name": Path(str(legacy_file)).stem,
        "file": str(legacy_file),
        "project_file": block.manifest.get("layout_project_file", "layout.kicad_pro"),
        "ports": block.manifest.get("layout_ports"),
    }


@dataclass(frozen=True, slots=True)
class BlockLayoutPlacement:
    """One schematic block occurrence and its desired PCB placement."""

    block: BlockRecord
    instance_reference: str
    position_mm: tuple[float, float]
    pin_to_board_net: dict[str, str]
    layout_anchor_mm: tuple[float, float] = (0.0, 0.0)
    rotation_deg: float = 0.0
    layout_id: str | None = None


def merge_block_layouts(
    target_board: Path,
    placements: list[BlockLayoutPlacement],
    output_board: Path,
) -> BatchBlockLayoutMergeResult:
    """Apply several block layouts to one new output board, in order.

    Intermediate board/project copies live in a temporary directory. The
    original project files are never modified. Each block is merged using the
    same validation, netclass, net-chain and geometry rules as the single-block
    operation.
    """
    target_board = Path(target_board).expanduser().resolve()
    output_board = Path(output_board).expanduser().resolve()
    placements = list(placements)
    if not placements:
        raise ValueError("Select at least one block layout to merge.")
    if output_board == target_board:
        raise ValueError("Output must be a new file; the project board is never overwritten.")
    if output_board.exists():
        raise FileExistsError(
            f"Output PCB already exists and will not be overwritten: {output_board}"
        )
    if output_board.with_suffix(".kicad_pro").exists():
        raise FileExistsError(
            "Output project settings already exist and will not be overwritten: "
            + str(output_board.with_suffix(".kicad_pro"))
        )

    references = [item.instance_reference.casefold() for item in placements]
    if len(references) != len(set(references)):
        raise ValueError("Each selected block instance must have a unique reference designator.")

    results: list[BlockLayoutMergeResult] = []
    current_board = target_board
    with tempfile.TemporaryDirectory(prefix="kicad-block-batch-") as temp:
        stage_root = Path(temp)
        for index, item in enumerate(placements):
            is_last = index == len(placements) - 1
            stage_output = output_board if is_last else stage_root / f"stage_{index + 1}.kicad_pcb"
            result = merge_block_layout(
                current_board,
                item.block,
                stage_output,
                instance_reference=item.instance_reference,
                position_mm=item.position_mm,
                pin_to_board_net=item.pin_to_board_net,
                layout_anchor_mm=item.layout_anchor_mm,
                rotation_deg=item.rotation_deg,
                layout_id=item.layout_id,
            )
            results.append(result)
            current_board = stage_output

    def flatten(values_by_placement: Sequence[list[str]]) -> list[str]:
        values: list[str] = []
        for placement_values in values_by_placement:
            for value in placement_values:
                if value not in values:
                    values.append(value)
        return values

    return {
        "output": str(output_board),
        "project_file": (
            str(output_board.with_suffix(".kicad_pro"))
            if output_board.with_suffix(".kicad_pro").is_file()
            else None
        ),
        "block_count": len(results),
        "placements": results,
        "footprints": flatten([item["footprints"] for item in results]),
        "footprint_count": sum(item["footprint_count"] for item in results),
        "object_count": sum(item["object_count"] for item in results),
        "new_net_count": sum(item["new_net_count"] for item in results),
        "new_nets": flatten([item["new_nets"] for item in results]),
        "merged_netclasses": flatten([item["merged_netclasses"] for item in results]),
        "assigned_project_nets": flatten([item["assigned_project_nets"] for item in results]),
        "applied_project_patterns": flatten([item["applied_project_patterns"] for item in results]),
        "unmatched_project_patterns": flatten(
            [item["unmatched_project_patterns"] for item in results]
        ),
        "merged_project_chain_names": flatten(
            [item["merged_project_chain_names"] for item in results]
        ),
        "unmatched_project_chains": flatten([item["unmatched_project_chains"] for item in results]),
        "merged_board_chains": flatten([item["merged_board_chains"] for item in results]),
        "skipped_top_level_items": flatten([item["skipped_top_level_items"] for item in results]),
        "ignored_project_patterns": any(item["ignored_project_patterns"] for item in results),
        "ignored_project_chain_classes": any(
            item["ignored_project_chain_classes"] for item in results
        ),
    }


def merge_block_layout(
    target_board: Path,
    block: BlockRecord,
    output_board: Path,
    *,
    instance_reference: str,
    position_mm: tuple[float, float],
    pin_to_board_net: dict[str, str],
    layout_anchor_mm: tuple[float, float] = (0.0, 0.0),
    rotation_deg: float = 0.0,
    layout_id: str | None = None,
) -> BlockLayoutMergeResult:
    """Merge a stored block layout into a new copy of a project board.

    The manifest's optional layout_ports map relates facade pin names to
    the net names used in the stored PCB. Without it, names are assumed equal.
    Internal source nets are namespaced with the block instance reference.
    The input project board is never modified.
    """
    target_board = Path(target_board).expanduser().resolve()
    output_board = Path(output_board).expanduser().resolve()
    selected_layout = resolve_block_layout(block, layout_id)
    layout_name = selected_layout["file"]
    source_board = (Path(block.path) / layout_name).resolve()
    layout_project_file = selected_layout.get("project_file")
    source_project = (
        (Path(block.path) / str(layout_project_file)).resolve() if layout_project_file else None
    )
    target_project = target_board.with_suffix(".kicad_pro")
    output_project = output_board.with_suffix(".kicad_pro")
    if not target_board.is_file() or not source_board.is_file():
        raise FileNotFoundError("Project board or stored block PCB layout does not exist.")
    if output_board == target_board:
        raise ValueError("Output must be a new file; the project board is never overwritten.")
    if output_board.exists():
        raise FileExistsError(
            f"Output PCB already exists and will not be overwritten: {output_board}"
        )
    if output_project.exists():
        raise FileExistsError(
            f"Output project settings already exist and will not be overwritten: {output_project}"
        )
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", instance_reference):
        raise ValueError(
            "Block instance reference must start with a letter and use only letters, digits and underscores."
        )
    if len(position_mm) != 2 or len(layout_anchor_mm) != 2:
        raise ValueError("Placement position and layout anchor must each contain two coordinates.")
    x, y = map(float, position_mm)
    anchor_x, anchor_y = map(float, layout_anchor_mm)
    rotation_deg = float(rotation_deg)
    if not all(math.isfinite(value) for value in (x, y, anchor_x, anchor_y, rotation_deg)):
        raise ValueError("Placement position, layout anchor and rotation must be finite.")
    rotation_deg %= 360.0

    pins = {str(pin["name"]) for pin in block.manifest.get("pins", [])}
    if set(pin_to_board_net) != pins:
        raise ValueError(f"Map every facade pin exactly once. Expected {sorted(pins)}.")
    if any(not str(name).strip() for name in pin_to_board_net.values()):
        raise ValueError("Target board net names cannot be empty.")

    target = parse_sexpr(target_board.read_text(encoding="utf-8"))
    source = parse_sexpr(source_board.read_text(encoding="utf-8"))
    if _head(target) != "kicad_pcb" or _head(source) != "kicad_pcb":
        raise ValueError("Both files must be KiCad PCB S-expression documents.")
    missing_layers = sorted(_get_layers(source) - _get_layers(target))
    if missing_layers:
        raise ValueError(
            "Project board lacks layers required by the block: " + ", ".join(missing_layers)
        )

    source_nets = _get_nets(source)
    target_nets = _get_nets(target)
    target_codes = {name: code for code, name in target_nets.items()}
    next_code = max(target_nets, default=0) + 1
    ports = (
        selected_layout.get("ports")
        or block.manifest.get("layout_ports")
        or {pin: pin for pin in pins}
    )
    if set(ports) != pins:
        raise ValueError("Block layout_ports must map every facade pin exactly once.")
    port_map: dict[str, str] = {}
    for pin, layout_net in ports.items():
        layout_net = str(layout_net)
        destination = str(pin_to_board_net[pin]).strip()
        if layout_net in port_map and port_map[layout_net] != destination:
            raise ValueError(f"Layout net {layout_net!r} maps to two project nets.")
        port_map[layout_net] = destination

    net_map: dict[int, tuple[int, str]] = {0: (0, "")}
    new_net_nodes: list[list] = []
    for old_code, source_name in source_nets.items():
        if old_code == 0:
            continue
        if source_name in port_map:
            destination_name = port_map[source_name]
        else:
            safe_name = (
                re.sub(r"[^A-Za-z0-9_./+-]+", "_", source_name).strip("_") or f"NET{old_code}"
            )
            destination_name = f"{instance_reference}_{safe_name}"
        destination_code = target_codes.get(destination_name)
        if destination_code is None:
            destination_code = next_code
            next_code += 1
            target_codes[destination_name] = destination_code
            new_net_nodes.append(["net", str(destination_code), _Quoted(destination_name)])
        net_map[old_code] = (destination_code, destination_name)

    existing_references = {
        reference.casefold()
        for item in target[1:]
        if _head(item) == "footprint"
        if (reference := _reference(item))
    }
    supported = {
        "footprint",
        "segment",
        "arc",
        "via",
        "zone",
        "gr_line",
        "gr_arc",
        "gr_circle",
        "gr_rect",
        "gr_poly",
        "gr_curve",
        "gr_text",
        "gr_text_box",
        "dimension",
        "target",
    }
    imported: list[list] = []
    imported_references: list[str] = []
    interactive_item_uuids: list[str] = []
    skipped: set[str] = set()
    pad_uuid_remap: dict[str, str] = {}
    anchor = (anchor_x, anchor_y)
    position = (x, y)
    for raw_item in source[1:]:
        kind = _head(raw_item)
        if kind in {
            "version",
            "generator",
            "general",
            "paper",
            "layers",
            "setup",
            "net",
            "net_class",
            "net_chains",
            "embedded_fonts",
            "uuid",
        }:
            continue
        if kind not in supported:
            if kind:
                skipped.add(kind)
            continue
        item = parse_sexpr(_dump(raw_item))
        if kind == "footprint":
            old_reference = _reference(item)
            if not old_reference:
                raise ValueError("Every stored layout footprint must have a Reference property.")
            new_reference = _unique_reference(
                f"{instance_reference}_{old_reference}", existing_references
            )
            _rewrite_footprint(item, new_reference, 0.0, 0.0, uuid_remap=pad_uuid_remap)
            _transform_item(
                item,
                anchor_mm=anchor,
                position_mm=position,
                rotation_deg=rotation_deg,
            )
            imported_references.append(new_reference)
        else:
            _transform_item(
                item,
                anchor_mm=anchor,
                position_mm=position,
                rotation_deg=rotation_deg,
            )
            _refresh_ids(item)
        _rewrite_nets(item, net_map, include_net_name=(kind == "footprint"))
        item_id_node = _direct(item, "uuid") or _direct(item, "tstamp")
        if item_id_node is None or len(item_id_node) < 2:
            raise ValueError(
                f"Imported PCB {kind} object has no top-level UUID/TSTAMP for interactive placement."
            )
        item_uuid = str(item_id_node[1]).strip()
        if not item_uuid or item_uuid in interactive_item_uuids:
            raise ValueError(f"Imported PCB object has a missing or duplicate UUID: {item_uuid!r}.")
        interactive_item_uuids.append(item_uuid)
        imported.append(item)

    if not imported:
        raise ValueError(f"Block {block.name!r} has no importable PCB objects.")
    last_net = max((i for i, item in enumerate(target) if _head(item) == "net"), default=0)
    for offset, net_node in enumerate(new_net_nodes, start=1):
        target.insert(last_net + offset, net_node)
    target.extend(imported)

    source_net_to_board_net = {
        source_name: net_map[code][1]
        for code, source_name in source_nets.items()
        if code in net_map and code != 0
    }
    existing_chain_names = set()
    target_chain_section = _direct(target, "net_chains")
    if target_chain_section is not None:
        for chain in target_chain_section[1:]:
            if _head(chain) == "net_chain":
                name_node = _direct(chain, "name")
                if name_node is not None and len(name_node) >= 2:
                    existing_chain_names.add(str(name_node[1]).casefold())
    if target_project.is_file():
        existing_project = json.loads(target_project.read_text(encoding="utf-8"))
        existing_settings = existing_project.get("net_settings", {})
        for key in ("net_chain_classes", "net_chain_netclasses"):
            values = existing_settings.get(key, {}) if isinstance(existing_settings, dict) else {}
            if isinstance(values, dict):
                existing_chain_names.update(str(name).casefold() for name in values)

    chain_name_map = _merge_net_chain_sections(
        source,
        target,
        source_nets=source_nets,
        source_net_to_board_net=source_net_to_board_net,
        pad_uuid_remap=pad_uuid_remap,
        instance_reference=instance_reference,
        existing_chain_names=existing_chain_names,
    )

    project_merge = _merge_project_net_settings(
        target_project,
        source_project,
        output_project,
        instance_reference=instance_reference,
        layout_ports={str(key): str(value) for key, value in ports.items()},
        pin_to_board_net=pin_to_board_net,
        source_net_to_board_net=source_net_to_board_net,
        source_chain_name_map=chain_name_map,
    )
    _atomic_write(output_board, _dump(target) + "\n")
    try:
        if project_merge is not None:
            _atomic_write(
                output_project,
                json.dumps(project_merge["data"], indent=2, ensure_ascii=False) + "\n",
            )
    except Exception:
        output_board.unlink(missing_ok=True)
        raise

    return {
        "output": str(output_board),
        "block": block.name,
        "layout_id": selected_layout.get("id"),
        "layout_name": selected_layout.get("name"),
        "instance_reference": instance_reference,
        "rotation_deg": rotation_deg,
        "project_file": project_merge["output"] if project_merge is not None else None,
        "merged_netclasses": project_merge["merged_classes"] if project_merge is not None else [],
        "assigned_project_nets": project_merge["assigned_nets"]
        if project_merge is not None
        else [],
        "ignored_project_patterns": project_merge["ignored_patterns"]
        if project_merge is not None
        else False,
        "applied_project_patterns": project_merge["applied_patterns"]
        if project_merge is not None
        else [],
        "unmatched_project_patterns": project_merge["unmatched_patterns"]
        if project_merge is not None
        else [],
        "ignored_project_chain_classes": project_merge["ignored_chain_classes"]
        if project_merge is not None
        else False,
        "merged_project_chain_names": project_merge["merged_chain_names"]
        if project_merge is not None
        else [],
        "unmatched_project_chains": project_merge["unmatched_chains"]
        if project_merge is not None
        else [],
        "merged_board_chains": sorted(chain_name_map.values()),
        "footprints": imported_references,
        "interactive_item_uuids": interactive_item_uuids,
        "footprint_count": len(imported_references),
        "object_count": len(imported),
        "new_net_count": len(new_net_nodes),
        "new_nets": [str(node[2]) for node in new_net_nodes],
        "skipped_top_level_items": sorted(skipped),
    }
