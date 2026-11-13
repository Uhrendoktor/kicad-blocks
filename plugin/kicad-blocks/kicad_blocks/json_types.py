# Concrete recursive value types for KiCad manifests and S-expressions.

from __future__ import annotations

from typing import TypeAlias

# JSON manifest/configuration values. Keep Any out of the production model.
JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]

# KiCad schematic/PCB S-expressions are nested lists of scalar string tokens.
SExpr: TypeAlias = str | list["SExpr"]
