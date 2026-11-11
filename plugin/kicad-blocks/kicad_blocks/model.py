"""Pure data model and KiCad S-expression generators for black-box blocks."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

PIN_TYPES = ("input", "output", "bidirectional", "tristate", "passive")
_KICAD_PIN_TYPES = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "tristate": "tri_state",
    "passive": "passive",
}


@dataclass(frozen=True, slots=True)
class BlockPin:
    """One external electrical pin on a block facade."""

    name: str
    electrical_type: str = "passive"
    number: str = ""

    def as_manifest(self, index: int) -> dict[str, str]:
        return {
            "name": self.name,
            "number": self.number or str(index),
            "electrical_type": self.electrical_type,
        }


PARAMETER_TYPES = ("string", "number", "integer", "boolean")
PARAMETER_FIELD_PREFIX = "KB_PARAM_"
LAYOUT_FIELD_NAME = "KB_LAYOUT"
VARIANT_FIELD_NAME = "KB_VARIANT"
_PARAMETER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PARAMETER_TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def parameter_field_name(name: str) -> str:
    """Return the KiCad custom-field name reserved for a block parameter."""
    if not _PARAMETER_NAME.fullmatch(str(name)):
        raise ValueError(f"Invalid block parameter name: {name!r}")
    return PARAMETER_FIELD_PREFIX + str(name)


def resolve_variant_implementation(manifest: dict, value: str) -> str:
    """Return the implementation filename for an exact variant value."""
    variants = manifest.get("variants") or []
    if not variants:
        return str(manifest["implementation_file"])
    requested = str(value or "").strip()
    if not requested:
        raise ValueError("KB_VARIANT is empty. Set it to a KiCad text variable or a literal value.")
    matches = [
        item for item in variants
        if str(item.get("value", "")).casefold() == requested.casefold()
    ]
    if len(matches) != 1:
        choices = ", ".join(str(item.get("value", "")) for item in variants)
        raise ValueError(f"Unknown KB_VARIANT value {requested!r}. Available values: {choices}.")
    return str(matches[0]["file"])


def validate_parameter_value(parameter: dict, value: object) -> str:
    """Validate and normalize one single-line parameter value."""
    name = str(parameter.get("name", "parameter"))
    kind = str(parameter.get("type", "string")).casefold()
    text = str(value)
    if len(text) > 512 or "\n" in text or "\r" in text:
        raise ValueError(f"Value for parameter {name!r} must be at most 512 single-line characters.")
    if kind == "string":
        return text
    if kind == "number":
        try:
            numeric = float(text)
        except ValueError as exc:
            raise ValueError(f"Parameter {name!r} must be a number.") from exc
        if not math.isfinite(numeric):
            raise ValueError(f"Parameter {name!r} must be a finite number.")
        return text
    if kind == "integer":
        if not re.fullmatch(r"[+-]?\d+", text.strip()):
            raise ValueError(f"Parameter {name!r} must be an integer.")
        return str(int(text.strip()))
    if kind == "boolean":
        values = {"true": "true", "false": "false", "1": "true", "0": "false", "yes": "true", "no": "false"}
        try:
            return values[text.strip().casefold()]
        except KeyError as exc:
            raise ValueError(f"Parameter {name!r} must be true or false.") from exc
    raise ValueError(f"Unsupported type {kind!r} for parameter {name!r}.")


def normalize_parameters(parameters: Iterable[dict] | None) -> list[dict[str, str]]:
    """Validate parameter declarations from a block manifest or API call."""
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parameters or ():
        if not isinstance(item, dict):
            raise TypeError("Each block parameter must be an object.")
        name = str(item.get("name", "")).strip()
        if not _PARAMETER_NAME.fullmatch(name):
            raise ValueError(f"Invalid block parameter name {name!r}.")
        key = name.casefold()
        if key in seen:
            raise ValueError(f"Duplicate block parameter name: {name}")
        seen.add(key)
        kind = str(item.get("type", "string")).strip().casefold()
        if kind == "bool":
            kind = "boolean"
        if kind not in PARAMETER_TYPES:
            raise ValueError(
                f"Unknown type {kind!r} for parameter {name!r}; choose {', '.join(PARAMETER_TYPES)}."
            )
        default = validate_parameter_value(
            {"name": name, "type": kind}, item.get("default", "")
        )
        description = str(item.get("description", "")).strip()
        if len(description) > 256 or "\n" in description or "\r" in description:
            raise ValueError(f"Description for parameter {name!r} must be a single line of at most 256 characters.")
        normalized.append(
            {"name": name, "type": kind, "default": default, "description": description}
        )
    return normalized


def parse_parameters(spec: str) -> list[dict[str, str]]:
    """Parse NAME[:type]=DEFAULT declarations separated by commas."""
    parameters = []
    for token in str(spec).split(","):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError(f"Invalid parameter {token!r}; use NAME[:type]=DEFAULT.")
        declaration, default = (part.strip() for part in token.split("=", 1))
        if ":" in declaration:
            name, kind = (part.strip() for part in declaration.split(":", 1))
        else:
            name, kind = declaration, "string"
        parameters.append({"name": name, "type": kind, "default": default})
    return normalize_parameters(parameters)


def resolve_parameter_values(
    parameters: Iterable[dict] | None, overrides: dict[str, object] | None = None
) -> dict[str, str]:
    """Resolve defaults plus caller overrides, rejecting unknown parameters."""
    definitions = normalize_parameters(parameters)
    values = {item["name"]: item["default"] for item in definitions}
    for name, value in (overrides or {}).items():
        if name not in values:
            raise ValueError(f"Unknown block parameter {name!r}.")
        declaration = next(item for item in definitions if item["name"] == name)
        values[name] = validate_parameter_value(declaration, value)
    return values


def substitute_parameter_templates(
    content: str,
    parameters: Iterable[dict] | None,
    overrides: dict[str, object] | None = None,
) -> str:
    """Substitute {{NAME}} tokens in KiCad text fields using validated values.

    Values are escaped for insertion inside an existing quoted S-expression
    string. Templates are deliberately limited to single-line values.
    """
    values = resolve_parameter_values(parameters, overrides)
    matches = list(_PARAMETER_TOKEN.finditer(content))
    tokens = {match.group(1) for match in matches}
    unknown = sorted(tokens - set(values))
    if unknown:
        raise ValueError("Implementation uses undeclared block parameter(s): " + ", ".join(unknown))

    # A parameter is text, not an S-expression fragment. Only expand tokens
    # inside an existing quoted KiCad string, never in structure or comments.
    in_string = False
    in_comment = False
    escaped = False
    token_positions = {match.start() for match in matches}
    for position, character in enumerate(content):
        if position in token_positions and (in_comment or not in_string):
            raise ValueError(
                "Block parameter templates must appear inside quoted KiCad text fields."
            )
        if in_comment:
            if character == "\n":
                in_comment = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == ";":
            in_comment = True
        elif character == '"':
            in_string = True

    def escape(match: re.Match) -> str:
        value = values[match.group(1)]
        return value.replace("\\", "\\\\").replace('"', '\\"')

    return _PARAMETER_TOKEN.sub(escape, content)


def slugify(value: str) -> str:
    """Return a safe, deterministic KiCad symbol and file name."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", normalized).strip("_").lower()
    if not slug or not re.match(r"[a-z0-9_]", slug):
        raise ValueError("Block name must contain at least one letter or number.")
    return slug[:48]


