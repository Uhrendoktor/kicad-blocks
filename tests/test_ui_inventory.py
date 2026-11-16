"""Tests for the generated screen inventory and its honesty about coverage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.generate_ui_inventory import load_catalog, render_inventory  # noqa: E402

CATALOG_PATH = ROOT / "tests" / "ui_contract_catalog.json"
INVENTORY_PATH = ROOT / "docs" / "ui-inventory.md"


def test_catalog_has_unique_screen_ids_and_recognized_coverage_states():
    catalog = load_catalog()
    screens = catalog["screens"]
    assert len({screen["id"] for screen in screens}) == len(screens)
    assert {screen["coverage"] for screen in screens} <= {
        "contracted",
        "partial",
        "inventory-only",
    }


def test_every_known_top_level_screen_is_in_catalog():
    catalog = load_catalog()
    ids = {screen["id"] for screen in catalog["screens"]}
    expected = {
        "task-hub",
        "functional-block-library",
        "functional-block-picker",
        "record-picker",
        "instance-configuration",
        "create-block-form",
        "manage-libraries",
        "implementations-manager",
        "implementation-details",
        "open-implementation-chooser",
        "open-pcb-layout-chooser",
        "pcb-layout-manager",
        "add-pcb-layout",
        "error-confirmation-reports",
        "kicad-native-documents",
    }
    assert expected <= ids


def test_inventory_distinguishes_implemented_from_unimplemented_contracts():
    rendered = render_inventory(load_catalog())
    assert "Contracted — widget action and expected UI transition asserted" in rendered
    assert "Partial — targeted GUI, integration, or visual checks exist" in rendered
    assert "Inventory only — systematic UI contracts not implemented yet" in rendered
    assert "does not claim that a screenshot or a visible control proves" in rendered


def test_every_current_screenshot_is_listed_in_the_generated_inventory():
    catalog = load_catalog()
    rendered = render_inventory(catalog)
    assets = {
        asset
        for screen in catalog["screens"]
        for asset in (
            list(screen.get("screenshots", []))
            + [
                screenshot
                for view in screen.get("views", [])
                for screenshot in view.get("screenshots", [])
            ]
        )
    }
    assert assets
    for asset in assets:
        assert asset in rendered


def test_committed_inventory_matches_the_catalog():
    expected = render_inventory(load_catalog())
    actual = INVENTORY_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/ui-inventory.md is stale; run scripts/generate_ui_inventory.py."
    )


def test_catalog_rejects_duplicate_screen_ids(tmp_path):
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog["screens"].append(dict(catalog["screens"][0]))
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(ValueError, match="unique"):
        load_catalog(path)


def test_catalog_rejects_unregistered_screenshot_assets(tmp_path):
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog["screens"][0]["screenshots"].append("not-in-the-visual-inventory.png")
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(ValueError, match="outside scripts/visual_asset_inventory.py"):
        load_catalog(path)
