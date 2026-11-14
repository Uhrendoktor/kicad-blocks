"""Typed user and project settings persisted by KiCad Blocks."""

from __future__ import annotations

from typing import TypedDict


class LibrarySettings(TypedDict, total=False):
    """Registered shared-library roots in the per-user settings file."""

    library_root: str
    library_roots: list[str]


class InstanceLink(TypedDict):
    """One schematic-instance KIID bound to a project-local block."""

    local_path: str
    source_block_id: str | None
    updated_at: str


class InstanceLinks(TypedDict):
    """Project-local KIID-to-block lookup document."""

    schema_version: int
    instances: dict[str, InstanceLink]
