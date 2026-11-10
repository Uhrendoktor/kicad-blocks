"""Opaque, library-backed schematic blocks for KiCad."""

from .model import BlockPin, parse_pins, render_symbol_library
from .repository import (
    add_dependency,
    create_block,
    list_blocks,
    make_local_copy,
    publish_local_copy,
    refresh_library_block,
    resolve_dependency_graph,
    synchronize_local_copy,
)

__all__ = [
    "BlockPin",
    "add_dependency",
    "create_block",
    "list_blocks",
    "make_local_copy",
    "parse_pins",
    "publish_local_copy",
    "refresh_library_block",
    "render_symbol_library",
    "resolve_dependency_graph",
    "synchronize_local_copy",
]
