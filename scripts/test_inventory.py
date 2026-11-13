#!/usr/bin/env python3
"""Authoritative routing and collection contract for KiCad Blocks tests."""

from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_DOC = ROOT / "docs" / "developers" / "TEST_INVENTORY.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

PYTEST_GROUPS: dict[str, dict[str, str]] = {
    "Domain and KiCad behavior": {
        "tests/test_blocks.py": "Block creation, catalog registration, nested composition, local copies, publishing, synchronization and conflict handling.",
        "tests/test_editor_workflow.py": "Editor commands, selection rules, instance configuration, transaction rollback and implementation-opening behavior.",
        "tests/test_extraction.py": "Hierarchical-sheet selection, facade pin extraction, publication and nested sheet-tree preservation.",
        "tests/test_library_browser.py": "Library search and preview, selection stability, variant/profile editing, window behavior and native symbol-placement handoff.",
        "tests/test_library_validation.py": "Machine-readable library reports, missing/corrupt assets, digest and revision mismatches, and recursive ERC.",
        "tests/test_operations.py": "Recursive BOM expansion and ERC execution, including nested instance references and violation reporting.",
        "tests/test_pcb_layout_unit.py": "Pure PCB-layout parsing, named-layout resolution, coordinate transforms, net mappings and netclass override behavior.",
        "tests/test_pcb_placement.py": "PCB merge-manifest integrity and native move behavior for single and batched imported layouts.",
        "tests/test_profiles.py": "Profile validation, typed overrides, linked variant/layout resolution, and local-copy synchronization.",
        "tests/test_stusb4500_reference.py": "Reference circuit structure, hierarchical ports, electrical pin shapes, layout zoning and grid alignment.",
        "tests/test_variants.py": "Implementation variant selection, fail-closed handling of unknown values, and KiCad text-variable expansion.",
    },
    "Packaging, release and CI tooling": {
        "tests/test_dispatch_release.py": "Release-workflow dispatch conditions and tag/source-commit mismatch rejection.",
        "tests/test_download_pinned_ipc_wheel.py": "Pinned IPC wheel lookup, ABI selection, source-SHA validation, digest checks and corrupt/missing asset handling.",
        "tests/test_install.py": "Plugin installation and reinstall safety, supported Python/KiCad versions, platform constraints and plugin identifier validation.",
        "tests/test_publish_ipc_wheel.py": "IPC wheel upload retry policy, permanent HTTP error handling and recovery when an upload succeeds but its response is lost.",
        "tests/test_publish_pinned_ipc_wheel.py": "Immutable pinned-wheel publication, digest verification, idempotency and refusal to overwrite different bytes.",
        "tests/test_publish_release.py": "Release asset upload integrity and refusal to replace an asset with a different digest.",
        "tests/test_release_package.py": "Deterministic release archive contents, extraction/install behavior, user-data paths and dependency URL validation.",
        "tests/test_test_inventory.py": "The test-routing validator, module selection and AST-based test discovery.",
        "tests/test_verify_release_gate.py": "Exact-commit main-CI release gating, tag/version agreement and rejection of failed or unrelated workflow runs.",
        "tests/test_visual_docs.py": "Visual-documentation asset inventory, source-commit provenance, SHA-256 integrity and stale/incomplete bundle rejection.",
    },
}

STANDALONE_TESTS: dict[str, str] = {
    "tests/test_eeschema_gui.py": "Launches real Eeschema, exercises the block implementation save/edit workflow and verifies saved project state.",
    "tests/test_instance_configuration_gui.py": "Drives the real Tk instance-configuration dialog and verifies a profile saves its parameter, implementation variant and PCB layout in one schematic IPC transaction.",
    "tests/test_ipc_headless.py": "Exercises real headless schematic IPC placement, block registration, instance metadata, local-copy creation and synchronization.",
    "tests/test_library_picker_gui.py": "Uses real Tk widgets and X11 keyboard events to verify searchable block-picker selection and native placement handoff.",
    "tests/test_pcb_ipc.py": "Probes real KiCad PCB IPC primitives against a temporary native board.",
    "tests/test_pcb_layout.py": "Runs end-to-end stored PCB block-layout application against KiCad and checks generated board output and failure behavior.",
    "tests/test_plugin_registration.py": "Verifies KiCad discovers the installed schematic IPC plugin action and that managed installation works on supported Python ABIs.",
}

