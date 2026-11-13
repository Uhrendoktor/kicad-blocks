"""Regression coverage for the repository-wide concrete-type policy."""

from __future__ import annotations

import pytest

from scripts.check_python_type_policy import source_type_policy_violations


@pytest.mark.parametrize(
    "source",
    (
        "from typing import Any\nvalue: Any\n",
        "import typing as t\nvalue: list[t.Any]\n",
        "from typing import Any as Dynamic\nvalue: dict[str, Dynamic]\n",
        "from typing_extensions import Any as Dynamic\nvalue: Dynamic\n",
        "from typing import TypeAlias, Any\nPayload: TypeAlias = dict[str, Any]\n",
        "from typing import Any\ndef parse(value: 'list[Any]') -> None: ...\n",
        "from typing import Any\ndef parse(value): # type: (Any) -> Any\n    return value\n",
    ),
)
def test_rejects_any_annotations_and_aliases(source: str) -> None:
    """Reject direct, imported, qualified, aliased, forward-reference and comment Any."""
    assert source_type_policy_violations(source)


@pytest.mark.parametrize(
    "source",
    (
        "value: object\n",
        "value: dict[str, object]\n",
        "import builtins\nvalue: builtins.object\n",
        "import builtins as b\nvalue: b.object\n",
        "from builtins import object as NativeObject\nvalue: NativeObject\n",
    ),
)
def test_rejects_generic_object_annotations(source: str) -> None:
    """Reject unrestricted object annotations at every nested level."""
    assert any(
        "generic built-in object annotations" in issue.message
        for issue in source_type_policy_violations(source)
    )


def test_runtime_object_operations_are_not_annotations() -> None:
    """Keep legitimate runtime object construction and object.__setattr__ available."""
    source = "instance = object()\nobject.__setattr__(instance, 'value', 1)\n"
    assert source_type_policy_violations(source) == ()


def test_concrete_json_aliases_and_protocols_are_accepted() -> None:
    """Allow recursive JSON values and narrow structural collaborators."""
    source = """
from typing import Protocol, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

class Point(Protocol):
    x: float
    y: float

def consume(value: Point) -> float:
    return value.x + value.y
"""
    assert source_type_policy_violations(source) == ()


def test_ordinary_strings_are_not_interpreted_as_type_syntax() -> None:
    """Do not scan ordinary string values for words that name forbidden types."""
    source = 'message: str = "Any and object are not type annotations"\n'
    assert source_type_policy_violations(source) == ()


def test_invalid_python_fails_closed() -> None:
    """Treat an unparsable source file as a policy error instead of skipping it."""
    violations = source_type_policy_violations("value: Any =\n", "broken.py")
    assert violations and "invalid Python syntax" in violations[0].message
