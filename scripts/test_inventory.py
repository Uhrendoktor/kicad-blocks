"""Generate and validate the complete inventory of pytest cases and acceptance runners."""

from __future__ import annotations

import argparse
import ast
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "docs" / "developers" / "TEST_INVENTORY.md"

STANDALONE_TESTS: dict[str, tuple[str, str]] = {
    "tests/test_eeschema_gui.py": (
        "Launches a real Eeschema window, creates a functional block, opens the stored "
        "implementation in the native editor, saves it, and verifies the editor workflow.",
        "Run in the virtual display with the configured KiCad executable.",
    ),
    "tests/test_instance_configuration_gui.py": (
        "Drives the production instance-configuration dialog with real Tk widgets; selects "
        "a profile and verifies the variant, typed parameter, layout, and single IPC commit.",
        "Run in the virtual display; also invoked by plugin registration and visual capture.",
    ),
    "tests/test_ipc_headless.py": (
        "Exercises the real KiCad schematic IPC server without a GUI, including placement, "
        "instance configuration, library registration, and local-copy actions.",
        "Run against the pinned kicad-python wheel and KiCad nightly IPC server.",
    ),
    "tests/test_library_picker_gui.py": (
        "Uses X11 keyboard events against the real searchable block picker and checks that "
        "the facade preview is rendered by KiCad rather than approximated.",
        "Run in the virtual display; also invoked inside KiCad's managed plugin environment.",
    ),
    "tests/test_pcb_ipc.py": (
        "Exercises native PCB IPC primitives and verifies the board-item operations used by "
        "the plugin against a live KiCad PCB Editor IPC server.",
        "Run with the pinned KiCad Python IPC binding and a live PCB Editor server.",
    ),
    "tests/test_pcb_layout.py": (
        "Runs the PCB block-layout import and merge acceptance path against real board files, "
        "checking the generated output with KiCad's own command-line parser.",
        "Run with KiCad CLI and the integration dependencies available.",
    ),
    "tests/test_plugin_registration.py": (
        "Installs the package into KiCad's managed plugin directory, launches Eeschema, "
        "verifies discovery and environment readiness, and exercises the installed task hub, "
        "picker, and instance-configuration entry points.",
        "Run in the virtual display with KiCad nightly; this is the managed-environment gate.",
    ),
}

MODULE_PURPOSES: dict[str, str] = {
    "tests/test_auto_dependencies.py": (
        "Automatic child-block dependency discovery from KiCad netlists and schematic "
        "connectivity, including reconciliation, identity preservation, and cycle rejection."
    ),
    "tests/test_blocks.py": (
        "Core block model, facade generation, parameter handling, library registration, "
        "creation, local copies, publishing, synchronization, and nested dependency behavior."
    ),
    "tests/test_dispatch_release.py": (
        "Release-workflow dispatch decisions, idempotency when assets already exist, and "
        "tag/source-commit mismatch rejection."
    ),
    "tests/test_download_pinned_ipc_wheel.py": (
        "Pinned KiCad IPC wheel reuse, release/ABI lookup, digest and archive validation, "
        "corrupt asset rejection, and source-SHA matching."
    ),
    "tests/test_editor_workflow.py": (
        "Schematic editor action routing, selected-sheet behavior, facade editing, transactional "
        "instance updates, parameter/layout validation, and safe opening of generated PCB copies."
    ),
    "tests/test_extraction.py": (
        "Hierarchical-sheet selection and extraction, interface pin/electrical-shape syncing, "
        "facade-artwork preservation, concurrent-edit rejection, and nested sheet copying."
    ),
    "tests/test_install.py": (
        "Installer Python/KiCad/platform compatibility, plugin metadata and dependency checks, "
        "safe replacement, and dry-run behavior."
    ),
    "tests/test_library_browser.py": (
        "Library search and selection, native previews, variants/profiles/layouts, cross-library "
        "lookup, theme and geometry behavior, scrolling, window restoration, and placement handoff."
    ),
    "tests/test_library_validation.py": (
        "Machine-readable library validation reports, asset hashes/revisions, corrupt or missing "
        "assets, empty-library behavior, and recursive ERC when KiCad CLI is available."
    ),
    "tests/test_operations.py": (
        "Recursive BOM expansion and reference namespacing, parameter-template rejection, and "
        "one-time-per-block recursive ERC with recorded violations."
    ),
    "tests/test_pcb_layout_unit.py": (
        "Deterministic PCB layout helpers: named/legacy layouts, net mapping, coordinate and angle "
        "transforms, netclass merging, bus-pattern matching, and invalid net-code rejection."
    ),
    "tests/test_pcb_placement.py": (
        "Placement sidecar schema, exclusive writes, item-group identity, active-board matching, "
        "duplicate/missing-ID rejection, and safe native move handoff."
    ),
    "tests/test_profiles.py": (
        "Profile parsing and resolution across variants, typed parameter overrides, named PCB "
        "layouts, local-copy synchronization, publishing, and conflict rejection."
    ),
    "tests/test_publish_ipc_wheel.py": (
        "IPC wheel release API retry behavior, permanent HTTP error handling, and recovery when "
        "the remote upload succeeded but its response was lost."
    ),
    "tests/test_publish_pinned_ipc_wheel.py": (
        "Immutable source-SHA-tagged wheel publication, idempotent asset reuse, digest verification, "
        "ABI selection, and refusal to overwrite different bytes."
    ),
    "tests/test_publish_release.py": (
        "Release archive and checksum upload, digest verification, and refusal to replace an "
        "existing release asset with changed content."
    ),
    "tests/test_release_package.py": (
        "Deterministic release archive contents, installation from the extracted package, user-data "
        "path selection, and pinned binding URL consistency."
    ),
    "tests/test_stusb4500_reference.py": (
        "Generated STUSB4500 example schematic, standard external electrical pin shapes, and "
        "functional PCB layout alignment/zoning."
    ),
    "tests/test_validate_docs.py": (
        "Markdown/HTML local-link and heading-anchor validation, image alt-text requirements, "
        "and exclusion of link-like text inside fenced code blocks."
    ),
    "tests/test_variants.py": (
        "Exact implementation-variant selection, invalid and duplicate variant handling, "
        "KiCad variable expansion, and variant synchronization/publishing."
    ),
    "tests/test_verify_release_gate.py": (
        "Exact-commit release readiness: successful main push CI, correct tag/version mapping, "
        "and rejection of failed or wrong-SHA workflow runs."
    ),
    "tests/test_visual_docs.py": (
        "Visual evidence manifest completeness, source-commit provenance, exact asset inventory, "
        "and per-asset SHA-256 integrity."
    ),
    "tests/test_quality_contracts.py": (
        "Regression tests for the AST-based Any/object policy, annotation-debt ratchet, and "
        "property-based round trips for KiCad S-expressions and parameter escaping."
    ),
}

