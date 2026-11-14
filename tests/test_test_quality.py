"""Regression tests for exact-duplicate and no-op test detection."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts" / "check_test_quality.py"
SPEC = importlib.util.spec_from_file_location("kicad_blocks_test_quality", CHECKER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load scripts/check_test_quality.py.")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def test_reports_exact_duplicate_test_bodies_even_when_docstrings_differ(tmp_path: Path) -> None:
    """Flag body-identical tests in the same module and fixture scope."""
    test_root = tmp_path / "tests"
    test_root.mkdir()
    (test_root / "test_duplicates.py").write_text(
        'def test_first():\n'
        '    """First description."""\n'
        '    result = 3\n'
        '    assert result == 3\n'
        '\n'
        'def test_second():\n'
        '    """Different description."""\n'
        '    result = 3\n'
        '    assert result == 3\n',
        encoding="utf-8",
    )

    count, errors = checker.scan_tests(tmp_path)

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
        "class FirstFixture:\n"
        "    def test_value(self):\n"
        "        assert 3 == 3\n"
        "\n"
        "class SecondFixture:\n"
        "    def test_value(self):\n"
        "        assert 3 == 3\n",
        encoding="utf-8",
    )

    count, errors = checker.scan_tests(tmp_path)

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

    count, errors = checker.scan_tests(tmp_path)

    assert count == 2
    assert len(errors) == 2
    assert all("has no executable test body" in error for error in errors)
