"""Validate generated KiCad schematic geometry and style rules."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from itertools import pairwise
from math import hypot
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "examples" / "stusb4500"))

from generate_reference import (
    ADJACENT_COMPONENT_PAIRS,
    COMMON_PIN_NET_GROUPS,
    COMPONENT_PROPERTY_LIMITS_MM,
    DIRECT_DECOUPLERS,
    FIELD_SYMBOL_MAX_GAP_MM,
    FRAME_CONTENT_PADDING_MM,
    GROUP_EVIDENCE,
    FORBIDDEN_LOCAL_LABELS,
    GROUND_PINS_DOWN,
    GROUP_GAP_MM,
    REQUIRED_NO_CONNECT_PINS,
    SCHEMATIC_GROUPS,
    WIRE_LENGTH_BUDGETS_MM,
    _uid,
)
from kicad_blocks.pcb_layout import _all, _direct, _head, parse_sexpr

STYLE_RULES = {
    "SCH001": {
        "name": "wire-geometry",
        "severity": "error",
        "description": "No diagonal, crossing, overlapping or unmarked-branch wires.",
        "scope": "generic",
    },
    "SCH002": {
        "name": "label-topology",
        "severity": "error",
        "description": "Local labels must join distinct wire islands and hierarchical labels must terminate a twig.",
        "scope": "generic",
    },
    "SCH003": {
        "name": "label-wire-clearance",
        "severity": "error",
        "description": "No wire may pass through a label's visible glyph rectangle.",
        "scope": "generic",
    },
    "SCH004": {
        "name": "physical-net-connectivity",
        "severity": "error",
        "description": "Declared decouplers and FET midpoint networks must be physically wired without labels.",
    },
    "SCH005": {
        "name": "pin-orientation",
        "severity": "error",
        "description": "Ground pins that can face down should use a downward symbol orientation.",
    },
    "SCH006": {
        "name": "component-spacing",
        "severity": "warning",
        "description": "Enforce adjacency limits and short reference/value offsets.",
    },
    "SCH007": {
        "name": "group-metadata",
        "severity": "error",
        "description": "Every declared circuit group must emit a title and description.",
    },
    "SCH008": {
        "name": "annotation-layout",
        "severity": "error",
        "description": "Use correctly sized native text boxes with consistent padding and no overlaps.",
        "scope": "generic",
    },
    "SCH009": {
        "name": "group-containment",
        "severity": "error",
        "description": "Pins, fields, labels, wires and annotations must remain inside their assigned group frame.",
        "scope": "profile",
    },
    "SCH010": {
        "name": "symbol-field-clearance",
        "severity": "error",
        "description": "Reference/value fields must clear symbol bodies, pins, wires and other visible elements.",
        "scope": "generic",
    },
    "SCH011": {
        "name": "block-annotation-structure",
        "severity": "error",
        "description": "Block titles are single-line and descriptive comment boxes have visible outlines.",
        "scope": "generic",
    },
    "SCH012": {
        "name": "frame-content-clearance",
        "severity": "error",
        "description": "Labels and descriptive panels keep a minimum inset from functional frame borders.",
        "scope": "profile",
    },
    "SCH013": {
        "name": "wire-obstacle-clearance",
        "severity": "error",
        "description": "Wires may terminate at intended pins but must not cross symbol bodies or intermediate pins.",
        "scope": "generic",
    },
    "SCH014": {
        "name": "shared-pin-net-contracts",
        "severity": "error",
        "description": "Declared paired pins share the required logical net.",
        "scope": "profile",
    },
    "SCH015": {
        "name": "required-no-connects",
        "severity": "error",
        "description": "Designated unused pins carry explicit no-connect markers.",
        "scope": "profile",
    },
    "SCH016": {
        "name": "wire-compactness",
        "severity": "warning",
        "description": "Per-group total wire length is scored against a configurable routing budget.",
        "scope": "profile",
    },
    "SCH017": {
        "name": "circuit-documentation",
        "severity": "error",
        "description": "Non-trivial circuit groups identify their component function and source evidence.",
        "scope": "profile",
    },
    "SCH018": {
        "name": "label-text-collision",
        "severity": "error",
        "description": "Label glyphs may not overlap other labels, fields or annotation boxes.",
        "scope": "generic",
    },
}

TOL = 0.001
PADDING = 1.27
FRAME_GAP = GROUP_GAP_MM
ZONES = {group["id"]: set(group["members"]) for group in SCHEMATIC_GROUPS}
FLOW = tuple(
    group["id"] for group in sorted(
        (item for item in SCHEMATIC_GROUPS if item["lane"] == "main"),
        key=lambda item: int(item["order"]),
    )
)


def _segments(tree: list):
    result = []
    for wire in _all(tree, "wire"):
        pts = _direct(wire, "pts")
        xy = [(round(float(p[1]), 3), round(float(p[2]), 3)) for p in _all(pts, "xy")] if pts else []
        result.append(tuple(xy) if len(xy) == 2 else ())
    return result


def _on_segment(point, start, end) -> bool:
    x, y = point
    x1, y1 = start
    x2, y2 = end
    if abs(y1 - y2) <= TOL:
        return abs(y - y1) <= TOL and min(x1, x2) - TOL <= x <= max(x1, x2) + TOL
    if abs(x1 - x2) <= TOL:
        return abs(x - x1) <= TOL and min(y1, y2) - TOL <= y <= max(y1, y2) + TOL
    return False


def wire_geometry_issues(tree: list) -> list[str]:
    """Reject crossings, diagonal routes, overlaps, and unmarked T-junctions."""
    issues, valid = [], []
    for index, segment in enumerate(_segments(tree)):
        if len(segment) != 2:
            issues.append(f"wire {index} must have exactly two endpoints")
            continue
        a, b = segment
        if a == b:
            issues.append(f"wire {index} has zero length")
            continue
        if abs(a[0] - b[0]) > TOL and abs(a[1] - b[1]) > TOL:
            issues.append(f"wire {index} is diagonal: {a}->{b}")
            continue
        valid.append((a, b))
    junctions = {
        (round(float(_direct(j, "at")[1]), 3), round(float(_direct(j, "at")[2]), 3))
        for j in _all(tree, "junction") if _direct(j, "at") is not None
    }
    for i, (a, b) in enumerate(valid):
        for c, d in valid[i + 1:]:
            ah, ch = abs(a[1] - b[1]) <= TOL, abs(c[1] - d[1]) <= TOL
            if ah == ch:
                if ah and abs(a[1] - c[1]) <= TOL:
                    overlap = min(max(a[0], b[0]), max(c[0], d[0])) - max(min(a[0], b[0]), min(c[0], d[0]))
                elif not ah and abs(a[0] - c[0]) <= TOL:
                    overlap = min(max(a[1], b[1]), max(c[1], d[1])) - max(min(a[1], b[1]), min(c[1], d[1]))
                else:
                    overlap = 0
                if overlap > TOL:
                    issues.append(f"collinear wire overlap: {a}->{b} and {c}->{d}")
                continue
            hs, he = (a, b) if ah else (c, d)
            vs, ve = (c, d) if ah else (a, b)
            cross = (vs[0], hs[1])
            if not _on_segment(cross, hs, he) or not _on_segment(cross, vs, ve):
                continue
            h_end, v_end = cross in (hs, he), cross in (vs, ve)
            if h_end and v_end:
                continue
            if not h_end and not v_end:
                issues.append(f"wire crossing at {cross}: {a}->{b} intersects {c}->{d}")
                continue
            if cross not in junctions:
                issues.append(f"unmarked T-junction at {cross}: {a}->{b} intersects {c}->{d}")
    return issues


class _DSU:
    def __init__(self):
        self.parent = {}

    def find(self, point):
        self.parent.setdefault(point, point)
        if self.parent[point] != point:
            self.parent[point] = self.find(self.parent[point])
        return self.parent[point]

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[b] = a


def _labels(tree: list, kind: str):
    return [
        (str(node[1]), (round(float(_direct(node, "at")[1]), 3), round(float(_direct(node, "at")[2]), 3)))
        for node in _all(tree, kind)
        if len(node) > 1 and _direct(node, "at") is not None
    ]


def label_network_issues(tree: list) -> list[str]:
    local, hierarchical = _labels(tree, "label"), _labels(tree, "hierarchical_label")
    segments = [s for s in _segments(tree) if len(s) == 2]
    junctions = {
        (round(float(_direct(j, "at")[1]), 3), round(float(_direct(j, "at")[2]), 3))
        for j in _all(tree, "junction") if _direct(j, "at") is not None
    }
    points = {p for s in segments for p in s} | junctions | {p for _, p in local + hierarchical}
    dsu = _DSU()
    for a, b in segments:
        split = [p for p in points if _on_segment(p, a, b)]
        axis = 0 if abs(a[1] - b[1]) <= TOL else 1
        split.sort(key=lambda p: p[axis], reverse=a[axis] > b[axis])
        for p, q in pairwise(split):
            dsu.union(p, q)
    totals, island_uses = Counter(), Counter()
    issues = []
    for name, point in local:
        totals[name] += 1
        island_uses[(name, dsu.find(point))] += 1
    for (name, _), count in island_uses.items():
        if count > 1:
            issues.append(f"local label {name!r} occurs {count} times on one physical wire network")
    for name, count in totals.items():
        if count < 2:
            issues.append(f"local label {name!r} is used only {count} time(s)")
    endpoint_degree = Counter(p for s in segments for p in s)
    for name, point in local:
        if not any(_on_segment(point, a, b) for a, b in segments):
            issues.append(f"local label {name!r} at {point} is not attached to a wire")
    for name, point in hierarchical:
        if endpoint_degree[point] != 1 or not any(_on_segment(point, a, b) for a, b in segments):
            issues.append(f"hierarchical label {name!r} at {point} must sit at the end of one wire twig")
    return issues


def _advance(char: str) -> float:
    if char in "ilI.,:;!|'":
        return 0.30
    if char in "MW@%&":
        return 0.90
    if char.isspace():
        return 0.34
    if char.isupper() or char.isdigit():
        return 0.64
    return 0.55


def _required_size(value: str, font: float, pad: float):
    lines = value.splitlines() or [""]
    width = max((sum(_advance(ch) for ch in line) for line in lines), default=0.0)
    return 2 * pad + max(width * font * 1.12, font), 2 * pad + len(lines) * font * 1.55


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return min(ax + aw, bx + bw) - max(ax, bx) > TOL and min(ay + ah, by + bh) - max(ay, by) > TOL


def _line_hits_box(a, b, box):
    x, y, width, height = box
    if abs(a[1] - b[1]) <= TOL:
        return y - TOL <= a[1] <= y + height + TOL and min(max(a[0], b[0]), x + width) - max(min(a[0], b[0]), x) > TOL
    if abs(a[0] - b[0]) <= TOL:
        return x - TOL <= a[0] <= x + width + TOL and min(max(a[1], b[1]), y + height) - max(min(a[1], b[1]), y) > TOL
    return False


def _inside(point, rect, margin: float = 0.0) -> bool:
    x, y, width, height = rect
    return (
        x + margin - TOL <= point[0] <= x + width - margin + TOL
        and y + margin - TOL <= point[1] <= y + height - margin + TOL
    )


def _instances(tree: list):
    result = {}
    for item in _all(tree, "symbol"):
        if _direct(item, "lib_id") is None:
            continue
        ref = next((str(p[2]) for p in _all(item, "property") if len(p) >= 3 and str(p[1]) == "Reference"), None)
        if ref:
            result[ref] = item
    return result


def _property(instance: list, name: str):
    return next((p for p in _all(instance, "property") if len(p) >= 3 and str(p[1]) == name), None)


def text_box_issues(tree: list) -> list[str]:
    issues, boxes = [], []
    if _all(tree, "text"):
        issues.append("free-standing annotation text is forbidden; use KiCad text_box")
    for index, node in enumerate(_all(tree, "text_box")):
        at, size, margins = _direct(node, "at"), _direct(node, "size"), _direct(node, "margins")
        effects = _direct(node, "effects")
        font_node = _direct(_direct(effects, "font"), "size") if effects is not None and _direct(effects, "font") is not None else None
        if at is None or size is None or margins is None or font_node is None:
            issues.append(f"text_box {index} is missing geometry, margins or font")
            continue
        pads = [float(v) for v in margins[1:]]
        if len(pads) != 4 or any(abs(v - PADDING) > TOL for v in pads):
            issues.append(f"text_box {index} must have {PADDING:.2f} mm equal padding")
            continue
        x, y, w, h = float(at[1]), float(at[2]), float(size[1]), float(size[2])
        need_w, need_h = _required_size(str(node[1]), float(font_node[1]), pads[0])
        if w + TOL < need_w or h + TOL < need_h:
            issues.append(f"text_box {index} undersized: {w:.2f}x{h:.2f} mm, needs {need_w:.2f}x{need_h:.2f}")
        uid = _direct(node, "uuid")
        boxes.append((str(uid[1]) if uid is not None else f"box-{index}", (x, y, w, h)))
    segments = [s for s in _segments(tree) if len(s) == 2]
    for index, (uid, box) in enumerate(boxes):
        for other_uid, other_box in boxes[index + 1:]:
            if _overlap(box, other_box):
                issues.append(f"text boxes overlap: {uid} and {other_uid}")
        if any(_line_hits_box(a, b, box) for a, b in segments):
            issues.append(f"a wire penetrates text box {uid}")
    return issues


def _frame_bounds(tree: list, zone: str):
    expected = _uid(f"graphic:rectangle:zone:{zone}")
    for item in _all(tree, "rectangle"):
        uid = _direct(item, "uuid")
        if uid is None or str(uid[1]) != expected:
            continue
        start, end = _direct(item, "start"), _direct(item, "end")
        if start is None or end is None:
            return None
        x1, y1, x2, y2 = float(start[1]), float(start[2]), float(end[1]), float(end[2])
        return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
    return None




def _pin_world_info(tree: list) -> dict[tuple[str, str], dict]:
    """Resolve every component pin to a sheet coordinate and outward angle."""
    lib_symbols = _direct(tree, "lib_symbols")
    library = {}
    def descendants(node):
        for child in node[1:]:
            if not isinstance(child, list):
                continue
            yield child
            yield from descendants(child)

    if lib_symbols is not None:
        for node in _all(lib_symbols, "symbol"):
            if len(node) > 1:
                library[str(node[1])] = node

    result = {}
    for reference, instance in _instances(tree).items():
        lib_id_node, at = _direct(instance, "lib_id"), _direct(instance, "at")
        if lib_id_node is None or at is None:
            continue
        definition = library.get(str(lib_id_node[1]))
        if definition is None:
            continue
        sx, sy, rotation = float(at[1]), float(at[2]), int(float(at[3]))
        seen = set()
        for pin in descendants(definition):
            if _head(pin) != "pin":
                continue
            number_node, pin_at = _direct(pin, "number"), _direct(pin, "at")
            if number_node is None or pin_at is None:
                continue
            number = str(number_node[1])
            if number in seen:
                continue
            seen.add(number)
            x, y = float(pin_at[1]), float(pin_at[2])
            if rotation == 0:
                point = (round(sx + x, 3), round(sy - y, 3))
            elif rotation == 90:
                point = (round(sx - y, 3), round(sy - x, 3))
            elif rotation == 180:
                point = (round(sx - x, 3), round(sy + y, 3))
            elif rotation == 270:
                point = (round(sx + y, 3), round(sy + x, 3))
            else:
                continue
            result[(reference, number)] = {
                "point": point,
                "angle": (int(float(pin_at[3])) + rotation) % 360,
            }
    return result


def _wire_networks(tree: list):
    """Return a DSU of physically connected wire segments (labels excluded)."""
    segments = [segment for segment in _segments(tree) if len(segment) == 2]
    junctions = {
        (
            round(float(_direct(node, "at")[1]), 3),
            round(float(_direct(node, "at")[2]), 3),
        )
        for node in _all(tree, "junction") if _direct(node, "at") is not None
    }
    pin_info = _pin_world_info(tree)
    label_points = {
        (round(float(_direct(node, "at")[1]), 3), round(float(_direct(node, "at")[2]), 3))
        for kind in ("label", "hierarchical_label", "global_label")
        for node in _all(tree, kind) if _direct(node, "at") is not None
    }
    points = {info["point"] for info in pin_info.values()}
    points |= junctions | label_points | {point for segment in segments for point in segment}
    dsu = _DSU()
    for start, end in segments:
        axis = 0 if abs(start[1] - end[1]) <= TOL else 1
        split = [point for point in points if _on_segment(point, start, end)]
        split.sort(key=lambda point: point[axis], reverse=start[axis] > end[axis])
        for first, second in pairwise(split):
            dsu.union(first, second)
    return dsu, pin_info


def _strict_line_hits_box(a, b, box) -> bool:
    """Check intersection with the visible text interior, excluding its anchor."""
    x, y, width, height = box
    if abs(a[1] - b[1]) <= TOL:
        return (
            y + TOL < a[1] < y + height - TOL
            and min(max(a[0], b[0]), x + width) - max(min(a[0], b[0]), x) > TOL
        )
    if abs(a[0] - b[0]) <= TOL:
        return (
            x + TOL < a[0] < x + width - TOL
            and min(max(a[1], b[1]), y + height) - max(min(a[1], b[1]), y) > TOL
        )
    return False


def label_wire_overlap_issues(tree: list) -> list[str]:
    """Reject wires that pass through visible local/hierarchical label glyphs."""
    issues = []
    segments = [segment for segment in _segments(tree) if len(segment) == 2]
    for kind in ("label", "hierarchical_label"):
        for node in _all(tree, kind):
            bounds = _text_visual_bounds(str(node[1]), node)
            if bounds is not None and any(
                _strict_line_hits_box(a, b, bounds) for a, b in segments
            ):
                at = _direct(node, "at")
                issues.append(
                    f"{kind} {str(node[1])!r} at "
                    f"({float(at[1]):.3f}, {float(at[2]):.3f}) overlaps a wire"
                )
    return issues


def physical_topology_issues(tree: list) -> list[str]:
    """Require decouplers and the back-to-back FET midpoint to be wire-connected."""
    issues = []
    dsu, pin_info = _wire_networks(tree)
    pairs = list(DIRECT_DECOUPLERS) + [
        ("T4", "3", "T1", "2"),
        ("T1", "2", "R10", "1"),
    ]
    label_positions = {
        (round(float(_direct(node, "at")[1]), 3), round(float(_direct(node, "at")[2]), 3))
        for kind in ("label", "hierarchical_label")
        for node in _all(tree, kind) if _direct(node, "at") is not None
    }
    for ref_a, pin_a, ref_b, pin_b in pairs:
        first, second = pin_info.get((ref_a, pin_a)), pin_info.get((ref_b, pin_b))
        if first is None or second is None:
            issues.append(
                f"required physical net is missing pin {ref_a}.{pin_a} or {ref_b}.{pin_b}"
            )
            continue
        a, b = first["point"], second["point"]
        if dsu.find(a) != dsu.find(b):
            issues.append(f"{ref_a}.{pin_a} must be directly wire-connected to {ref_b}.{pin_b}")
        if a in label_positions or b in label_positions:
            issues.append(f"{ref_a}.{pin_a}/{ref_b}.{pin_b} must not use labels instead of direct wires")

    forbidden = set(FORBIDDEN_LOCAL_LABELS)
    for node in _all(tree, "label"):
        if len(node) > 1 and str(node[1]) in forbidden:
            issues.append(f"local label {str(node[1])!r} is forbidden on a directly wired net")
    return issues


def pin_orientation_issues(tree: list) -> list[str]:
    """Check declared ground pins face down (global pin angle 90 degrees)."""
    issues = []
    pin_info = _pin_world_info(tree)
    for key in GROUND_PINS_DOWN:
        reference, number = key.split(".", 1)
        pin = pin_info.get((reference, number))
        if pin is None:
            issues.append(f"ground orientation rule references missing pin {key}")
        elif pin["angle"] != 90:
            issues.append(
                f"ground pin {key} points at {pin['angle']} degrees; expected 90 degrees (down)"
            )
    return issues


def component_spacing_issues(tree: list) -> list[str]:
    """Enforce declared adjacency and short component property offsets."""
    issues = []
    instances = _instances(tree)
    positions = {}
    for reference, instance in instances.items():
        at = _direct(instance, "at")
        if at is not None:
            positions[reference] = (float(at[1]), float(at[2]))
    for rule in ADJACENT_COMPONENT_PAIRS:
        left, right = str(rule["left"]), str(rule["right"])
        if left not in positions or right not in positions:
            issues.append(f"adjacency rule references missing components {left}/{right}")
            continue
        ax, ay = positions[left]
        bx, by = positions[right]
        if abs(ay - by) > TOL:
            issues.append(f"adjacent components {left}/{right} are not aligned")
            continue
        gap = abs(bx - ax) - float(rule["body_width_mm"])
        if gap > float(rule["max_gap_mm"]) + TOL:
            issues.append(
                f"components {left}/{right} gap is {gap:.2f} mm; "
                f"maximum is {float(rule['max_gap_mm']):.2f} mm"
            )
    for reference, instance in instances.items():
        at = _direct(instance, "at")
        if at is None:
            continue
        sx, sy = float(at[1]), float(at[2])
        ref_prop, value_prop = _property(instance, "Reference"), _property(instance, "Value")
        checks = []
        if reference.startswith("R") and ref_prop is not None:
            checks.append(("Reference", ref_prop, COMPONENT_PROPERTY_LIMITS_MM["resistor_reference"]))
        if reference.startswith("C"):
            if ref_prop is not None:
                checks.append(("Reference", ref_prop, COMPONENT_PROPERTY_LIMITS_MM["capacitor_reference"]))
            if value_prop is not None:
                checks.append(("Value", value_prop, COMPONENT_PROPERTY_LIMITS_MM["capacitor_value"]))
        for name, prop, limit in checks:
            prop_at = _direct(prop, "at")
            if prop_at is None:
                issues.append(f"{reference} {name} field has no position")
                continue
            distance = hypot(float(prop_at[1]) - sx, float(prop_at[2]) - sy)
            if distance > limit + TOL:
                issues.append(
                    f"{reference} {name} field is {distance:.2f} mm from the component; "
                    f"maximum is {limit:.2f} mm"
                )
    return issues


def group_metadata_issues(tree: list) -> list[str]:
    """Every declarative group must emit its generated title and description."""
    issues = []
    text_uuids = {
        str(uid[1]) for node in _all(tree, "text_box")
        if (uid := _direct(node, "uuid")) is not None
    }
    for group in SCHEMATIC_GROUPS:
        for key, description in (
            (f"graphic:text:zone-title:{group['id']}", "title"),
            (f"graphic:text:group-description:{group['id']}", "description"),
        ):
            expected = _uid(key)
            if expected not in text_uuids:
                issues.append(f"schematic group {group['id']} is missing its generated {description}")
    return issues


def _text_visual_bounds(value: str, node: list) -> tuple[float, float, float, float] | None:
    """Approximate the printable text rectangle for a label or symbol field."""
    at, effects = _direct(node, "at"), _direct(node, "effects")
    if at is None:
        return None
    font_node = _direct(_direct(effects, "font"), "size") if effects is not None else None
    font = float(font_node[1]) if font_node is not None else 1.0
    width = max(font, sum(_advance(ch) for ch in value) * font * 1.12)
    height = font * 1.55
    x, y, angle = float(at[1]), float(at[2]), int(float(at[3]))
    justify_node = _direct(effects, "justify") if effects is not None else None
    justify = str(justify_node[1]) if justify_node is not None else "center"
    left = x if justify == "left" else x - width if justify == "right" else x - width / 2
    if angle in {90, 270}:
        return x - height / 2, y - width / 2, height, width
    if justify_node is not None and len(justify_node) > 2 and str(justify_node[2]) == "top":
        return left, y, width, height
    return left, y - height, width, height


def _rect_inside(inner, outer) -> bool:
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return (
        ix >= ox - TOL and iy >= oy - TOL
        and ix + iw <= ox + ow + TOL
        and iy + ih <= oy + oh + TOL
    )

def frame_and_component_issues(tree: list) -> list[str]:
    issues, instances, frames = [], _instances(tree), {}
    for ref, instance in instances.items():
        dnp_node, value_prop = _direct(instance, "dnp"), _property(instance, "Value")
        dnp = dnp_node is not None and str(dnp_node[1]).lower() == "yes"
        value = str(value_prop[2]) if value_prop is not None else ""
        if ref in {"R51", "R52"}:
            if not dnp or value != "100k":
                issues.append(f"{ref} must have Value=100k and DNP=yes")
        elif dnp:
            issues.append(f"{ref} is DNP but is not an approved optional-population component")
    for zone in ZONES:
        rect = _frame_bounds(tree, zone)
        if rect is None:
            issues.append(f"functional frame {zone!r} is missing")
        else:
            frames[zone] = rect
    entries = list(frames.items())
    for i, (name, rect) in enumerate(entries):
        for other_name, other_rect in entries[i + 1:]:
            if _overlap(rect, other_rect):
                issues.append(f"functional frames overlap: {name}/{other_name}")
    for left_name, right_name in pairwise(FLOW):
        if left_name in frames and right_name in frames:
            left, right = frames[left_name], frames[right_name]
            gap = right[0] - (left[0] + left[2])
            if abs(gap - FRAME_GAP) > TOL:
                issues.append(f"stage gap {left_name}->{right_name} is {gap:.2f} mm (expected {FRAME_GAP:.2f})")
    pin_info = _pin_world_info(tree)
    for zone, members in ZONES.items():
        rect = frames.get(zone)
        if rect is None:
            continue
        for ref in members:
            at = _direct(instances[ref], "at") if ref in instances else None
            point = (float(at[1]), float(at[2])) if at is not None else (float("nan"), float("nan"))
            x, y, w, h = rect
            if not (x + 2.54 - TOL <= point[0] <= x + w - 2.54 + TOL and y + 2.54 - TOL <= point[1] <= y + h - 2.54 + TOL):
                issues.append(f"component {ref} is not comfortably inside frame {zone}")
            for (pin_ref, pin_number), info in pin_info.items():
                if pin_ref == ref and not _inside(info["point"], rect):
                    issues.append(
                        f"pin {ref}.{pin_number} crosses outside its group frame {zone}"
                    )
            for name in ("Reference", "Value"):
                prop = _property(instances[ref], name)
                if prop is None or "hide" in (_direct(prop, "effects") or []):
                    continue
                bounds = _text_visual_bounds(str(prop[2]), prop)
                if bounds is not None and not _rect_inside(bounds, rect):
                    issues.append(f"{ref} {name} text crosses outside its group frame {zone}")
    for zone, rect in frames.items():
        for segment in _segments(tree):
            if len(segment) != 2:
                continue
            a, b = segment
            if _inside(a, rect) and _inside(b, rect):
                continue
            if _line_hits_box(a, b, rect):
                issues.append(f"wire {a}->{b} crosses the {zone} frame boundary")
    # A label belongs to the group containing its anchor. Its whole visible
    # glyph must fit that same frame; being inside a neighbouring frame is not
    # a valid containment exception.
    for kind in ("label", "hierarchical_label"):
        for node in _all(tree, kind):
            bounds = _text_visual_bounds(str(node[1]), node)
            at = _direct(node, "at")
            if bounds is None or at is None:
                continue
            anchor = (float(at[1]), float(at[2]))
            owners = [
                (zone, rect) for zone, rect in frames.items()
                if _inside(anchor, rect)
            ]
            if len(owners) != 1:
                issues.append(
                    f"{kind} {str(node[1])!r} at ({anchor[0]:.3f}, {anchor[1]:.3f}) "
                    f"belongs to {len(owners)} group frames; expected exactly one"
                )
            elif not _rect_inside(bounds, owners[0][1]):
                issues.append(
                    f"{kind} {str(node[1])!r} at ({anchor[0]:.3f}, {anchor[1]:.3f}) "
                    f"extends outside its owning group frame {owners[0][0]}"
                )
    for node in _all(tree, "text_box"):
        uid_node = _direct(node, "uuid")
        uid = str(uid_node[1]) if uid_node is not None else ""
        if "zone-title:" in uid:
            continue
        at, size = _direct(node, "at"), _direct(node, "size")
        if at is None or size is None:
            continue
        box = (float(at[1]), float(at[2]), float(size[1]), float(size[2]))
        for zone, rect in frames.items():
            if _overlap(box, rect):
                wholly_inside = _rect_inside(box, rect)
                if not wholly_inside:
                    issues.append(f"text box {uid} penetrates frame {zone}")
    return issues



def _walk_nodes(node: list):
    """Yield an S-expression node and every nested child."""
    yield node
    for child in node[1:]:
        if isinstance(child, list):
            yield from _walk_nodes(child)


def _local_to_world(sx: float, sy: float, rotation: int, x: float, y: float):
    if rotation == 0:
        return sx + x, sy - y
    if rotation == 90:
        return sx - y, sy - x
    if rotation == 180:
        return sx - x, sy + y
    if rotation == 270:
        return sx + y, sy + x
    raise ValueError(f"Unsupported symbol rotation {rotation}")


def _symbol_body_bounds(tree: list) -> dict[str, tuple[float, float, float, float]]:
    """Approximate symbol body bounds from library rectangles, paths, circles and arcs."""
    library = _direct(tree, "lib_symbols")
    definitions = {}
    if library is not None:
        definitions = {
            str(node[1]): node for node in _all(library, "symbol") if len(node) > 1
        }
    result = {}
    for reference, instance in _instances(tree).items():
        lib_id, at = _direct(instance, "lib_id"), _direct(instance, "at")
        if lib_id is None or at is None:
            continue
        definition = definitions.get(str(lib_id[1]))
        if definition is None:
            continue
        sx, sy, rotation = float(at[1]), float(at[2]), int(float(at[3]))
        points = []
        for node in _walk_nodes(definition):
            kind = _head(node)
            local_points = []
            if kind == "rectangle":
                for field in ("start", "end"):
                    point = _direct(node, field)
                    if point is not None:
                        local_points.append((float(point[1]), float(point[2])))
            elif kind in {"polyline", "polygon"}:
                pts = _direct(node, "pts")
                if pts is not None:
                    local_points.extend(
                        (float(point[1]), float(point[2]))
                        for point in _all(pts, "xy")
                    )
            elif kind == "circle":
                center, radius = _direct(node, "center"), _direct(node, "radius")
                if center is not None and radius is not None:
                    cx, cy, r = float(center[1]), float(center[2]), abs(float(radius[1]))
                    local_points.extend(((cx - r, cy - r), (cx + r, cy + r)))
            elif kind == "arc":
                for field in ("start", "mid", "end"):
                    point = _direct(node, field)
                    if point is not None:
                        local_points.append((float(point[1]), float(point[2])))
            points.extend(
                _local_to_world(sx, sy, rotation, x, y)
                for x, y in local_points
            )
        if points:
            xs, ys = [point[0] for point in points], [point[1] for point in points]
            result[reference] = (
                min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys),
            )
    return result


def _visible_properties(tree: list):
    fields = []
    for reference, instance in _instances(tree).items():
        for name in ("Reference", "Value"):
            prop = _property(instance, name)
            if prop is None:
                continue
            effects = _direct(prop, "effects")
            if effects is not None and _direct(effects, "hide") is not None:
                continue
            bounds = _text_visual_bounds(str(prop[2]), prop)
            if bounds is not None:
                fields.append((reference, name, bounds, prop))
    return fields


def _box_gap(first, second) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    dx = max(0.0, bx - (ax + aw), ax - (bx + bw))
    dy = max(0.0, by - (ay + ah), ay - (by + bh))
    return hypot(dx, dy)


def symbol_field_clearance_issues(tree: list) -> list[str]:
    """Check visible fields against wires, pins, symbol bodies and other UI objects."""
    issues = []
    bodies = _symbol_body_bounds(tree)
    fields = _visible_properties(tree)
    pin_info = _pin_world_info(tree)
    segments = [segment for segment in _segments(tree) if len(segment) == 2]
    text_boxes = []
    for index, node in enumerate(_all(tree, "text_box")):
        at, size = _direct(node, "at"), _direct(node, "size")
        if at is not None and size is not None:
            text_boxes.append((f"text_box {index}", (
                float(at[1]), float(at[2]), float(size[1]), float(size[2]),
            )))
    label_boxes = [
        (str(node[1]), _text_visual_bounds(str(node[1]), node))
        for kind in ("label", "hierarchical_label", "global_label")
        for node in _all(tree, kind)
    ]
    label_boxes = [(name, box) for name, box in label_boxes if box is not None]

    for reference, name, bounds, _prop in fields:
        body = bodies.get(reference)
        if body is not None and _overlap(bounds, body):
            if name == "Value" and _rect_inside(bounds, body):
                pass  # A value may be intentionally centered inside a passive body.
            elif _rect_inside(bounds, body):
                issues.append(f"{reference} {name} is embedded inside its symbol body")
            else:
                issues.append(f"{reference} {name} partially straddles its symbol body")
        elif body is not None and _box_gap(bounds, body) > FIELD_SYMBOL_MAX_GAP_MM + TOL:
            gap = _box_gap(bounds, body)
            issues.append(
                f"{reference} {name} is {gap:.2f} mm from its symbol body; "
                f"maximum is {FIELD_SYMBOL_MAX_GAP_MM:.2f} mm"
            )
        for start, end in segments:
            if _strict_line_hits_box(start, end, bounds):
                issues.append(f"{reference} {name} overlaps wire {start}->{end}")
        for (pin_ref, pin_number), info in pin_info.items():
            if _inside(info["point"], bounds):
                issues.append(f"{reference} {name} overlaps pin {pin_ref}.{pin_number}")
        for other_ref, other_body in bodies.items():
            if other_ref != reference and _overlap(bounds, other_body):
                issues.append(f"{reference} {name} overlaps symbol body {other_ref}")
        for title, box in text_boxes:
            if _overlap(bounds, box):
                issues.append(f"{reference} {name} overlaps {title}")
        for label, box in label_boxes:
            if _overlap(bounds, box):
                issues.append(f"{reference} {name} overlaps label {label!r}")

    for index, (ref_a, name_a, box_a, _prop_a) in enumerate(fields):
        for ref_b, name_b, box_b, _prop_b in fields[index + 1:]:
            if _overlap(box_a, box_b):
                if ref_a == ref_b:
                    issues.append(f"{ref_a} {name_a} overlaps its {name_b} field")
                else:
                    issues.append(f"{ref_a} {name_a} overlaps {ref_b} {name_b}")
    return issues


def block_annotation_issues(tree: list) -> list[str]:
    """Require one-line block titles and bordered description/comment boxes."""
    issues = []
    for node in _all(tree, "text_box"):
        uid_node = _direct(node, "uuid")
        uid = str(uid_node[1]) if uid_node is not None else ""
        value = str(node[1])
        if "zone-title:" in uid and ("\n" in value or "\r" in value):
            issues.append(f"block title {uid} must be a single line")
        if "group-description:" in uid:
            stroke = _direct(node, "stroke")
            width = _direct(stroke, "width") if stroke is not None else None
            if width is None or float(width[1]) <= 0:
                issues.append(f"group comment {uid} must have its own visible box outline")
    return issues


def _rect_inside_margin(inner, outer, margin: float) -> bool:
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return (
        ix >= ox + margin - TOL
        and iy >= oy + margin - TOL
        and ix + iw <= ox + ow - margin + TOL
        and iy + ih <= oy + oh - margin + TOL
    )


def frame_content_clearance_issues(tree: list) -> list[str]:
    """Keep visible labels and group comment panels inset from their owning frame."""
    issues = []
    frames = {zone: _frame_bounds(tree, zone) for zone in ZONES}
    for kind in ("label", "hierarchical_label", "global_label"):
        for node in _all(tree, kind):
            bounds = _text_visual_bounds(str(node[1]), node)
            at = _direct(node, "at")
            if bounds is None or at is None:
                continue
            anchor = (float(at[1]), float(at[2]))
            owners = [
                (zone, rect) for zone, rect in frames.items()
                if rect is not None and _inside(anchor, rect)
            ]
            if len(owners) == 1:
                zone, rect = owners[0]
                if not _rect_inside_margin(bounds, rect, FRAME_CONTENT_PADDING_MM):
                    issues.append(
                        f"{kind} {str(node[1])!r} is closer than "
                        f"{FRAME_CONTENT_PADDING_MM:.2f} mm to frame {zone}"
                    )
    for node in _all(tree, "text_box"):
        uid_node = _direct(node, "uuid")
        uid = str(uid_node[1]) if uid_node is not None else ""
        if "group-description:" not in uid:
            continue
        at, size = _direct(node, "at"), _direct(node, "size")
        if at is None or size is None:
            continue
        box = (float(at[1]), float(at[2]), float(size[1]), float(size[2]))
        zone = next((z for z in ZONES if f"group-description:{z}" in uid), None)
        rect = frames.get(zone) if zone is not None else None
        if rect is not None and not _rect_inside_margin(box, rect, FRAME_CONTENT_PADDING_MM):
            issues.append(f"group comment {uid} violates frame content padding")
    return issues


def wire_obstacle_issues(tree: list) -> list[str]:
    """Reject wire segments through symbol bodies and intermediate pins."""
    issues = []
    bodies = _symbol_body_bounds(tree)
    pin_info = _pin_world_info(tree)
    for index, segment in enumerate(_segments(tree)):
        if len(segment) != 2:
            continue
        start, end = segment
        for reference, bounds in bodies.items():
            if _strict_line_hits_box(start, end, bounds):
                issues.append(f"wire {index} penetrates symbol body {reference}")
        for (reference, number), info in pin_info.items():
            point = info["point"]
            if point not in (start, end) and _on_segment(point, start, end):
                issues.append(f"wire {index} passes through intermediate pin {reference}.{number}")
    return issues


def common_pin_net_issues(tree: list) -> list[str]:
    """Check configured pin groups against a common named logical net."""
    issues = []
    dsu, pin_info = _wire_networks(tree)
    names_by_root = {}
    for kind in ("label", "hierarchical_label", "global_label"):
        for node in _all(tree, kind):
            at = _direct(node, "at")
            if at is None:
                continue
            point = (round(float(at[1]), 3), round(float(at[2]), 3))
            names_by_root.setdefault(dsu.find(point), set()).add(str(node[1]))
    for group in COMMON_PIN_NET_GROUPS:
        expected = str(group["name"])
        roots = []
        for reference, number in group["pins"]:
            info = pin_info.get((reference, number))
            if info is None:
                issues.append(f"common-net rule {expected} references missing pin {reference}.{number}")
                continue
            root = dsu.find(info["point"])
            roots.append(root)
            if expected not in names_by_root.get(root, set()):
                issues.append(f"{reference}.{number} must belong to common net {expected}")
        if len(roots) > 1 and len(set(roots)) > 1:
            if any(expected not in names_by_root.get(root, set()) for root in roots):
                issues.append(f"configured pins {group['pins']} do not all share {expected}")
    return issues


def required_no_connect_issues(tree: list) -> list[str]:
    """Require explicit no-connect markers at configured intentionally-unused pins."""
    issues = []
    pin_info = _pin_world_info(tree)
    markers = {
        (round(float(_direct(node, "at")[1]), 3), round(float(_direct(node, "at")[2]), 3))
        for node in _all(tree, "no_connect") if _direct(node, "at") is not None
    }
    for key in REQUIRED_NO_CONNECT_PINS:
        reference, number = key.split(".", 1)
        info = pin_info.get((reference, number))
        if info is None:
            issues.append(f"required NC rule references missing pin {key}")
        elif info["point"] not in markers:
            issues.append(f"unused pin {key} must have an explicit no-connect marker")
    return issues


def wire_compactness_issues(tree: list, budgets: dict | None = None) -> list[str]:
    """Score total internal wire length in each group against a configurable budget."""
    issues = []
    active_budgets = WIRE_LENGTH_BUDGETS_MM if budgets is None else budgets
    for zone, limit in active_budgets.items():
        frame = _frame_bounds(tree, zone)
        if frame is None:
            issues.append(f"wire compactness rule references missing frame {zone}")
            continue
        total = 0.0
        for segment in _segments(tree):
            if len(segment) != 2:
                continue
            start, end = segment
            if _inside(start, frame) and _inside(end, frame):
                total += hypot(end[0] - start[0], end[1] - start[1])
        score = total / float(limit)
        if score > 1.0 + TOL:
            issues.append(
                f"group {zone} wire compactness score is {score:.3f} "
                f"({total:.2f}/{float(limit):.2f} mm); maximum score is 1.000"
            )
    return issues


def circuit_documentation_issues(tree: list) -> list[str]:
    """Require non-trivial circuit groups to identify purpose and source evidence."""
    issues = []
    text_by_uuid = {
        str(uid[1]): str(node[1])
        for node in _all(tree, "text_box")
        if (uid := _direct(node, "uuid")) is not None
    }
    evidence_ok = bool(GROUP_EVIDENCE.get("datasheet") or GROUP_EVIDENCE.get("screenshot_asset"))
    if not evidence_ok:
        issues.append("group evidence must provide a datasheet or reviewed screenshot asset")
    if not GROUP_EVIDENCE.get("screenshot_asset") and not (
        GROUP_EVIDENCE.get("document") and GROUP_EVIDENCE.get("page") and GROUP_EVIDENCE.get("figure")
    ):
        issues.append("group evidence must identify its document, page and figure")
    for group in SCHEMATIC_GROUPS:
        members = tuple(group.get("documentation_members", ()))
        if not members:
            continue
        description_id = _uid(f"graphic:text:group-description:{group['id']}")
        description = text_by_uuid.get(description_id, "")
        missing = [reference for reference in members if reference not in description]
        if missing:
            issues.append(
                f"group {group['id']} must describe circuit members: {', '.join(missing)}"
            )
    return issues


def label_text_collision_issues(tree: list) -> list[str]:
    """Reject label glyph overlap with other labels, fields and annotation boxes."""
    issues = []
    labels = []
    for kind in ("label", "hierarchical_label", "global_label"):
        for node in _all(tree, kind):
            bounds = _text_visual_bounds(str(node[1]), node)
            at = _direct(node, "at")
            if bounds is not None and at is not None:
                labels.append((
                    kind, str(node[1]),
                    (float(at[1]), float(at[2])), bounds,
                ))
    for index, (kind, name, anchor, bounds) in enumerate(labels):
        for other_kind, other_name, other_anchor, other_bounds in labels[index + 1:]:
            if anchor != other_anchor and _overlap(bounds, other_bounds):
                issues.append(f"{kind} {name!r} overlaps {other_kind} {other_name!r}")
    visible_fields = _visible_properties(tree)
    for kind, name, _anchor, bounds in labels:
        for reference, field_name, field_bounds, _prop in visible_fields:
            if _overlap(bounds, field_bounds):
                issues.append(f"{kind} {name!r} overlaps {reference} {field_name}")
        for index, node in enumerate(_all(tree, "text_box")):
            at, size = _direct(node, "at"), _direct(node, "size")
            if at is None or size is None:
                continue
            box = (float(at[1]), float(at[2]), float(size[1]), float(size[2]))
            if _overlap(bounds, box):
                issues.append(f"{kind} {name!r} overlaps text_box {index}")
    return issues


def check_schematic_style(tree: list, generic_only: bool = False) -> list[str]:
    """Run every registered lint rule and return Clippy-style diagnostics."""
    generic_checks = (
        ("SCH001", wire_geometry_issues),
        ("SCH002", label_network_issues),
        ("SCH003", label_wire_overlap_issues),
        ("SCH008", text_box_issues),
        ("SCH010", symbol_field_clearance_issues),
        ("SCH011", block_annotation_issues),
        ("SCH013", wire_obstacle_issues),
        ("SCH018", label_text_collision_issues),
    )
    profile_checks = (
        ("SCH004", physical_topology_issues),
        ("SCH005", pin_orientation_issues),
        ("SCH006", component_spacing_issues),
        ("SCH007", group_metadata_issues),
        ("SCH009", frame_and_component_issues),
        ("SCH012", frame_content_clearance_issues),
        ("SCH014", common_pin_net_issues),
        ("SCH015", required_no_connect_issues),
        ("SCH016", wire_compactness_issues),
        ("SCH017", circuit_documentation_issues),
    )
    checks = generic_checks if generic_only else (*generic_checks, *profile_checks)
    return [
        f"{rule_id}: {issue}"
        for rule_id, checker in checks
        for issue in checker(tree)
    ]


def check_generic_schematic_style(tree: list) -> list[str]:
    """Run only reusable geometry and presentation checks."""
    return check_schematic_style(tree, generic_only=True)


def validate_schematic_style(path: Path, generic_only: bool = False) -> dict[str, int]:
    tree = parse_sexpr(Path(path).read_text(encoding="utf-8"))
    issues = check_schematic_style(tree, generic_only=generic_only)
    if issues:
        raise ValueError("Schematic style validation failed:\n- " + "\n- ".join(issues))
    return {
        "wires": len(_all(tree, "wire")),
        "local_labels": len(_all(tree, "label")),
        "hierarchical_labels": len(_all(tree, "hierarchical_label")),
        "text_boxes": len(_all(tree, "text_box")),
        "functional_frames": len(ZONES),
        "style_rules": len(STYLE_RULES),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schematic", type=Path, nargs="?")
    parser.add_argument(
        "--list-rules", action="store_true",
        help="list registered schematic lint rules and their severity",
    )
    parser.add_argument(
        "--generic-only", action="store_true",
        help="run reusable geometry/presentation rules without project contracts",
    )
    args = parser.parse_args()
    if args.list_rules:
        for rule_id, rule in STYLE_RULES.items():
            scope = rule.get("scope", "profile")
            print(
                f"{rule_id} [{rule['severity']}] [{scope}] "
                f"{rule['name']}: {rule['description']}"
            )
        return
    if args.schematic is None:
        parser.error("schematic is required unless --list-rules is used")
    totals = validate_schematic_style(args.schematic, generic_only=args.generic_only)
    mode = "generic schematic" if args.generic_only else "schematic profile"
    print(f"{mode.title()} style audit passed: " + ", ".join(f"{k}={v}" for k, v in totals.items()))


if __name__ == "__main__":
    main()
