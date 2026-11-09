"""Pure validation and layout helpers for encapsulated sheet blocks."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

PIN_TYPES = ("input", "output", "bidirectional", "tristate", "passive")
S_EXPRESSION_SHAPES = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "tristate": "tri_state",
    "passive": "passive",
}


@dataclass(frozen=True, slots=True)
class BlockPin:
    """One electrical port on a block interface."""

    name: str
    electrical_type: str = "passive"

    @property
    def shape_token(self) -> str:
        """Return the shape spelling used in KiCad schematic S-expressions."""
        return S_EXPRESSION_SHAPES[self.electrical_type]


def slugify(value: str) -> str:
    """Convert a display name to a safe, portable filename stem."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", normalized).strip("_-").lower()
    if not slug:
        raise ValueError("Block name must contain at least one letter or number.")
    return slug[:48]


def parse_pins(spec: str) -> list[BlockPin]:
    """Parse `NAME:type, NAME:type` pin definitions."""
    pins: list[BlockPin] = []
    seen: set[str] = set()

    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        if ":" in token:
            name, electrical_type = (part.strip() for part in token.split(":", 1))
        else:
            name, electrical_type = token, "passive"

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./+\\-]*", name):
            raise ValueError(
                f"Invalid pin name: {name!r}. Start with a letter or underscore; "
                "use letters, numbers, _, ., /, + or -."
            )

        electrical_type = electrical_type.lower().replace("bi-directional", "bidirectional")
        if electrical_type == "bidir":
            electrical_type = "bidirectional"
        if electrical_type not in PIN_TYPES:
            raise ValueError(
                f"Unknown pin type {electrical_type!r}; choose {', '.join(PIN_TYPES)}."
            )

        key = name.casefold()
        if key in seen:
            raise ValueError(f"Duplicate pin name: {name}")
        seen.add(key)
        pins.append(BlockPin(name, electrical_type))

    if not pins:
        raise ValueError("Define at least one pin.")
    if len(pins) > 32:
        raise ValueError("The MVP supports at most 32 pins per block.")
    return pins


def block_geometry(pin_count: int, pin_pitch_mm: float = 5.08) -> tuple[float, int, int]:
    """Return (height, left_count, right_count) for a two-sided sheet symbol."""
    if pin_count < 1:
        raise ValueError("pin_count must be positive")
    if pin_pitch_mm <= 0:
        raise ValueError("pin_pitch_mm must be positive")
    left_count = (pin_count + 1) // 2
    right_count = pin_count - left_count
    max_side = max(left_count, right_count)
    height_mm = max(20.32, (max_side + 1) * pin_pitch_mm)
    return height_mm, left_count, right_count