AUXILIARY_CI_CHECKS: dict[str, str] = {
    "tests/validate_kicad_cli.py": "Validates generated KiCad schematic and symbol files with the native KiCad CLI.",
    "tests/capture_visual_docs.py": "Captures production UI states and rendered KiCad outputs, then validates the visual evidence bundle before publication.",
    "python -m compileall -q plugin tests scripts examples": "Compiles project Python sources to catch syntax errors.",
    "ruff check plugin tests scripts examples": "Runs Ruff lint over production, test, script and example code.",
}


def pytest_module_purposes() -> dict[str, str]:
    """Return the pytest module inventory flattened in stable group order."""
    return {
        path: purpose
        for group in PYTEST_GROUPS.values()
        for path, purpose in group.items()
    }


def collect_test_case_names(
    source: str, *, filename: str = "<test-source>"
) -> tuple[str, ...]:
    """Return module-level and test-class test function names from Python source."""
    tree = ast.parse(source, filename=filename)
    names: list[str] = []
    for node in tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef):
            names.extend(
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name.startswith("test_")
            )
    return tuple(names)


def _has_main_guard(tree: ast.Module) -> bool:
    """Return whether a script has an if __name__ == '__main__' entry point."""
    for node in tree.body:
        test = node.test if isinstance(node, ast.If) else None
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        ):
            return True
    return False


def validate_routing(root: Path = ROOT) -> tuple[str, ...]:
    """Check routing, pytest test presence, executable entry points and CI invocation."""
    root = root.resolve()
    pytest_routes = pytest_module_purposes()
    routes = (*pytest_routes, *STANDALONE_TESTS)
    counts = Counter(routes)
    errors = [
        f"{path} is routed {count} times."
        for path, count in counts.items()
        if count != 1
    ]

    tests_dir = root / "tests"
    discovered = (
        {path.relative_to(root).as_posix() for path in tests_dir.glob("test_*.py")}
        if tests_dir.is_dir()
        else set()
    )
    configured = set(routes)
    errors.extend(
        f"{path} is not routed in the test inventory."
        for path in sorted(discovered - configured)
    )
    errors.extend(
        f"{path} is listed but does not exist."
        for path in sorted(configured - discovered)
    )

    workflow = (
        CI_WORKFLOW if root == ROOT else root / ".github" / "workflows" / "ci.yml"
    )
    workflow_text = workflow.read_text(encoding="utf-8") if workflow.is_file() else ""
    if not workflow_text:
        errors.append("The required .github/workflows/ci.yml file is missing.")

    for relative_path in sorted(pytest_routes):
        path = root / relative_path
        if not path.is_file():
            continue
        try:
            cases = collect_test_case_names(
                path.read_text(encoding="utf-8"), filename=relative_path
            )
        except (OSError, SyntaxError) as exc:
            errors.append(f"{relative_path} cannot be parsed: {exc}")
            continue
        if not cases:
            errors.append(
                f"{relative_path} is routed to pytest but defines no test cases."
            )

    for relative_path in sorted(STANDALONE_TESTS):
        path = root / relative_path
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        except (OSError, SyntaxError) as exc:
            errors.append(f"{relative_path} cannot be parsed: {exc}")
            continue
        has_main = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "main"
            for node in tree.body
        )
        if not has_main or not _has_main_guard(tree):
            errors.append(
                f"{relative_path} must define main() and a __main__ entry point."
            )
        if relative_path not in workflow_text:
            errors.append(
                f"{relative_path} is not invoked by .github/workflows/ci.yml."
            )
    return tuple(errors)


def expected_modules_for_args(
    args: Sequence[str], root: Path = ROOT
) -> frozenset[Path]:
    """Resolve which inventoried pytest modules an invocation promises to collect."""
    root = root.resolve()
    module_paths = {(root / path).resolve() for path in pytest_module_purposes()}
    tests_dir = (root / "tests").resolve()
    selected: set[Path] = set()
    has_path_argument = False
    for argument in args:
        raw = str(argument).split("::", 1)[0]
        if raw.startswith("-"):
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        if not candidate.exists():
            continue
        candidate = candidate.resolve()
        if candidate == tests_dir:
            has_path_argument = True
            selected.update(module_paths)
        elif candidate in module_paths:
            has_path_argument = True
            selected.add(candidate)
        elif candidate.is_dir() and candidate.is_relative_to(tests_dir):
            has_path_argument = True
            selected.update(path for path in module_paths if candidate in path.parents)
    return frozenset(selected if has_path_argument else module_paths)


