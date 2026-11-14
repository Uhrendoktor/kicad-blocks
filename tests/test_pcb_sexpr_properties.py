"""Property tests for lossless KiCad PCB S-expression string serialization."""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from hypothesis import given, settings
from hypothesis import strategies as st

from kicad_blocks.model import sexpr_quote
from kicad_blocks.pcb_layout import _dump, _Quoted, parse_sexpr

SAFE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=512,
)


@settings(max_examples=250, deadline=None)
@given(value=SAFE_TEXT)
def test_quoted_property_value_round_trips_without_losing_escapes(value: str) -> None:
    """Preserve arbitrary Unicode, quotes, slashes, and newlines in quoted atoms."""
    document = f"(property \"Value\" {sexpr_quote(value)})"
    parsed = parse_sexpr(document)
    assert len(parsed) == 3
    assert isinstance(parsed[2], _Quoted)
    assert str(parsed[2]) == value
    assert parse_sexpr(_dump(parsed)) == parsed


@settings(max_examples=200, deadline=None)
@given(values=st.lists(SAFE_TEXT, min_size=1, max_size=20))
def test_ordered_property_values_round_trip_through_nested_sexpression(
    values: list[str],
) -> None:
    """Preserve child order and scalar values when serializing a nested expression."""
    document = "(root " + " ".join(sexpr_quote(value) for value in values) + ")"
    parsed = parse_sexpr(document)
    assert [str(value) for value in parsed[1:]] == values
    assert all(isinstance(value, _Quoted) for value in parsed[1:])
    assert parse_sexpr(_dump(parsed)) == parsed
