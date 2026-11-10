"""Safe file-level merge of a stored KiCad PCB block into a project board.

KiCad's public IPC Board API imports nets and exposes board objects, but has
no general add-existing-track/zone-from-another-board operation. This module
therefore creates a new validated board copy. It never overwrites the project
board currently open in PCB Editor.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any


class _Quoted(str):
    """Keep quoted S-expression strings distinct from bare atoms."""


def _lex(source: str):
    i = 0
    while i < len(source):
        ch = source[i]
        if ch.isspace():
            i += 1
            continue
        if ch == ";":
            end = source.find("\n", i)
            i = len(source) if end < 0 else end + 1
            continue
        if ch in "()":
            yield ch
            i += 1
            continue
        if ch == '"':
            i += 1
            escaped = False
            out = []
            while i < len(source):
                cur = source[i]
                if escaped:
                    out.append("\\" + cur)
                    escaped = False
                elif cur == "\\":
                    escaped = True
                elif cur == '"':
                    break
                else:
                    out.append(cur)
                i += 1
            if i >= len(source):
                raise ValueError("Unterminated quoted string in KiCad S-expression.")
            try:
                value = json.loads('"' + "".join(out) + '"')
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid quoted string in KiCad S-expression: {exc}") from exc
            yield _Quoted(value)
            i += 1
            continue
        start = i
        while i < len(source) and not source[i].isspace() and source[i] not in "()":
            i += 1
        yield source[start:i]


def parse_sexpr(source: str) -> list:
    """Parse one S-expression into nested lists and scalar tokens."""
    root: list = []
    stack = [root]
    for token in _lex(source):
        if token == "(":
            node: list = []
            stack[-1].append(node)
            stack.append(node)
        elif token == ")":
            if len(stack) == 1:
                raise ValueError("Unexpected ')' in KiCad S-expression.")
            stack.pop()
        else:
            stack[-1].append(token)
    if len(stack) != 1 or len(root) != 1 or not isinstance(root[0], list):
        raise ValueError("Malformed KiCad S-expression document.")
    return root[0]


def _dump(value: Any) -> str:
    if isinstance(value, list):
        return "(" + " ".join(_dump(item) for item in value) + ")"
    if isinstance(value, _Quoted):
        return json.dumps(str(value), ensure_ascii=False)
    return str(value)


def _head(node: Any) -> str | None:
    return str(node[0]) if isinstance(node, list) and node and isinstance(node[0], str) else None


def _direct(node: list, tag: str) -> list | None:
    return next((child for child in node[1:] if _head(child) == tag), None)


def _all(node: list, tag: str) -> list[list]:
    return [child for child in node[1:] if _head(child) == tag]


def _num(value: Any) -> float:
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


def _xy(node: list, dx: float, dy: float) -> None:
    if len(node) < 3:
        raise ValueError(f"Malformed coordinate node {_dump(node)}")
    node[1] = _fmt(_num(node[1]) + dx)
    node[2] = _fmt(_num(node[2]) + dy)


def _get_nets(board: list) -> dict[int, str]:
    result = {0: ""}
    for node in _all(board, "net"):
        if len(node) >= 3:
            try:
                result[int(str(node[1]))] = str(node[2])
            except ValueError as exc:
                raise ValueError(f"Invalid board net declaration: {_dump(node)}") from exc
    return result


def _get_layers(board: list) -> set[str]:
    layers = _direct(board, "layers")
    if layers is None:
        raise ValueError("PCB file does not define a layers list.")
    return {str(row[1]) for row in layers[1:] if isinstance(row, list) and len(row) >= 2}


def _reference(footprint: list) -> str | None:
    for prop in _all(footprint, "property"):
        if len(prop) >= 3 and str(prop[1]).casefold() == "reference":
            return str(prop[2])
    return None


def _refresh_ids(node: Any) -> None:
    if not isinstance(node, list):
        return
    if _head(node) in ("uuid", "tstamp") and len(node) >= 2:
        node[1] = _Quoted(str(uuid.uuid4()))
    for child in node[1:]:
        _refresh_ids(child)


def _rewrite_nets(node: Any, mapping: dict[int, tuple[int, str]]) -> None:
    if not isinstance(node, list):
        return
    if _head(node) == "net" and len(node) >= 3:
        try:
            old_code = int(str(node[1]))
        except ValueError as exc:
            raise ValueError(f"Invalid net reference: {_dump(node)}") from exc
        if old_code not in mapping:
            raise ValueError(f"Board item references undefined source net code {old_code}.")
        code, name = mapping[old_code]
        node[1] = str(code)
        node[2] = _Quoted(name)
        return
    for child in node[1:]:
        _rewrite_nets(child, mapping)


def _translate_item(node: list, dx: float, dy: float) -> None:
    """Translate board-absolute geometry, preserving footprint-local geometry."""
    if _head(node) == "footprint":
        at = _direct(node, "at")
        if at is None:
            raise ValueError("Stored PCB footprint has no placement coordinate.")
        _xy(at, dx, dy)
        for prop in _all(node, "property"):
            prop_at = _direct(prop, "at")
            if prop_at is not None:
                _xy(prop_at, dx, dy)
        return

    absolute_tags = {"at", "start", "mid", "end", "center", "xy", "pts"}

    def walk(item: Any) -> None:
        if not isinstance(item, list):
            return
        tag = _head(item)
        if tag in absolute_tags:
            if tag == "pts":
                for point in item[1:]:
                    if _head(point) == "xy":
                        _xy(point, dx, dy)
            else:
                _xy(item, dx, dy)
            return
        for child in item[1:]:
            walk(child)

    walk(node)


def _rewrite_footprint(footprint: list, reference: str, dx: float, dy: float) -> None:
    _translate_item(footprint, dx, dy)
    for prop in _all(footprint, "property"):
        if len(prop) >= 3 and str(prop[1]).casefold() == "reference":
            prop[2] = _Quoted(reference)
    for fp_text in _all(footprint, "fp_text"):
        if len(fp_text) >= 3 and str(fp_text[1]).casefold() == "reference":
            fp_text[2] = _Quoted(reference)
    _refresh_ids(footprint)
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
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def merge_block_layout(
    target_board: Path,
    block,
    output_board: Path,
    *,
    instance_reference: str,
    position_mm: tuple[float, float],
    pin_to_board_net: dict[str, str],
    layout_anchor_mm: tuple[float, float] = (0.0, 0.0),
) -> dict[str, Any]:
    """Merge a stored block layout into a new copy of a project board.

    The manifest's optional layout_ports map relates facade pin names to
    the net names used in the stored PCB. Without it, names are assumed equal.
    Internal source nets are namespaced with the block instance reference.
    The input project board is never modified.
    """
    target_board = Path(target_board).expanduser().resolve()
    output_board = Path(output_board).expanduser().resolve()
    layout_name = block.manifest.get("layout_file")
    if not layout_name:
        raise ValueError(f"Block {block.name!r} has no stored PCB layout.")
    source_board = (Path(block.path) / str(layout_name)).resolve()
    if not target_board.is_file() or not source_board.is_file():
        raise FileNotFoundError("Project board or stored block PCB layout does not exist.")
    if output_board == target_board:
        raise ValueError("Output must be a new file; the project board is never overwritten.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", instance_reference):
        raise ValueError("Block instance reference must start with a letter and use only letters, digits and underscores.")
    if len(position_mm) != 2 or len(layout_anchor_mm) != 2:
        raise ValueError("Placement position and layout anchor must each contain two coordinates.")
    x, y = map(float, position_mm)
    anchor_x, anchor_y = map(float, layout_anchor_mm)
    if not all(math.isfinite(value) for value in (x, y, anchor_x, anchor_y)):
        raise ValueError("Placement position and layout anchor must be finite.")

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
        raise ValueError("Project board lacks layers required by the block: " + ", ".join(missing_layers))

    source_nets = _get_nets(source)
    target_nets = _get_nets(target)
    target_codes = {name: code for code, name in target_nets.items()}
    next_code = max(target_nets, default=0) + 1
    ports = block.manifest.get("layout_ports") or {pin: pin for pin in pins}
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
            safe_name = re.sub(r"[^A-Za-z0-9_./+-]+", "_", source_name).strip("_") or f"NET{old_code}"
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
        "footprint", "segment", "arc", "via", "zone", "gr_line", "gr_arc",
        "gr_circle", "gr_rect", "gr_poly", "gr_curve", "gr_text",
        "gr_text_box", "dimension", "target",
    }
    imported: list[list] = []
    imported_references: list[str] = []
    skipped: set[str] = set()
    dx, dy = x - anchor_x, y - anchor_y
    for raw_item in source[1:]:
        kind = _head(raw_item)
        if kind in {"version", "generator", "general", "paper", "layers", "setup", "net", "net_class", "embedded_fonts", "uuid"}:
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
            new_reference = _unique_reference(f"{instance_reference}_{old_reference}", existing_references)
            _rewrite_footprint(item, new_reference, dx, dy)
            imported_references.append(new_reference)
        else:
            _translate_item(item, dx, dy)
            _refresh_ids(item)
        _rewrite_nets(item, net_map)
        imported.append(item)

    if not imported:
        raise ValueError(f"Block {block.name!r} has no importable PCB objects.")
    last_net = max((i for i, item in enumerate(target) if _head(item) == "net"), default=0)
    for offset, net_node in enumerate(new_net_nodes, start=1):
        target.insert(last_net + offset, net_node)
    target.extend(imported)
    _atomic_write(output_board, _dump(target) + "\n")
    return {
        "output": str(output_board),
        "block": block.name,
        "instance_reference": instance_reference,
        "footprints": imported_references,
        "footprint_count": len(imported_references),
        "object_count": len(imported),
        "new_net_count": len(new_net_nodes),
        "new_nets": [str(node[2]) for node in new_net_nodes],
        "skipped_top_level_items": sorted(skipped),
    }
