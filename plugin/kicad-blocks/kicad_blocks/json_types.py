"""Concrete recursive value types for KiCad manifests and S-expressions."""

from __future__ import annotations

# JSON manifest/configuration values. Use concrete JSON values instead of untyped escape hatches.
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]

# KiCad schematic/PCB S-expressions are nested lists of scalar string tokens.
type SExpr = str | list[SExpr]
