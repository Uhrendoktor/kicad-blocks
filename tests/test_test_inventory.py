"""Tests for the authoritative test inventory and pytest collection contract."""

from __future__ import annotations

from pathlib import Path

from scripts.test_inventory import (
    ROOT,
    PYTEST_GROUPS,
    collect_test_case_names,
    expected_modules_for_args,
    pytest_module_purposes,
    validate_routing,
)


def test_inventory_routes_every_test_module_exactly_once() -> None:
    """Keep every test module classified and every standalone script wired into CI."""
    assert validate_routing(ROOT) == ()


def test_pytest_module_selection_respects_full_and_focused_runs() -> None:
    """Require the full suite by default but only the selected module for focused runs."""
    all_modules = expected_modules_for_args([], ROOT)
    assert len(all_modules) == sum(len(group) for group in PYTEST_GROUPS.values())
    assert all_modules == frozenset((ROOT / path).resolve() for path in pytest_module_purposes())
    focused = expected_modules_for_args(["tests/test_blocks.py::test_example"], ROOT)
    assert focused == frozenset({(ROOT / "tests/test_blocks.py").resolve()})


def test_ast_discovery_includes_test_functions_and_test_class_methods() -> None:
    """Discover pytest cases without counting helpers or nested local functions."""
    source = '''
def test_module_case():
    def test_nested_helper():
        pass

class ExampleTests:
    def test_class_case(self):
        pass

def helper():
    pass
'''
    assert collect_test_case_names(source) == ("test_module_case", "test_class_case")
