#!/usr/bin/env python3
"""Generate and validate the authoritative test-purpose inventory."""

from __future__ import annotations

import argparse
import ast
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "tests"
INVENTORY_PATH = ROOT / "docs" / "testing" / "TEST_INVENTORY.md"

ACCEPTANCE_RUNNERS = {
    "acceptance_eeschema_gui.py": (
        "Starts the real Eeschema editor under a virtual display, creates a block, "
        "and verifies that the editor can save it.",
        ".github/workflows/ci.yml — Verify Eeschema GUI editor launch and save",
    ),
    "acceptance_instance_configuration_gui.py": (
        "Drives the real Tk instance-configuration dialog, applies a profile, and "
        "verifies that variant, typed parameter, and PCB-layout fields are committed "
        "in one IPC transaction.",
        "Called by acceptance_plugin_registration.py and capture_visual_docs.py",
    ),
    "acceptance_ipc_headless.py": (
        "Starts KiCad headlessly and verifies schematic IPC placement, facade "
        "configuration, and project-local implementation behavior.",
        ".github/workflows/ci.yml — Verify real KiCad IPC placement (headless)",
    ),
    "acceptance_library_picker_gui.py": (
        "Exercises the searchable native block picker with real Tk widgets and X11 "
        "keyboard events.",
        ".github/workflows/ci.yml — Verify native-rendered functional block picker",
    ),
    "acceptance_pcb_ipc.py": (
        "Probes the live KiCad PCB IPC primitives used by block-layout application.",
        ".github/workflows/ci.yml — Verify PCB IPC primitives (headless)",
    ),
    "acceptance_pcb_layout.py": (
        "Merges stored PCB block layouts into a separate project-board copy and "
        "validates the result through KiCad.",
        ".github/workflows/ci.yml — Verify PCB block layout merge (headless)",
    ),
    "acceptance_plugin_registration.py": (
        "Installs the plugin into a temporary KiCad user environment, starts KiCad, "
        "and verifies action discovery and managed-interpreter integration.",
        ".github/workflows/ci.yml — Verify KiCad plugin discovery and managed environment",
    ),
    "capture_visual_docs.py": (
        "Captures reproducible screenshots and rendered design previews from the real "
        "KiCad UI for documentation and visual review.",
        ".github/workflows/ci.yml — Generate real KiCad visual documentation previews",
    ),
    "validate_kicad_cli.py": (
        "Generates representative block files and asks KiCad CLI to parse and "
        "validate the generated schematic, symbol-library, and PCB assets.",
        ".github/workflows/ci.yml — Validate generated files with KiCad",
    ),
}


@dataclass(frozen=True)
class TestCase:
    """One discovered pytest test function or unittest test method."""

    path: str
    class_name: str
    name: str
    line: int
    purpose: str


def _purpose(name: str, docstring: str | None) -> str:
    """Return a concise purpose from the test docstring or its descriptive name."""
    purpose = (
        docstring.strip().splitlines()[0].strip()
        if docstring and docstring.strip()
        else f"Verifies the behavior: {name.removeprefix('test_').replace('_', ' ')}."
    )
    return purpose.replace("|", "\\|").replace("\n", " ")


def _test_methods(module: ast.Module, relative_path: str) -> list[TestCase]:
    """Collect test functions and class methods and reject Python method shadowing."""
    cases: list[TestCase] = []
    seen: set[tuple[str, str]] = set()
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            key = ("", node.name)
            if key in seen:
                raise ValueError(f"{relative_path}:{node.lineno}: duplicate test {node.name!r}")
            seen.add(key)
            cases.append(TestCase(
                relative_path, "", node.name, node.lineno,
                _purpose(node.name, ast.get_docstring(node)),
            ))
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not child.name.startswith("test_"):
                    continue
                key = (node.name, child.name)
                if key in seen:
                    raise ValueError(
                        f"{relative_path}:{child.lineno}: duplicate test "
                        f"{node.name}.{child.name}; Python would shadow the earlier method"
                    )
                seen.add(key)
                cases.append(TestCase(
                    relative_path, node.name, child.name, child.lineno,
                    _purpose(child.name, ast.get_docstring(child)),
                ))
    return cases


