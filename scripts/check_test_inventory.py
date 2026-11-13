#!/usr/bin/env python3
"""Validate and render the canonical test/acceptance inventory."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTATION_PATH = ROOT / "docs" / "test-inventory.md"
PYTEST_MODE = "pytest"
STANDALONE_MODES = {"standalone-gui", "standalone-ipc", "standalone-kicad"}
WORKFLOW_MODE = "workflow-helper"
VALID_MODES = STANDALONE_MODES | {PYTEST_MODE, WORKFLOW_MODE}


def load_inventory(root: Path = ROOT) -> list[dict[str, str]]:
    """Load and validate the authoritative JSON inventory."""
    payload = json.loads((root / "tests" / "test_inventory.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Test inventory must be a schema_version 1 JSON object.")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("Test inventory entries must be a JSON array.")
    entries: list[dict[str, str]] = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"Test inventory entry {index} must be a JSON object.")
        entry = {str(key): str(value) for key, value in raw_entry.items()}
        if not all(entry.get(key, "").strip() for key in ("path", "mode", "purpose")):
            raise ValueError(f"Test inventory entry {index} is missing path, mode, or purpose.")
        entries.append(entry)
    return entries


def _has_test_function(tree: ast.AST) -> bool:
    """Return whether a module contains a pytest- or unittest-discoverable test."""
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )


def _has_main_guard(tree: ast.AST) -> bool:
    """Return whether a standalone validation script has an executable main guard."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not isinstance(node.left, ast.Name) or node.left.id != "__name__":
            continue
        if any(
            isinstance(value, ast.Constant) and value.value == "__main__"
            for value in node.comparators
        ):
            return True
    return False


def expected_entrypoints(root: Path = ROOT) -> set[str]:
    """Find every test module and the explicitly inventoried validation helpers."""
    discovered = {
        path.relative_to(root).as_posix()
        for path in (root / "tests").glob("test_*.py")
        if path.is_file()
    }
    discovered.update(
        {"tests/capture_visual_docs.py", "tests/validate_kicad_cli.py"}
    )
    return discovered


def validate_inventory(root: Path, entries: list[dict[str, str]]) -> list[str]:
    """Fail closed on missing, stale, duplicate, empty, or misclassified entries."""
    errors: list[str] = []
    counts: dict[str, int] = {}
    for entry in entries:
        path = entry["path"]
        mode = entry["mode"]
        counts[path] = counts.get(path, 0) + 1
        if mode not in VALID_MODES:
            errors.append(f"{path}: unsupported execution mode {mode!r}")
        if not entry["purpose"].strip():
            errors.append(f"{path}: purpose must not be empty")
        candidate = root / path
        if not candidate.is_file():
            errors.append(f"{path}: inventory entry does not exist")
            continue
        if not path.startswith("tests/"):
            errors.append(f"{path}: validation entrypoints must live under tests/")
            continue
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8"), filename=path)
        except (OSError, SyntaxError) as error:
            errors.append(f"{path}: cannot parse validation entrypoint: {error}")
            continue
        if mode == PYTEST_MODE and not _has_test_function(tree):
            errors.append(f"{path}: pytest entry has no test_ function")
        if mode in STANDALONE_MODES | {WORKFLOW_MODE} and not _has_main_guard(tree):
            errors.append(f"{path}: standalone/helper entry has no executable main guard")

    duplicates = sorted(path for path, count in counts.items() if count != 1)
    errors.extend(
        f"{path}: appears {counts[path]} times (expected exactly once)"
        for path in duplicates
    )
    expected = expected_entrypoints(root)
    actual = set(counts)
    errors.extend(f"{path}: missing from inventory" for path in sorted(expected - actual))
    errors.extend(f"{path}: stale inventory entry" for path in sorted(actual - expected))
    return errors


def render_documentation(entries: list[dict[str, str]]) -> str:
    """Render human-readable test-purpose documentation from the canonical JSON source."""
    rows = [
        "# Test inventory",
        "",
        "This inventory is the authoritative list of repository validation entrypoints. CI checks that every tests/test_*.py module appears exactly once, that its execution mode is valid, and that this page stays synchronized with tests/test_inventory.json.",
        "",
        "Pytest modules are collected explicitly from the inventory. Standalone acceptance scripts are invoked by dedicated CI steps and are not mistaken for empty pytest modules.",
        "",
        "## Pytest modules",
        "",
        "| Module | Purpose |",
        "| --- | --- |",
    ]
    for entry in sorted(entries, key=lambda value: value["path"]):
        if entry["mode"] == PYTEST_MODE:
            rows.append(f"| {entry['path']} | {entry['purpose']} |")
    rows.extend(
        [
            "",
            "## Standalone acceptance scripts",
            "",
            "| Entrypoint | Execution mode | Purpose |",
            "| --- | --- | --- |",
        ]
    )
    for entry in sorted(entries, key=lambda value: value["path"]):
        if entry["mode"] in STANDALONE_MODES:
            rows.append(
                f"| {entry['path']} | {entry['mode']} | {entry['purpose']} |"
            )
    rows.extend(
        ["", "## Workflow helpers", "", "| Entrypoint | Purpose |", "| --- | --- |"]
    )
    for entry in sorted(entries, key=lambda value: value["path"]):
        if entry["mode"] == WORKFLOW_MODE:
            rows.append(f"| {entry['path']} | {entry['purpose']} |")
    rows.extend(
        [
            "",
            "## How to update",
            "",
            "Add a purpose statement and execution mode to tests/test_inventory.json whenever a test module or validation entrypoint is added, removed, or renamed. Regenerate this page with python scripts/check_test_inventory.py --write-docs, then run python scripts/check_test_inventory.py. Do not add a second list of pytest paths to the workflow; CI reads them from this inventory.",
            "",
        ]
    )
    return "\n".join(rows)


def main() -> int:
    """Validate the inventory or emit the explicit pytest file list."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list-pytest", action="store_true")
    group.add_argument("--write-docs", action="store_true")
    args = parser.parse_args()
    try:
        entries = load_inventory()
        errors = validate_inventory(ROOT, entries)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"test-inventory=failed: {error}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"::error::{error}", file=sys.stderr)
        return 1
    if args.list_pytest:
        for entry in sorted(entries, key=lambda value: value["path"]):
            if entry["mode"] == PYTEST_MODE:
                print(entry["path"])
        return 0
    rendered = render_documentation(entries)
    if args.write_docs:
        DOCUMENTATION_PATH.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"test-inventory=written entries={len(entries)}")
        return 0
    if not DOCUMENTATION_PATH.is_file():
        print(f"::error::Missing generated inventory document: {DOCUMENTATION_PATH}", file=sys.stderr)
        return 1
    existing = DOCUMENTATION_PATH.read_text(encoding="utf-8")
    if existing != rendered:
        print(
            "::error::docs/test-inventory.md is stale; run "
            "python scripts/check_test_inventory.py --write-docs",
            file=sys.stderr,
        )
        return 1
    pytest_count = sum(entry["mode"] == PYTEST_MODE for entry in entries)
    standalone_count = sum(entry["mode"] in STANDALONE_MODES for entry in entries)
    print(
        f"test-inventory=passed entries={len(entries)} "
        f"pytest={pytest_count} standalone={standalone_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
