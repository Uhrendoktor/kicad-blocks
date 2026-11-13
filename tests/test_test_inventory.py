"""Regression coverage for complete test-purpose inventory generation."""

from __future__ import annotations

from scripts.check_test_inventory import RUNNER_PURPOSES, render_inventory


def test_inventory_lists_all_discoverable_test_methods() -> None:
    """Render the full test-method inventory and its purpose column."""
    inventory = render_inventory()
    assert "## Test methods" in inventory
    assert "| Test | Purpose |" in inventory
    assert "test_recognizes_mock_and_pytest_exception_oracles" in inventory


def test_inventory_lists_every_standalone_acceptance_runner() -> None:
    """Keep script-style GUI, IPC and KiCad CLI runners visible outside pytest collection."""
    inventory = render_inventory()
    for runner in RUNNER_PURPOSES:
        assert f"`{runner}`" in inventory
    assert "pytest import/collection does not execute their" in inventory


def test_inventory_records_its_scope_totals() -> None:
    """Expose a stable summary for test methods, modules and direct-run runners."""
    inventory = render_inventory()
    assert "**Inventory totals:**" in inventory
    assert f"{len(RUNNER_PURPOSES)} standalone integration/evidence runners." in inventory
