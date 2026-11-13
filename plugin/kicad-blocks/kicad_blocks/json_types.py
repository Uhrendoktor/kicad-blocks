"""Concrete recursive types for serialized KiCad Blocks data."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
JsonMapping: TypeAlias = Mapping[str, JsonValue]

SExpr: TypeAlias = str | list["SExpr"]
SExprList: TypeAlias = list[SExpr]


class BlockLayoutSource(Protocol):
    """Minimal block record surface consumed by PCB layout merge code."""

    path: Path
    manifest: JsonMapping

    @property
    def name(self) -> str:
        """Return the user-visible block name."""