def parse_pins(spec: str) -> list[BlockPin]:
    """Parse comma-separated NAME:type definitions. Type defaults to passive."""
    pins: list[BlockPin] = []
    seen_names: set[str] = set()

    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        if ":" in token:
            name, raw_type = (part.strip() for part in token.split(":", 1))
        else:
            name, raw_type = token, "passive"
        electrical_type = raw_type.casefold().replace("bi-directional", "bidirectional")
        if electrical_type == "bidir":
            electrical_type = "bidirectional"
        if electrical_type not in PIN_TYPES:
            raise ValueError(
                f"Unknown pin type {raw_type!r}; choose {', '.join(PIN_TYPES)}."
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./+\\-]*", name):
            raise ValueError(
                f"Invalid pin name {name!r}. Use letters, numbers, _, ., /, + or -; "
                "the first character must be a letter or underscore."
            )
        key = name.casefold()
        if key in seen_names:
            raise ValueError(f"Duplicate pin name: {name}")
        seen_names.add(key)
        pins.append(BlockPin(name, electrical_type, str(len(pins) + 1)))

    if not pins:
        raise ValueError("Define at least one external pin.")
    if len(pins) > 64:
        raise ValueError("The MVP supports up to 64 external pins per block.")
    return pins


def sexpr_quote(value: str) -> str:
    """Quote a string using KiCad's S-expression string conventions."""
    return json.dumps(str(value), ensure_ascii=False)


