"""Recursive JSON boundary types for parsed external documents.

These aliases are intentionally separate from manifest TypedDicts: they model
untrusted JSON before validation narrows a document into a domain schema.
"""

from __future__ import annotations

type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]
