"""Property and policy regression tests for the strict Python quality contract."""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_parameters, sexpr_quote, substitute_parameter_templates
from kicad_blocks.pcb_layout import _dump, parse_sexpr

from scripts.check_python_quality import (
    annotation_debt,
    annotation_ratchet_violations,
    python_policy_violations,
)
from scripts.check_test_quality import scan_tests


def test_rejects_imported_any_alias_in_nested_annotations() -> None:
    """Rejects renamed Any imports even when nested inside a generic annotation."""
    source = "from typing import Any as DynamicValue\nvalue: list[DynamicValue]\n"
    violations = python_policy_violations(source, "fixture.py")
    assert len(violations) == 1
    assert "explicit Any reference" in violations[0]


def test_rejects_quoted_forward_reference_to_any() -> None:
    """Rejects explicit Any hidden in a quoted forward annotation."""
    source = "from typing import Any\ndef read() -> \"list[Any]\": ...\n"
    violations = python_policy_violations(source, "fixture.py")
    assert len(violations) == 1
    assert "explicit Any reference" in violations[0]


def test_rejects_generic_object_aliases_inside_annotations() -> None:
    """Rejects builtins.object aliases nested inside parameterized types."""
    source = "from builtins import object as Dynamic\nvalue: dict[str, list[Dynamic]]\n"
    violations = python_policy_violations(source, "fixture.py")
    assert len(violations) == 1
    assert "generic object annotation" in violations[0]


def test_allows_runtime_object_construction_and_ordinary_prose() -> None:
    """Keeps runtime object construction and English prose outside the annotation ban."""
    source = (
        '"""An object can be created when a runtime adapter needs a placeholder."""\n'
        "placeholder = object()\n"
        "def label(value: str) -> str:\n    return value\n"
    )
    assert python_policy_violations(source, "fixture.py") == ()


def test_annotation_debt_counts_parameters_and_returns_separately() -> None:
    """Counts missing parameter and return annotations as separate ratchet rules."""
    source = "def legacy(value):\n    return value\n"
    assert annotation_debt(source, "fixture.py") == Counter(
        {
            "missing-parameter-annotation": 1,
            "missing-return-annotation": 1,
        }
    )


@settings(max_examples=80, deadline=None)
@given(st.lists(st.text(max_size=80), min_size=0, max_size=12))
def test_quoted_sexpr_values_round_trip(values: list[str]) -> None:
    """Preserves arbitrary quoted Unicode/text values through KiCad S-expression parsing."""
    source = "(root" + "".join(" " + sexpr_quote(value) for value in values) + ")"
    assert parse_sexpr(_dump(parse_sexpr(source))) == ["root", *values]


@settings(max_examples=80, deadline=None)
@given(st.text(alphabet=st.characters(blacklist_characters="\r\n"), max_size=120))
def test_parameter_values_remain_one_quoted_sexpr_field(value: str) -> None:
    """Escapes text parameters so quotes and delimiters cannot alter S-expression structure."""
    parameters = parse_parameters("LABEL:string=default")
    source = '(kicad_sch (property "Value" "{{LABEL}}"))'
    rendered = substitute_parameter_templates(source, parameters, {"LABEL": value})
    parsed = parse_sexpr(rendered)
    assert parsed[1][2] == value
    assert len(parsed) == 2


def test_changed_file_ratchet_rejects_increased_annotation_debt(tmp_path: Path) -> None:
    """Rejects a changed Python file that adds unannotated parameters and returns."""
    def run_git(*arguments: str) -> None:
        subprocess.run(
            ("git", *arguments),
            cwd=tmp_path,
            check=True,
            capture_output=True,
            timeout=30,
        )

    run_git("init")
    run_git("config", "user.name", "Quality Contract Test")
    run_git("config", "user.email", "quality-contract@example.invalid")
    source = tmp_path / "module.py"
    source.write_text(
        "def stable(value: str) -> str:\n    return value\n",
        encoding="utf-8",
    )
    run_git("add", "module.py")
    run_git("commit", "-m", "base")
    run_git("branch", "quality-base")
    source.write_text(
        "def stable(value: str) -> str:\n    return value\n\n"
        "def introduced(value):\n    return value\n",
        encoding="utf-8",
    )
    run_git("add", "module.py")
    run_git("commit", "-m", "introduce annotation debt")

    violations = annotation_ratchet_violations("quality-base", root=tmp_path)
    assert len(violations) == 2
    assert any("missing-parameter-annotation increased" in item for item in violations)
    assert any("missing-return-annotation increased" in item for item in violations)

def test_reports_exact_duplicate_test_bodies_even_when_docstrings_differ(tmp_path: Path) -> None:
    """Flag body-identical test functions despite different descriptive docstrings."""
    test_root = tmp_path / "tests"
    test_root.mkdir()
    (test_root / "test_duplicates.py").write_text(
        "def test_first():\n"
        '    """First description."""\n'
        "    result = 3\n"
        "    assert result == 3\n"
        "\n"
        "def test_second():\n"
        '    """Different description."""\n'
        "    result = 3\n"
        "    assert result == 3\n",
        encoding="utf-8",
    )

    count, errors = scan_tests(tmp_path)

    assert count == 2
    assert len(errors) == 1
    assert "test_first" in errors[0] and "test_second" in errors[0]


def test_different_bodies_and_class_fixture_scopes_are_not_duplicates(tmp_path: Path) -> None:
    """Keep distinct expectations and separate class fixture scopes independent."""
    test_root = tmp_path / "tests"
    test_root.mkdir()
    (test_root / "test_distinct.py").write_text(
        "def test_first():\n"
        "    assert 1 == 1\n"
        "\n"
        "def test_second():\n"
        "    assert 2 == 2\n"
        "\n"
        "class TestFirstFixture:\n"
        "    def test_value(self):\n"
        "        assert 3 == 3\n"
        "\n"
        "class TestSecondFixture:\n"
        "    def test_value(self):\n"
        "        assert 3 == 3\n",
        encoding="utf-8",
    )

    count, errors = scan_tests(tmp_path)

    assert count == 4
    assert errors == []


def test_reports_empty_and_pass_only_test_bodies(tmp_path: Path) -> None:
    """Reject tests with no executable assertion or behavioral check."""
    test_root = tmp_path / "tests"
    test_root.mkdir()
    (test_root / "test_noop.py").write_text(
        "def test_empty():\n"
        "    pass\n"
        "\n"
        "def test_documented_only():\n"
        '    """This is only documentation."""\n',
        encoding="utf-8",
    )

    count, errors = scan_tests(tmp_path)

    assert count == 2
    assert len(errors) == 2
    assert all("has no executable test body" in error for error in errors)