def _pin_y(row: int, row_count: int, pitch: float = 2.54) -> float:
    return round(((row_count - 1) / 2.0 - row) * pitch, 4)


def facade_pin_connection_points(
    pins: Iterable[BlockPin],
) -> dict[str, tuple[float, float, int]]:
    """Return pin-name to (connection-x, connection-y, label-spin-style).

    The point is the outer endpoint of the rendered symbol pin line.
    """
    pins = list(pins)
    if not pins:
        raise ValueError("A block symbol must have at least one pin.")
    left_count = (len(pins) + 1) // 2
    right_count = len(pins) - left_count
    rows = max(left_count, right_count)
    connection_x = 5.08 + 2.54
    points: dict[str, tuple[float, float, int]] = {}
    for index, pin in enumerate(pins):
        if index < left_count:
            row, x, spin_style = index, -connection_x, 1
        else:
            row, x, spin_style = index - left_count, connection_x, 3
        points[pin.name] = (x, _pin_y(row, rows), spin_style)
    return points


def transform_symbol_point_to_schematic(
    x_mm: float, y_mm: float, rotation_deg: int = 0
) -> tuple[float, float]:
    """Transform a symbol-local Y-up point into sheet Y-down coordinates.

    The four transforms match KiCad's SCH_SYMBOL orientation matrix:
    0°=(x,-y), 90°=(-y,-x), 180°=(-x,y), 270°=(y,x).
    """
    transforms = {
        0: (x_mm, -y_mm),
        90: (-y_mm, -x_mm),
        180: (-x_mm, y_mm),
        270: (y_mm, x_mm),
    }
    try:
        return transforms[rotation_deg]
    except KeyError as exc:
        raise ValueError("Rotation must be 0, 90, 180 or 270 degrees.") from exc


def schematic_symbol_orientation(rotation_deg: int = 0) -> int:
    """Return the KiCad IPC SchematicSymbolOrientation enum value."""
    try:
        return {0: 1, 90: 2, 180: 3, 270: 4}[rotation_deg]
    except KeyError as exc:
        raise ValueError("Rotation must be 0, 90, 180 or 270 degrees.") from exc


def rotate_label_spin_style(spin_style: int, rotation_deg: int = 0) -> int:
    """Rotate a KiCad SchematicLabelSpinStyle enum with its owning symbol."""
    maps = {
        0: {1: 1, 2: 2, 3: 3, 4: 4},
        90: {1: 4, 2: 1, 3: 2, 4: 3},
        180: {1: 3, 2: 4, 3: 1, 4: 2},
        270: {1: 2, 2: 3, 3: 4, 4: 1},
    }
    try:
        return maps[rotation_deg][spin_style]
    except KeyError as exc:
        raise ValueError(
            "Spin style must be LEFT/UP/RIGHT/BOTTOM and rotation must be 0, 90, 180 or 270."
        ) from exc


