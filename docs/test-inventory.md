# Test inventory

This inventory is the authoritative list of repository validation entrypoints. CI checks that every tests/test_*.py module appears exactly once, that its execution mode is valid, and that this page stays synchronized with tests/test_inventory.json.

Pytest modules are collected explicitly from the inventory. Standalone acceptance scripts are invoked by dedicated CI steps and are not mistaken for empty pytest modules.

## Pytest modules

| Module | Purpose |
| --- | --- |
| tests/test_blocks.py | Core block model, facade-symbol rendering, library registration, local-copy publishing and synchronization, and nested dependency graph invariants. |
| tests/test_dispatch_release.py | Release workflow dispatch idempotence and rejection of tag, source revision, or version mismatches. |
| tests/test_download_pinned_ipc_wheel.py | Immutable IPC wheel reuse: ABI and source-SHA matching, digest verification, ZIP validation, and corrupt/missing asset handling. |
| tests/test_editor_workflow.py | Editor and command workflows: selected-sheet handling, native Eeschema edit handoff, instance configuration rollback, and variant/layout selection. |
| tests/test_extraction.py | Hierarchical sheet selection, pin extraction, nested sheet-tree preservation, and safe facade synchronization under concurrent edits. |
| tests/test_install.py | Plugin installer platform/version validation, atomic install and reinstall, dry-run safety, and runtime/dependency compatibility. |
| tests/test_library_browser.py | Library search and preview behavior, variants/profiles UI logic, display geometry, and native keyboard placement handoff. |
| tests/test_library_validation.py | Shared-library manifest, asset hashes, missing/corrupt asset reporting, and recursive ERC validation behavior. |
| tests/test_operations.py | Recursive BOM parameter expansion, reference namespacing, child-instance aggregation, and recursive ERC invocation. |
| tests/test_pcb_layout_unit.py | PCB net-reference serialization, coordinate transforms, project netclass rules, and invalid net-code rejection. |
| tests/test_pcb_placement.py | Persistent PCB placement manifests, unique item ownership, active-board identity, and safe native move selection. |
| tests/test_profiles.py | Named profile validation and round-trip behavior through library copies, synchronization, and conflict-safe publishing. |
| tests/test_publish_ipc_wheel.py | Transient GitHub API retry behavior and publisher recovery after an upload succeeds but its response is lost. |
| tests/test_publish_pinned_ipc_wheel.py | Revision-addressed ABI wheel publication, immutable asset handling, digest checks, and Python-tag selection. |
| tests/test_publish_release.py | Versioned release asset upload and digest verification, including refusing to overwrite changed assets. |
| tests/test_python_contracts.py | Static Python policy helpers: explicit-Any detection and changed-file annotation/object-debt metrics. |
| tests/test_release_package.py | Deterministic release archive contents, install-from-archive behavior, and consistency of pinned binding dependencies. |
| tests/test_stusb4500_reference.py | STUSB4500 example schematic structure, hierarchical ports, electrical pin shapes, grid alignment, and functional zoning. |
| tests/test_test_inventory_contract.py | Test inventory routing completeness, uniqueness, correct execution mode, and generated documentation freshness. |
| tests/test_variants.py | Implementation variant resolution, no silent fallback, parameter expansion, and local-copy synchronization/publishing. |
| tests/test_verify_release_gate.py | Release gate acceptance only for successful main-branch CI on the exact packaged commit and matching tag/version. |
| tests/test_visual_docs.py | Visual documentation asset inventory, source revision provenance, content hashes, and incomplete/stale bundle rejection. |

## Standalone acceptance scripts

| Entrypoint | Execution mode | Purpose |
| --- | --- |
| tests/test_eeschema_gui.py | standalone-gui | Real Eeschema GUI acceptance: opens an implementation for editing, saves an edit, and verifies the source file remains intact. |
| tests/test_instance_configuration_gui.py | standalone-gui | Real Tk/X11 instance-configuration form smoke test for interactive field editing and save/undo integration. |
| tests/test_ipc_headless.py | standalone-ipc | Headless real KiCad schematic IPC acceptance for facade placement, instance configuration, library synchronization, and conflict protection. |
| tests/test_library_picker_gui.py | standalone-gui | Real Tk/X11 searchable block picker keyboard-flow acceptance and selected-record identity. |
| tests/test_pcb_ipc.py | standalone-ipc | Live KiCad PCB IPC capability probe for the primitives required by layout import and placement. |
| tests/test_pcb_layout.py | standalone-kicad | End-to-end PCB layout import acceptance against real KiCad board files, geometry, nets, and project settings. |
| tests/test_plugin_registration.py | standalone-gui | Installed-plugin discovery and action-registration acceptance inside KiCad, including managed Python environment readiness. |
| tests/validate_kicad_cli.py | standalone-kicad | Generates a block fixture and validates its generated schematic/symbol output with the native KiCad CLI parser. |

## Workflow helpers

| Entrypoint | Purpose |
| --- | --- |
| tests/capture_visual_docs.py | Captures the real KiCad UI and generated documentation visuals used by CI; this is an evidence-generation helper, not a test module. |

## How to update

Add a purpose statement and execution mode to tests/test_inventory.json whenever a test module or validation entrypoint is added, removed, or renamed. Regenerate this page with python scripts/check_test_inventory.py --write-docs, then run python scripts/check_test_inventory.py. Do not add a second list of pytest paths to the workflow; CI reads them from this inventory.
