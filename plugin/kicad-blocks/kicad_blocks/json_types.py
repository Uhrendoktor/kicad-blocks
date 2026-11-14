"""Recursive JSON types shared by manifests, reports, and KiCad IPC payloads.

The aliases model JSON-compatible values instead of using Any or object as an
escape hatch. Mapping and Sequence accept ordinary dict and list values from
json.loads while keeping read-only consumers accurately typed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import NoneType

type JsonValue = NoneType | bool | int | float | str | Sequence[JsonValue] | Mapping[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]