def render_symbol_library(
    symbol_name: str,
    display_name: str,
    block_id: str,
    pins: Iterable[BlockPin],
    *,
    reference_prefix: str = "X",
    parameters: Iterable[dict] = (),
    default_layout_id: str | None = None,
) -> str:
    """Generate a one-symbol .kicad_sym library with an opaque IC-like facade."""
    pins = list(pins)
    if not pins:
        raise ValueError("A block symbol must have at least one pin.")
    if not re.fullmatch(r"[A-Za-z0-9_]+", symbol_name):
        raise ValueError("Symbol name must contain only letters, numbers and underscores.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", reference_prefix):
        raise ValueError("Reference prefix must start with a letter and contain alphanumerics.")

    left_count = (len(pins) + 1) // 2
    right_count = len(pins) - left_count
    rows = max(left_count, right_count)
    half_height = max(5.08, ((rows - 1) * 2.54) / 2.0 + 2.54)
    top = round(half_height, 4)
    bottom = round(-half_height, 4)
    half_width = 5.08
    pin_x = half_width + 2.54
    lines = [
        "(kicad_symbol_lib",
        "  (version 20250114)",
        '  (generator "kicad_blocks")',
        f"  (symbol {sexpr_quote(symbol_name)}",
        "    (pin_names (offset 0.762))",
        "    (exclude_from_sim no)",
        "    (in_bom yes)",
        "    (on_board yes)",
        "    (property \"Reference\" " + sexpr_quote(reference_prefix) +
        " (at 0 " + str(round(top + 2.54, 4)) +
        ' 0) (effects (font (size 1.27 1.27)) (justify left bottom)))',
        "    (property \"Value\" " + sexpr_quote(display_name) +
        " (at 0 " + str(round(bottom - 2.54, 4)) +
        ' 0) (effects (font (size 1.27 1.27)) (justify left top)))',
        '    (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
        '    (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
        '    (property "Block ID" ' + sexpr_quote(block_id) +
        ' (at 0 0 0) (effects (font (size 1.0 1.0)) hide))',
        '    (property "Block Type" "kicad-blocks" (at 0 0 0) (effects (font (size 1.0 1.0)) hide))',
        f"    (symbol {sexpr_quote(symbol_name + '_0_1')}",
        "      (rectangle (start " + str(-half_width) + " " + str(top) +
        ") (end " + str(half_width) + " " + str(bottom) +
        ") (stroke (width 0.254) (type default)) (fill (type background)))",
        "    )",
        f"    (symbol {sexpr_quote(symbol_name + '_1_1')}",
    ]

    parameter_fields = [
        '    (property "KB_VARIANT" "" (at 0 0 0) (effects (font (size 1.0 1.0)) hide))'
    ] + [
        "    (property " + sexpr_quote(parameter_field_name(item["name"])) + " "
        + sexpr_quote(item["default"]) + " (at 0 0 0) "
        + '(effects (font (size 1.0 1.0)) hide))'
        for item in normalize_parameters(parameters)
    ]
    if default_layout_id:
        if not re.fullmatch(r"[a-z0-9_]+", default_layout_id):
            raise ValueError("Default PCB layout ID must use lowercase letters, numbers and underscores.")
        parameter_fields.append(
            "    (property " + sexpr_quote(LAYOUT_FIELD_NAME) + " "
            + sexpr_quote(default_layout_id) + " (at 0 0 0) "
            + '(effects (font (size 1.0 1.0)) hide))'
        )
    if parameter_fields:
        insertion = next(
            index for index, line in enumerate(lines)
            if line == f"    (symbol {sexpr_quote(symbol_name + '_0_1')}"
        )
        lines[insertion:insertion] = parameter_fields

    for index, pin in enumerate(pins):
        if index < left_count:
            row = index
            x, angle = -pin_x, 0
        else:
            row = index - left_count
            x, angle = pin_x, 180
        y = _pin_y(row, rows)
        number = pin.number or str(index + 1)
        lines.extend(
            [
                f"      (pin {_KICAD_PIN_TYPES[pin.electrical_type]} line (at {x} {y} {angle}) (length 2.54)",
                f"        (name {sexpr_quote(pin.name)} (effects (font (size 1.0 1.0))))",
                f"        (number {sexpr_quote(number)} (effects (font (size 1.0 1.0))))",
                "      )",
            ]
        )
    lines.extend(["    )", "  )", ")", ""])
    return "\n".join(lines)


def render_implementation_schematic(
    display_name: str, block_id: str, pins: Iterable[BlockPin]
) -> str:
    """Generate a separate editable schematic document with interface net labels.

    This is a standalone asset in the block library. It is not included in the
    project hierarchy and creates no child-sheet file when a facade is placed.
    """
    pins = list(pins)
    document_uuid = str(uuid.uuid4())
    lines = [
        '(kicad_sch (version 20250114) (generator "kicad_blocks")',
        f"  (uuid {sexpr_quote(document_uuid)})",
        '  (paper "A4")',
        '  (title_block (title ' + sexpr_quote(display_name) +
        ') (comment 1 ' + sexpr_quote("Block ID: " + block_id) + '))',
        "  (lib_symbols)",
    ]
    for index, pin in enumerate(pins):
        x = 50.8 if index < 32 else 152.4
        row = index if index < 32 else index - 32
        y = 38.1 + row * 5.08
        lines.extend(
            [
                f"  (global_label {sexpr_quote(pin.name)}",
                f"    (shape {_KICAD_PIN_TYPES[pin.electrical_type]})",
                f"    (at {x} {y} 0)",
                '    (effects (font (size 1.27 1.27)) (justify left bottom))',
                f"    (uuid {sexpr_quote(str(uuid.uuid4()))})",
                "  )",
            ]
        )
    lines.extend(
        [
            '  (sheet_instances (path "/" (page "1")))',
            "  (embedded_fonts no)",
            ")",
            "",
        ]
    )
    return "\n".join(lines)


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def revision_for_hashes(hashes: dict[str, str]) -> str:
    payload = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
