#!/usr/bin/env python3
"""Generate a source-linked inventory of every automated test and its purpose."""

from __future__ import annotations

import argparse
import ast
import difflib
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "docs" / "test-inventory.md"

RUNNER_PURPOSES: dict[str, tuple[str, str]] = {
    "test_eeschema_gui.py": (
        "Exercises the real Eeschema edit, save, publish, and local-copy sync journey.",
        "Run by the KiCad CI job under Xvfb.",
    ),
    "test_instance_configuration_gui.py": (
        "Exercises the instance-configuration GUI against a real KiCad schematic.",
        "Invoked by visual-document capture.",
    ),
    "test_ipc_headless.py": (
        "Exercises real schematic IPC placement, configuration, composition, and synchronization.",
        "Run by the KiCad CI job without a display.",
    ),
    "test_library_picker_gui.py": (
        "Exercises the native block picker search, selection, and placement interaction.",
        "Run by the KiCad CI job under Xvfb.",
    ),
    "test_pcb_ipc.py": (
        "Exercises native PCB IPC primitives and verifies resulting board objects.",
        "Run by the KiCad CI job without a display.",
    ),
    "test_pcb_layout.py": (
        "Exercises end-to-end PCB layout import, merge, and export with KiCad.",
        "Run by the KiCad CI job without a display.",
    ),
    "test_plugin_registration.py": (
        "Installs and discovers the plugin in KiCad's managed environment and exercises its actions.",
        "Run by the KiCad CI job under Xvfb.",
    ),
}

SUPPORT_PURPOSES: dict[str, str] = {
    "capture_visual_docs.py": (
        "Captures real KiCad GUI screenshots used as versioned visual documentation evidence."
    ),
    "validate_kicad_cli.py": (
        "Opens and exports generated KiCad assets with the KiCad CLI to validate file integrity."
    ),
}


@dataclass(frozen=True)
class TestCase:
    """One AST-discovered pytest or unittest test case."""

    path: str
    class_name: str
    name: str
    line: int
    purpose: str


