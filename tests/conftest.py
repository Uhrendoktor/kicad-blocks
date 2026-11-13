"""Enforce the pytest module collection contract for the complete test inventory."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from scripts.test_inventory import expected_modules_for_args


def pytest_itemcollected(item: pytest.Item) -> None:
    """Record modules that produced items before keyword deselection is applied."""
    collected = getattr(item.config, "_kicad_blocks_collected_test_modules", None)
    if collected is None:
        collected = set()
        setattr(item.config, "_kicad_blocks_collected_test_modules", collected)
    collected.add(Path(str(item.path)).resolve())


def pytest_collection_finish(session: pytest.Session) -> None:
    """Fail when any selected, inventoried module silently collects zero tests."""
    expected = expected_modules_for_args(session.config.args, ROOT)
    collected = getattr(session.config, "_kicad_blocks_collected_test_modules", set())
    missing = sorted(path.relative_to(ROOT).as_posix() for path in expected - collected)
    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise pytest.UsageError(
            "pytest collection contract failed: these inventoried modules collected "
            f"no test items:\n{formatted}"
        )