def discover_tests(test_root: Path = TEST_ROOT) -> list[TestCase]:
    """Discover every pytest-style module and reject empty or malformed test modules."""
    modules = sorted(test_root.glob("test_*.py"))
    if not modules:
        raise ValueError(f"No pytest test modules found under {test_root}.")
    cases: list[TestCase] = []
    for path in modules:
        relative = path.relative_to(ROOT).as_posix()
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError) as error:
            raise ValueError(f"Cannot parse test module {relative}: {error}") from error
        module_cases = _test_methods(module, relative)
        if not module_cases:
            raise ValueError(
                f"{relative} matches pytest's test-file pattern but contains no test cases. "
                "Standalone KiCad/GUI runners must use the acceptance_ prefix."
            )
        cases.extend(module_cases)
    return cases


def _validate_runner_inventory(test_root: Path) -> None:
    """Require the runner catalogue to match the standalone acceptance scripts."""
    expected = set(ACCEPTANCE_RUNNERS)
    actual = {path.name for path in test_root.glob("acceptance_*.py")}
    if actual != expected:
        raise ValueError(
            "Acceptance runner inventory mismatch; "
            f"missing={sorted(expected - actual)}, undocumented={sorted(actual - expected)}"
        )
    support = {"capture_visual_docs.py", "validate_kicad_cli.py"}
    for filename in expected | support:
        if not (test_root / filename).is_file():
            raise ValueError(f"Documented test runner does not exist: tests/{filename}")


def render_inventory(root: Path = ROOT) -> str:
    """Render every discovered test and every native acceptance runner."""
    test_root = root / "tests"
    cases = discover_tests(test_root)
    _validate_runner_inventory(test_root)
    modules = {case.path for case in cases}
    lines = [
        "# Test inventory", "",
        "This file is generated by `scripts/check_test_inventory.py`. "
        "Edit the test or its docstring, not this generated table.", "",
        f"**Pytest inventory:** {len(cases)} test cases in {len(modules)} modules.", "",
        "## Pytest-discovered tests", "", "| Test | Purpose |", "| --- | --- |",
    ]
    for case in cases:
        module_link = f"../../{case.path}#L{case.line}"
        label = f"{case.class_name + '.' if case.class_name else ''}{case.name}"
        lines.append(f"| [`{label}`]({module_link}) | {case.purpose} |")
    lines.extend([
        "", "## Native KiCad, GUI, and evidence runners", "",
        "These scripts require KiCad, a real IPC server, a GUI session, or rendered "
        "evidence. They use the `acceptance_*.py` prefix (or a descriptive "
        "non-test filename), so pytest does not mistake an executable acceptance "
        "runner for a test module that collects zero tests.", "",
        "| Runner | Purpose | CI invocation / dependency |",
        "| --- | --- | --- |",
    ])
    for filename, (purpose, invocation) in ACCEPTANCE_RUNNERS.items():
        lines.append(
            f"| [`tests/{filename}`](../../tests/{filename}) | {purpose} | {invocation} |"
        )
    lines.extend([
        "", "## Inventory rules", "",
        "- Every `tests/test_*.py` module must contain at least one discoverable "
        "`test_*` function or method.",
        "- A test method name must be unique within its class. Duplicate method "
        "definitions are rejected because Python silently shadows the earlier one.",
        "- Every standalone `tests/acceptance_*.py` runner is listed above.",
        "- The CI `--check` mode compares this file with the current source tree and "
        "fails when a test, purpose, source line, or runner entry is missing or stale.", "",
    ])
    return "\n".join(lines)


def main() -> int:
    """Write the inventory or fail when the checked-in copy is stale."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Check the generated inventory without modifying the working tree.",
    )
    arguments = parser.parse_args()
    try:
        expected = render_inventory()
    except ValueError as error:
        print(f"test-inventory=failed reason={error}", file=sys.stderr)
        return 1
    if arguments.check:
        try:
            actual = INVENTORY_PATH.read_text(encoding="utf-8")
        except OSError:
            actual = ""
        if actual != expected:
            diff = difflib.unified_diff(
                actual.splitlines(), expected.splitlines(),
                fromfile=str(INVENTORY_PATH), tofile="generated inventory", lineterm="",
            )
            print("\n".join(diff), file=sys.stderr)
            print("test-inventory=stale; regenerate with scripts/check_test_inventory.py")
            return 1
        print("test-inventory=passed")
        return 0
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_text(expected, encoding="utf-8")
    print(f"test-inventory=written cases={len(discover_tests())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