def render_inventory(root: Path = ROOT) -> str:
    """Render the human-readable inventory from the authoritative test routing."""
    root = root.resolve()
    lines = [
        "# Test inventory",
        "",
        "<!-- Generated by scripts/test_inventory.py; edit its routing data, not this table. -->",
        "",
        "The routing in scripts/test_inventory.py is authoritative. Every tests/test_*.py "
        "module must appear exactly once. CI verifies that pytest modules contain collectable "
        "tests and that standalone scripts are invoked by the workflow.",
        "",
        "## Pytest modules",
        "",
        "| Group | Module | Test cases | Purpose |",
        "| --- | --- | ---: | --- |",
    ]
    total = 0
    routes = pytest_module_purposes()
    for group, modules in PYTEST_GROUPS.items():
        for relative_path, purpose in modules.items():
            source = (root / relative_path).read_text(encoding="utf-8")
            count = len(collect_test_case_names(source, filename=relative_path))
            total += count
            lines.append(
                f"| {group} | [{relative_path}](../../{relative_path}) | {count} | {purpose} |"
            )
    lines.extend(
        [
            "",
            f"**Pytest total:** {len(routes)} modules, {total} test cases.",
            "",
            "## Standalone KiCad integration tests",
            "",
            "These tests require a live KiCad process, KiCad IPC, Tk/X11 or the native KiCad CLI. "
            "They are executed as scripts rather than collected by pytest.",
            "",
            "| Script | Purpose |",
            "| --- | --- |",
        ]
    )
    for relative_path, purpose in STANDALONE_TESTS.items():
        lines.append(f"| [{relative_path}](../../{relative_path}) | {purpose} |")
    lines.extend(
        [
            "",
            "## Supporting CI validations",
            "",
            "| Check | Purpose |",
            "| --- | --- |",
        ]
    )
    for check, purpose in AUXILIARY_CI_CHECKS.items():
        if check.startswith("tests/"):
            lines.append(f"| [{check}](../../{check}) | {purpose} |")
        else:
            lines.append(f"| {check} | {purpose} |")
    lines.extend(
        [
            "",
            "## Collection and maintenance rules",
            "",
            "- Add every tests/test_*.py file to exactly one pytest group or to STANDALONE_TESTS; "
            "an unlisted or multiply listed module fails CI.",
            "- Pytest-routed modules must define at least one collectable test function or "
            "test-class method.",
            "- Standalone tests must define main(), use a __main__ guard and be invoked explicitly "
            "by CI.",
            "- The pytest collection hook verifies that every module selected by an invocation "
            "produces at least one collected item.",
            "- Regenerate this file after inventory changes with python scripts/test_inventory.py "
            "--write-docs.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the inventory contract and optionally regenerate its documentation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-docs", action="store_true", help="regenerate the inventory Markdown"
    )
    parser.add_argument(
        "--check-docs",
        action="store_true",
        help="fail if the inventory Markdown is stale",
    )
    args = parser.parse_args(argv)
    errors = validate_routing()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    rendered = render_inventory()
    if args.write_docs:
        INVENTORY_DOC.parent.mkdir(parents=True, exist_ok=True)
        INVENTORY_DOC.write_text(rendered, encoding="utf-8")
    else:
        if (
            not INVENTORY_DOC.is_file()
            or INVENTORY_DOC.read_text(encoding="utf-8") != rendered
        ):
            print(
                "ERROR: Test inventory Markdown is missing or stale; run with --write-docs.",
                file=sys.stderr,
            )
            return 1
    case_count = sum(
        len(
            collect_test_case_names(
                (ROOT / path).read_text(encoding="utf-8"), filename=path
            )
        )
        for path in pytest_module_purposes()
    )
    print(
        f"test-inventory=passed pytest_modules={len(pytest_module_purposes())} "
        f"pytest_cases={case_count} standalone_scripts={len(STANDALONE_TESTS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
