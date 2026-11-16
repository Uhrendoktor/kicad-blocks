"""Recursive JSON boundary types for parsed external project documents."""

from __future__ import annotations

type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]
