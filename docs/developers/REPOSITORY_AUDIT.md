# Repository maintainability audit

**Audit date:** 2026-11-13  
**Audited baseline:** main tree at f526a7284c89a5ef22c4eba0ae54602f24638c10  
**Scope:** all 83 tracked paths in the baseline tree, including the complete test directory, plugin package, build/release scripts, examples, documentation, and CI workflows.

This is a conservative maintainability audit. A test or compatibility entrypoint is not considered disposable merely because its name or subject resembles another one. Removal requires evidence that it is unreferenced and that the behavior it protects is covered elsewhere.

## Findings addressed in this change

| Finding | Evidence | Action |
| --- | --- | --- |
| Pytest discovery mixed unit-test modules with standalone acceptance programs. | Several files named test_*.py expose executable main guards but no pytest test functions. CI also invokes these programs separately under KiCad, IPC, or X11. | Added a machine-checked inventory that distinguishes pytest, GUI, IPC, KiCad CLI, and workflow-helper entrypoints. CI now passes only inventory-declared pytest modules to pytest and retains the dedicated acceptance invocations. |
| Test purposes and execution modes were implicit. | Before this change there was no canonical mapping from test module to behavior or execution environment. | Added tests/test_inventory.json and the generated docs/test-inventory.md. The checker rejects missing, stale, duplicate, empty, or incorrectly classified entries and keeps the rendered document synchronized. |
| Production code used explicit typing.Any at dynamic boundaries. | The imports were in composition.py, operations.py, pcb_placement.py, pcb_layout.py, and place_imported_layout.py. | Added recursive JSON/S-expression types and a small KIID protocol/TypeGuard. No inline suppressions or blanket type-policy exclusions were added. |
| Static checks did not cover the whole Python repository or enforce changed-file debt. | The previous workflow linted plugin and tests only, had no Ruff format check, and did not prevent new annotation debt. | Added a reviewed Ruff policy, a staged Pyright inventory for deterministic core modules, a repository AST gate for explicit Any, and a changed-file annotation/object-debt ratchet. CI now checks examples, plugin, scripts, and tests. |

## Test families reviewed

The baseline contained 27 test_*.py modules plus the visual-capture and KiCad CLI validation helpers. The current inventory includes those entrypoints and the two new quality-contract test modules.

| Area | Modules / entrypoints | Assessment |
| --- | --- | --- |
| Block model and repository lifecycle | test_blocks.py, test_extraction.py, test_profiles.py, test_variants.py, test_library_validation.py | test_blocks.py is a large legacy aggregation (over 1,100 lines) whose subject areas now also have focused files. The focused files cover different slices and regressions; this is a split/reorganization candidate, not evidence that the older synchronization, concurrency, or nested-dependency cases are safe to delete. |
| Editor and library UX | test_editor_workflow.py, test_library_browser.py, test_eeschema_gui.py, test_instance_configuration_gui.py, test_library_picker_gui.py, test_plugin_registration.py | The files exercise distinct layers: mocked command/UI contracts, native KiCad editor behavior, Tk keyboard flows, and installed-plugin discovery. They should remain separate until a common scenario matrix demonstrates exact duplicated assertions. |
| Schematic and PCB IPC | test_ipc_headless.py, test_pcb_ipc.py, test_pcb_layout.py, test_pcb_layout_unit.py, test_pcb_placement.py | These are complementary: schematic IPC, PCB IPC capability, file-level end-to-end merge, pure serialization/geometry rules, and interactive placement identity are different contracts. |
| Release and packaging | test_dispatch_release.py, test_download_pinned_ipc_wheel.py, test_publish_ipc_wheel.py, test_publish_pinned_ipc_wheel.py, test_publish_release.py, test_release_package.py, test_verify_release_gate.py | The files correspond to separate release lifecycle boundaries (dispatch, download, publish, pin, archive, and exact-SHA release gate). Similar GitHub API fixtures do not make the assertions interchangeable. |
| Example and visual evidence | test_stusb4500_reference.py, test_visual_docs.py, validate_kicad_cli.py, capture_visual_docs.py | Native format validation, visual provenance, and screenshot generation are separate quality signals. The capture helper is explicitly classified as a workflow helper, not a pytest test. |

## Candidate cleanup backlog

These items are worth a focused follow-up, but current evidence is insufficient for automatic deletion or consolidation:

1. **Split the broad block test module.** tests/test_blocks.py combines pure model/rendering assertions with repository lifecycle, local-copy merge, publish conflict, and dependency graph scenarios. Move tests by behavior into the focused domain modules only after preserving each scenario and confirming no assertion coverage is lost. Avoid creating a second copy of the same scenarios during the move.
2. **Review legacy top-level launchers.** plugin/kicad-blocks/browse_library.py and plugin/kicad-blocks/open_implementation.py are small standalone wrappers; the current plugin manifest points to entrypoints/browse_blocks.py and entrypoints/open_implementation.py instead. They have no evident in-repository caller in the current manifest/docs/workflow search. Retain them for now because external users may invoke them directly; confirm compatibility expectations before removal. actions.py is not included in this candidate because the installer test explicitly expects it in the installed package.
3. **Consider a shared GitHub Releases transport helper.** publish_ipc_wheel.py, publish_pinned_ipc_wheel.py, publish_release.py, and dispatch_release.py each own part of the GitHub HTTP request/retry/error behavior. A shared transport layer could reduce duplicated request setup, but the pinned-wheel path has stronger immutability and digest requirements and the standard-wheel publisher has its own retry/recovery semantics. Extract only common mechanics with contract tests for every caller; do not merge the release policies.
4. **Decompose the large plugin orchestrator.** kicad_blocks/plugin.py is substantially larger than the other production modules. It contains GUI, editor command, library browser, instance configuration, and PCB orchestration concerns. This is a maintainability/ownership concern rather than proof of duplicate code; split behind existing public action functions and keep the current UI/IPC contracts intact.

## Retention decisions

No existing test or production entrypoint was deleted in this audit. The apparent overlaps above protect distinct runtime boundaries or may be compatibility surfaces. The immediate, high-confidence waste was redundant pytest discovery of standalone acceptance scripts; this has been removed by inventory-driven collection without removing the real KiCad/GUI acceptance runs.

Future deletions should include the exact removed assertions/callers, their replacement coverage, and evidence from the complete CI matrix. Similar names, file age, and source length are not sufficient deletion criteria.
