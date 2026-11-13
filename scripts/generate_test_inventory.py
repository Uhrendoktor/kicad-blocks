#!/usr/bin/env python3
"""Generate a complete, drift-checked inventory of every automated test."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "testing-inventory.md"
TEST_ROOT = ROOT / "tests"

MODULE_PURPOSES = {
    "test_blocks.py": "Pin and parameter parsing, facade generation, stable catalog identity, library storage, independent local copies, synchronization, conflict detection, and nested dependency invariants.",
    "test_dispatch_release.py": "Release dispatch idempotency and rejection of mismatched tags or source revisions.",
    "test_download_pinned_ipc_wheel.py": "Pinned wheel lookup, ABI selection, archive integrity, digest validation, and source-revision verification.",
    "test_editor_workflow.py": "Editor action semantics, native KiCad API usage, configuration transactions, rollback, and GUI workflow behavior using controlled IPC doubles.",
    "test_entrypoints.py": "Shared IPC entrypoint bootstrap path management, action dispatch delegation, and manifest entrypoint consistency.",
    "test_extraction.py": "Hierarchical-sheet selection, facade-pin derivation, publication conflict handling, and recursive sheet extraction.",
    "test_install.py": "Installer runtime/platform gating, safe replacement, dry-run behavior, manifest validity, and dependency compatibility.",
    "test_library_browser.py": "Library search and preview, layout/variant editing, profile safety, selection behavior, scroll handling, and native placement handoff.",
    "test_library_validation.py": "Machine-readable library validation, checksum/revision mismatches, corrupt or missing assets, recursive ERC, and empty-library behavior.",
    "test_operations.py": "Recursive BOM expansion, parameter substitution, reference namespacing, nested-instance handling, and recursive ERC invocation.",
    "test_pcb_layout_unit.py": "Pure PCB S-expression transformations, net mapping, rotation geometry, netclass rules, and invalid-net rejection.",
    "test_pcb_placement.py": "Persistent placement-manifest integrity, instance grouping, board identity, and refusal of partial or ambiguous moves.",
    "test_profiles.py": "Configuration-profile normalization, parameter/variant/layout resolution, local-copy synchronization, and divergent-change protection.",
    "test_python_policy.py": "Regression coverage for global typing policy violations, diagnostic suppression/dynamic execution bans, production module docstrings, and Pyright inventory enforcement.",
    "test_publish_ipc_wheel.py": "Transient versus permanent GitHub API failures and recovery when a wheel upload succeeded but its response was lost.",
    "test_publish_pinned_ipc_wheel.py": "Immutable revision-addressed wheel publication, digest idempotency, no-overwrite guarantees, and ABI-specific tagging.",
    "test_publish_release.py": "Versioned release asset upload, checksum verification, and immutable existing-asset behavior.",
    "test_release_package.py": "Deterministic release archive contents, installation from an extracted archive, and dependency URL consistency.",
    "test_stusb4500_reference.py": "Generated STUSB4500 reference design structure, electrical pin shapes, grid alignment, and functional PCB zoning.",
    "test_variants.py": "Exact variant resolution, invalid/default behavior, text-variable expansion, and variant synchronization.",
    "test_verify_release_gate.py": "Release gate enforcement for successful main CI on the exact source SHA and correct version/tag metadata.",
    "test_visual_docs.py": "Visual documentation inventory completeness, source-commit freshness, asset hashes, and stale or incomplete bundle rejection.",
}

SCRIPTED_ACCEPTANCE = [
    (
        "test_eeschema_gui.py",
        "xvfb-run -a python tests/test_eeschema_gui.py",
        "Launches the real Eeschema editor, edits/publishes an implementation, synchronizes an existing local instance, saves, and verifies KiCad can re-parse the result.",
    ),
    (
        "test_instance_configuration_gui.py",
        "python tests/test_instance_configuration_gui.py [--screenshot PATH]",
        "Drives the actual Tk instance-configuration form and checks saved schematic fields and the IPC transaction; also used to capture a documentation screenshot.",
    ),
    (
        "test_ipc_headless.py",
        "python tests/test_ipc_headless.py",
        "Exercises real schematic IPC placement, instance configuration, facade identity, local-copy binding, nested composition, and saved-project invariants without an interactive window.",
    ),
    (
        "test_library_picker_gui.py",
        "xvfb-run -a python tests/test_library_picker_gui.py",
        "Opens the real library picker, inspects native preview rendering and selection behavior, and validates the user-facing placement handoff.",
    ),
    (
        "test_pcb_ipc.py",
        "python tests/test_pcb_ipc.py",
        "Uses real PCB IPC to create a board, assign/import nets and footprints, save, and reload native board objects.",
    ),
    (
        "test_pcb_layout.py",
        "python tests/test_pcb_layout.py",
        "Runs end-to-end KiCad CLI/IPC board-layout merges and checks transformed footprints, tracks, vias, zones, graphics, namespaced nets/references, netclass mappings, and output safety.",
    ),
    (
        "test_plugin_registration.py",
        "xvfb-run -a python tests/test_plugin_registration.py",
        "Starts KiCad, verifies plugin discovery and action registration, managed Python environment setup, and runs the configuration-form probe.",
    ),
]

SUPPORT_SCRIPTS = [
    (
        "validate_kicad_cli.py",
        "python tests/validate_kicad_cli.py",
        "Exports and validates generated schematic/PCB reference assets with the real KiCad CLI and checks structural/netlist expectations.",
    ),
    (
        "capture_visual_docs.py",
        "xvfb-run -a python tests/capture_visual_docs.py --output-dir OUT",
        "Captures real light/dark KiCad plugin UI states, previews, modal workflows, and GIF/source evidence used by documentation and release publication.",
    ),
]

ACRONYMS = {
    "kicad": "KiCad",
    "kiCad": "KiCad",
    "ipc": "IPC",
    "pcb": "PCB",
    "erc": "ERC",
    "bom": "BOM",
    "sha": "SHA",
}


def _purpose(name: str) -> str:
    """Describe the behavior identified by one test's explicit name."""
    words = name.removeprefix("test_").split("_")
    description = " ".join(ACRONYMS.get(word, word) for word in words)
    return description + "."


