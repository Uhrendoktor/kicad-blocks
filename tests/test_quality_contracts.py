"""Property and policy regression tests for the strict Python quality contract."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_parameters, sexpr_quote, substitute_parameter_templates
from kicad_blocks.pcb_layout import _dump, parse_sexpr

from scripts.check_python_quality import python_policy_violations
from scripts.check_test_quality import scan_tests


def test_rejects_imported_any_alias_in_nested_annotations() -> None:
    """Rejects renamed Any imports even when nested inside a generic annotation."""
    source = "from typing import Any as DynamicValue\nvalue: list[DynamicValue]\n"
    violations = python_policy_violations(source, "fixture.py")
    assert len(violations) == 1
    assert "explicit Any reference" in violations[0]


def test_rejects_quoted_forward_reference_to_any() -> None:
    """Rejects explicit Any hidden in a quoted forward annotation."""
    source = 'from typing import Any\ndef read() -> "list[Any]": ...\n'
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


def test_ruff_policy_uses_full_selection_and_exact_compatibility_exceptions() -> None:
    """Keep Ruff comprehensive while limiting deliberate rule exceptions to known boundaries."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lint = project["tool"]["ruff"]["lint"]

    expected_global_ignores = {
        "COM812",
        "COM819",
        "D203",
        "D206",
        "D213",
        "D300",
        "E111",
        "E114",
        "E117",
        "ISC002",
        "N999",
        "PLC0415",
        "PLR6301",
        "UP031",
        "Q000",
        "Q001",
        "Q002",
        "Q003",
        "Q004",
        "W191",
    }
    bootstrap_paths = {
        "plugin/kicad-blocks/entrypoints/action_hub.py",
        "plugin/kicad-blocks/entrypoints/browse_blocks.py",
        "plugin/kicad-blocks/entrypoints/create_block.py",
        "plugin/kicad-blocks/entrypoints/open_implementation.py",
        "scripts/validate_schematic_style.py",
        "examples/stusb4500/generate_reference.py",
        "tests/test_schematic_style_rules.py",
        "tests/test_variants.py",
        "tests/test_profiles.py",
        "tests/test_operations.py",
        "tests/test_library_validation.py",
        "tests/test_pcb_placement.py",
        "tests/test_library_picker_gui.py",
        "tests/validate_kicad_cli.py",
        "tests/test_auto_dependencies.py",
        "tests/test_extraction.py",
        "tests/test_blocks.py",
        "tests/test_pcb_layout.py",
        "tests/test_pcb_layout_unit.py",
        "tests/test_eeschema_gui.py",
        "tests/test_ipc_headless.py",
        "tests/test_stusb4500_reference.py",
        "tests/test_editor_workflow.py",
        "tests/test_library_browser.py",
        "tests/create_block_ipc_integration.py",
        "tests/test_plugin_registration.py",
        "tests/test_instance_configuration_gui.py",
        "tests/capture_visual_docs.py",
    }
    expected_per_file = {
        "tests/**/*.py": ["S101", "PLR2004", "T201"],
        "scripts/check_python_quality.py": ["T201"],
        "scripts/check_test_quality.py": ["T201"],
        "scripts/test_inventory.py": ["T201"],
        "scripts/verify_release_gate.py": ["T201"],
        "scripts/validate_docs.py": ["T201"],
        **{path: ["E402"] for path in bootstrap_paths},
    }

    assert lint["select"] == ["ALL"]
    assert lint["preview"] is True
    assert set(lint["ignore"]) == expected_global_ignores
    assert lint["per-file-ignores"] == expected_per_file


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