class _TestCollector(ast.NodeVisitor):
    """Collect test functions while retaining their containing test class."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.class_names: list[str] = []
        self.test_cases: list[TestCase] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_names.append(node.name)
        self.generic_visit(node)
        self.class_names.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record(node)
        self.generic_visit(node)

    def _record(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not node.name.startswith("test_"):
            return
        self.test_cases.append(
            TestCase(
                path=self.path,
                class_name=".".join(self.class_names),
                name=node.name,
                line=node.lineno,
                purpose=describe_test(node.name, ast.get_docstring(node)),
            )
        )


def describe_test(name: str, docstring: str | None = None) -> str:
    """Return an explicit test docstring or a readable purpose derived from its name."""
    if docstring and docstring.strip():
        purpose = docstring.strip().splitlines()[0].strip()
    else:
        purpose = f"Checks: {name.removeprefix('test_').replace('_', ' ')}."
    if purpose[-1:] not in {".", "!", "?"}:
        purpose += "."
    return purpose


def collect_test_cases_from_source(path: str, source: str) -> list[TestCase]:
    """Parse one source file and return its test functions in source order."""
    collector = _TestCollector(path)
    collector.visit(ast.parse(source, filename=path))
    return collector.test_cases


def collect_test_cases(tests_dir: Path, root: Path) -> list[TestCase]:
    """Discover every test function under the repository test directory."""
    cases: list[TestCase] = []
    for path in sorted(tests_dir.glob("test_*.py")):
        relative_path = path.relative_to(root).as_posix()
        cases.extend(
            collect_test_cases_from_source(
                relative_path, path.read_text(encoding="utf-8")
            )
        )
    return cases


def render_inventory(root: Path = ROOT) -> str:
    """Render the complete deterministic test inventory."""
    tests_dir = root / "tests"
    grouped: dict[str, list[TestCase]] = defaultdict(list)
    for case in collect_test_cases(tests_dir, root):
        grouped[case.path].append(case)

    test_modules = sorted(tests_dir.glob("test_*.py"))
    runner_names = {path.name for path in test_modules if path.name in RUNNER_PURPOSES}
    unclassified = [
        path.name
        for path in test_modules
        if path.name not in RUNNER_PURPOSES
        and not grouped.get(path.relative_to(root).as_posix())
    ]
    missing_runners = sorted(set(RUNNER_PURPOSES) - runner_names)
    if unclassified or missing_runners:
        details = []
        if unclassified:
            details.append(f"unclassified test modules: {', '.join(unclassified)}")
        if missing_runners:
            details.append(f"missing configured runners: {', '.join(missing_runners)}")
        raise ValueError("; ".join(details))

    case_count = sum(len(items) for items in grouped.values())
    lines = [
        "# Test inventory",
        "",
        "Generated by scripts/test_inventory.py. Unit-test rows link to their source files.",
        "CI checks this file against the current Python AST, so renamed, added, or removed",
        "test functions cannot silently leave the inventory stale.",
        "",
        f"- **Unit test cases:** {case_count}",
        f"- **Unit-test modules:** {len(grouped)}",
        f"- **Direct KiCad integration runners:** {len(RUNNER_PURPOSES)}",
        f"- **Test-support utilities:** {len(SUPPORT_PURPOSES)}",
        "",
        "## Unit tests",
        "",
    ]

    for path in sorted(grouped):
        cases = grouped[path]
        lines.extend(
            [
                f"### {path} ({len(cases)} tests)",
                "",
                "| Test case | Source | Purpose |",
                "|---|---|---|",
            ]
        )
        for case in cases:
            purpose = case.purpose.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {case.name} | [source](../{path}) | {purpose} |")
        lines.append("")

    lines.extend(
        [
            "## Direct integration runners",
            "",
            "These executable acceptance tests are invoked directly rather than collected",
            "as pytest or unittest cases. They exercise real KiCad processes, IPC, GUI",
            "interactions, or persisted project files.",
            "",
            "| Runner | Purpose | Execution |",
            "|---|---|---|",
        ]
    )
    for name, (purpose, execution) in sorted(RUNNER_PURPOSES.items()):
        path = f"tests/{name}"
        lines.append(f"| [{path}](../{path}) | {purpose} | {execution} |")
    lines.append("")

    lines.extend(
        [
            "## Test-support utilities",
            "",
            "| Utility | Purpose |",
            "|---|---|",
        ]
    )
    for name, purpose in sorted(SUPPORT_PURPOSES.items()):
        path = f"tests/{name}"
        lines.append(f"| [{path}](../{path}) | {purpose} |")
    lines.extend(
        [
            "",
            "## Maintenance",
            "",
            "After adding, removing, or renaming test functions or integration runners,",
            "regenerate this document with: python scripts/test_inventory.py --write.",
            "The CI check is read-only and fails when the committed inventory is stale.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Write the generated inventory or verify that the committed file is current."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the generated inventory")
    mode.add_argument("--check", action="store_true", help="fail when the inventory is stale")
    args = parser.parse_args()
    try:
        generated = render_inventory()
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"Test inventory error: {exc}", file=sys.stderr)
        return 1

    if args.write:
        INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        INVENTORY_PATH.write_text(generated, encoding="utf-8")
        print(f"Wrote {INVENTORY_PATH.relative_to(ROOT)}")
        return 0

    if not INVENTORY_PATH.is_file():
        print("Test inventory is missing; run scripts/test_inventory.py --write.", file=sys.stderr)
        return 1
    committed = INVENTORY_PATH.read_text(encoding="utf-8")
    if committed != generated:
        print("Test inventory is stale; run scripts/test_inventory.py --write.", file=sys.stderr)
        diff = difflib.unified_diff(
            committed.splitlines(),
            generated.splitlines(),
            fromfile="docs/test-inventory.md",
            tofile="generated test inventory",
            lineterm="",
        )
        print("\n".join(list(diff)[:80]), file=sys.stderr)
        return 1
    print("Test inventory is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