def _cases(path: Path) -> list[tuple[int, str, str]]:
    """Collect only pytest-discoverable functions and methods from one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            found.append((node.lineno, node.name, _purpose(node.name)))
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and member.name.startswith("test_"):
                    found.append((member.lineno, member.name, _purpose(member.name)))
    return sorted(found)


def render() -> str:
    """Render the full pytest and host-driven acceptance inventory."""
    discovered = sorted(TEST_ROOT.rglob("test_*.py"))
    runner_names = {filename for filename, _command, _purpose in SCRIPTED_ACCEPTANCE}
    unclassified: list[str] = []
    for path in discovered:
        cases = _cases(path)
        if cases and path.name not in MODULE_PURPOSES:
            unclassified.append(f"{path.relative_to(ROOT).as_posix()}: missing module purpose")
        elif not cases and (path.parent != TEST_ROOT or path.name not in runner_names):
            unclassified.append(
                f"{path.relative_to(ROOT).as_posix()}: neither pytest tests nor a declared runner"
            )
        elif not cases and not (TEST_ROOT / path.name).is_file():
            unclassified.append(
                f"{path.relative_to(ROOT).as_posix()}: declared runner must exist at tests/{path.name}"
            )
    for filename in sorted(runner_names):
        if not (TEST_ROOT / filename).is_file():
            unclassified.append(f"tests/{filename}: declared runner does not exist")
    for filename, _command, _purpose in SUPPORT_SCRIPTS:
        if not (TEST_ROOT / filename).is_file():
            unclassified.append(f"tests/{filename}: declared support script does not exist")
    if unclassified:
        raise ValueError("Incomplete test inventory:\n" + "\n".join(unclassified))

    lines = [
        "# Test inventory",
        "",
        "<!-- Generated by scripts/generate_test_inventory.py. Edit test code or the module purpose map, then regenerate. -->",
        "",
        "This inventory is generated from the repository's test source, not maintained as a second list of test names. Every pytest-discoverable test function/method is listed individually. The host-driven scripts are listed separately because they launch KiCad/GUI/IPC processes and are intentionally executed by dedicated CI steps rather than collected by pytest.",
        "",
        "## Pytest test cases",
        "",
    ]
    total = 0
    modules = 0
    for path in discovered:
        cases = _cases(path)
        if not cases:
            continue
        modules += 1
        module_purpose = MODULE_PURPOSES.get(path.name, "Purpose is described by each case below.")
        lines.extend(
            [
                f"### `{path.relative_to(ROOT).as_posix()}`",
                "",
                module_purpose,
                "",
                "| Test | Purpose |",
                "| --- | --- |",
            ]
        )
        for _line, name, purpose in cases:
            escaped_purpose = purpose.replace("|", "\\|")
            lines.append(f"| `{name}` | {escaped_purpose} |")
            total += 1
        lines.append("")
    lines.extend(
        [
            f"**Pytest inventory total: {total} test cases across {modules} modules.**",
            "",
            "## Host-driven KiCad acceptance scripts",
            "",
            "These files are named `test_*.py`, but expose executable `main()` acceptance harnesses rather than pytest test functions. CI invokes them explicitly with the KiCad runtime and (where required) a virtual display.",
            "",
            "| Script | CI/local invocation | Purpose |",
            "| --- | --- | --- |",
        ]
    )
    for filename, command, purpose in SCRIPTED_ACCEPTANCE:
        lines.append(f"| `tests/{filename}` | `{command}` | {purpose} |")
    lines.extend(
        [
            "",
            "## Supporting validation and evidence scripts",
            "",
            "| Script | CI/local invocation | Purpose |",
            "| --- | --- | --- |",
        ]
    )
    for filename, command, purpose in SUPPORT_SCRIPTS:
        lines.append(f"| `tests/{filename}` | `{command}` | {purpose} |")
    lines.extend(
        [
            "",
            "## Inventory maintenance",
            "",
            "Run `python scripts/generate_test_inventory.py --write` after adding, renaming, or removing a test. CI runs `python scripts/generate_test_inventory.py --check` and fails when the committed inventory is stale. Update `MODULE_PURPOSES` when a test module's scope changes materially.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Write the inventory or fail when committed documentation is stale."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write docs/testing-inventory.md")
    mode.add_argument(
        "--check", action="store_true", help="verify the committed inventory is current"
    )
    args = parser.parse_args()
    expected = render()
    if args.write:
        OUTPUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUTPUT.relative_to(ROOT)}")
        return 0
    if args.check:
        actual = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if actual != expected:
            print(
                f"{OUTPUT.relative_to(ROOT)} is stale; run: python scripts/generate_test_inventory.py --write"
            )
            return 1
        print("test-inventory=passed")
        return 0
    sys.stdout.write(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