AUXILIARY_RUNNERS: dict[str, str] = {
    "tests/capture_visual_docs.py": (
        "Captures real KiCad/Tk production UI states and native-rendered schematic evidence, "
        "then writes the visual bundle and SHA-256 provenance manifest consumed by CI."
    ),
    "tests/validate_kicad_cli.py": (
        "Generates the STUSB4500 reference block, exports its KiCad netlist/BOM/ERC artifacts, "
        "and validates the generated files with KiCad CLI."
    ),
}


def _purpose_from_name(name: str) -> str:
    """Turn a descriptive test name into a compact assertion purpose."""
    phrase = name.removeprefix("test_")
    verbs = (
        ("does_not_", "Ensures the system does not "),
        ("rejects_", "Rejects "),
        ("reject_", "Rejects "),
        ("accepts_", "Accepts "),
        ("accept_", "Accepts "),
        ("preserves_", "Preserves "),
        ("creates_", "Creates "),
        ("create_", "Creates "),
        ("generates_", "Generates "),
        ("generate_", "Generates "),
        ("parses_", "Parses "),
        ("parse_", "Parses "),
        ("resolves_", "Resolves "),
        ("resolve_", "Resolves "),
        ("returns_", "Returns "),
        ("maps_", "Maps "),
        ("selects_", "Selects "),
        ("uses_", "Uses "),
        ("keeps_", "Keeps "),
        ("publishes_", "Publishes "),
        ("publish_", "Publishes "),
        ("synchronizes_", "Synchronizes "),
        ("synchronize_", "Synchronizes "),
        ("syncs_", "Synchronizes "),
        ("sync_", "Synchronizes "),
        ("validates_", "Validates "),
        ("validate_", "Validates "),
        ("requires_", "Requires "),
        ("reports_", "Reports "),
        ("prevents_", "Prevents "),
        ("detects_", "Detects "),
        ("detect_", "Detects "),
        ("round_trip_", "Round-trips "),
        ("round_trips_", "Round-trips "),
        ("can_", "Verifies that the system can "),
        ("is_", "Checks that "),
        ("has_", "Checks that "),
    )
    for prefix, candidate_wording in verbs:
        if phrase.startswith(prefix):
            phrase = phrase[len(prefix):]
            wording = candidate_wording
            break
    else:
        wording = "Exercises "
    readable = phrase.replace("_", " ").strip()
    if not readable:
        return "Exercises the behavior named by this test."
    return f"{wording}{readable[0].lower() + readable[1:]}."


def _escape_cell(value: str) -> str:
    """Escape Markdown table delimiters in generated descriptions."""
    return value.replace("|", "\\|")


