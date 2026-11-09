"""Opaque, library-backed schematic blocks for KiCad."""

from .model import BlockPin, parse_pins, render_symbol_library
from .repository import (
    add_dependency,
    create_block,
    list_blocks,
    make_local_copy,
    refresh_library_block,
    synchronize_local_copy,
)

__all__ = [
    "BlockPin",
    "parse_pins",
    "render_symbol_library",
    "add_dependency",
    "create_block",
    "list_blocks",
    "make_local_copy",
    "refresh_library_block",
    "synchronize_local_copy",
]