def _test_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return every pytest/unittest test function in source order."""
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    return sorted(found, key=lambda node: (node.lineno, node.name))


def _module_purpose(relative_path: str, tree: ast.Module) -> str:
    """Return the maintained module-level test purpose."""
    purpose = MODULE_PURPOSES.get(relative_path)
    if purpose is not None:
        return purpose
    docstring = ast.get_docstring(tree)
    return docstring.strip().splitlines()[0] if docstring else "Focused regression tests for this module."


def render_inventory(root: Path = ROOT) -> str:
    """Render the complete inventory and fail if a test module is not classified."""
    test_root = root / "tests"
    module_paths = sorted(test_root.glob("test_*.py"))
    pytest_modules: list[tuple[str, str, list[tuple[str, str]]]] = []
    standalone_modules: list[tuple[str, str, str]] = []

    for path in module_paths:
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        functions = _test_functions(tree)
        if relative in STANDALONE_TESTS:
            if functions:
                raise ValueError(
                    f"{relative} is registered as a standalone runner but contains "
                    f"{len(functions)} pytest test function(s)."
                )
            if not any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
                for node in tree.body
            ):
                raise ValueError(f"{relative} is a standalone runner but has no main() entrypoint.")
            purpose, invocation = STANDALONE_TESTS[relative]
            standalone_modules.append((relative, purpose, invocation))
            continue
        if not functions:
            raise ValueError(
                f"{relative} collects no pytest tests and is not registered as a standalone runner."
            )
        cases = [
            (function.name, _purpose_from_name(function.name))
            for function in functions
        ]
        pytest_modules.append((relative, _module_purpose(relative, tree), cases))

    actual_standalone = set(STANDALONE_TESTS)
    discovered_standalone = {path for path, _, _ in standalone_modules}
    if actual_standalone != discovered_standalone:
        raise ValueError(
            f"Standalone test inventory mismatch: configured={sorted(actual_standalone)}, "
            f"discovered={sorted(discovered_standalone)}."
        )

    case_count = sum(len(cases) for _, _, cases in pytest_modules)
    lines = [
        "# Test inventory",
        "",
        "<!-- Generated by python scripts/test_inventory.py; edit its source metadata, not this file. -->",
        "",
        "This inventory is generated from the Python AST. Every test_* function in a pytest module",
        "is listed individually with its assertion purpose. Files that are executable acceptance",
        "programs rather than pytest modules are listed separately and must remain explicitly",
        "registered in STANDALONE_TESTS in scripts/test_inventory.py.",
        "",
        f"- Pytest modules: **{len(pytest_modules)}**",
        f"- Individually inventoried pytest cases: **{case_count}**",
        f"- Standalone acceptance runners: **{len(standalone_modules)}**",
        f"- Additional CI validation runners: **{len(AUXILIARY_RUNNERS)}**",
        "",
        "## Pytest cases",
        "",
    ]
    for relative, purpose, cases in pytest_modules:
        lines.extend((f"### {relative}", "", purpose, "", "| Test | Purpose |", "| --- | --- |"))
        for name, case_purpose in cases:
            lines.append(f"| {name} | {_escape_cell(case_purpose)} |")
        lines.append("")

    lines.extend(
        (
            "## Standalone acceptance runners",
            "",
            "These files intentionally expose main() instead of pytest test functions. CI invokes",
            "them explicitly because they need a live KiCad process, native IPC server, virtual X11",
            "display, or the KiCad-managed plugin environment. The collection guard allows only",
            "these registered exceptions to collect zero pytest cases.",
            "",
            "| Runner | Purpose | Invocation/context |",
            "| --- | --- | --- |",
        )
    )
    for relative, purpose, invocation in standalone_modules:
        lines.append(
            f"| {relative} | {_escape_cell(purpose)} | {_escape_cell(invocation)} |"
        )

    lines.extend(
        (
            "",
            "## Additional CI validation runners",
            "",
            "These are test-support/acceptance tools but are not named test_*.py, so pytest does",
            "not collect them as modules.",
            "",
            "| Runner | Purpose |",
            "| --- | --- |",
        )
    )
    for relative, purpose in sorted(AUXILIARY_RUNNERS.items()):
        lines.append(f"| {relative} | {_escape_cell(purpose)} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """Write the generated inventory or verify that the committed document is current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when TEST_INVENTORY.md differs from the generated inventory.",
    )
    args = parser.parse_args()
    expected = render_inventory()
    if args.check:
        actual = INVENTORY_PATH.read_text(encoding="utf-8") if INVENTORY_PATH.is_file() else ""
        if actual != expected:
            print(
                "Test inventory is stale. Run python scripts/test_inventory.py and commit the result.",
                file=sys.stderr,
            )
            diff = difflib.unified_diff(
                actual.splitlines(),
                expected.splitlines(),
                fromfile="committed inventory",
                tofile="generated inventory",
                lineterm="",
            )
            for index, line in enumerate(diff):
                if index >= 240:
                    print("... inventory diff truncated after 240 lines ...", file=sys.stderr)
                    break
                print(line, file=sys.stderr)
            return 1
        print("test-inventory=passed")
        return 0
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_text(expected, encoding="utf-8")
    print(f"Generated {INVENTORY_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
