# Repository quality and redundancy audit

**Reference standard:** `Uhrendoktor/freecad-cloth#2751`, specifically its staged Ruff, Pyright, test-inventory, changed-file-debt, and high-confidence dead-code contract. The adaptation deliberately omits FreeCAD-only runtime, geometry, solver, and GUI checks.

## Scope and method

The repository's complete tracked tree was enumerated at base commit `8dd6475916d6e55e9d9b39e153681cc58c7415e9`. The audit classified every test path, enumerated pytest functions and class methods, cross-checked standalone runners against the CI workflow and helper invocations, inspected their purpose and assertion surfaces, and searched production/import boundaries and release scripts for annotation escape hatches and repeated implementation patterns.

This was a structural and targeted semantic audit, not a claim that a heuristic reference search can prove a KiCad plugin callback is unused. KiCad's manifest/entry-point discovery, IPC registration, dynamic Tk callbacks, and subprocess entry points make direct-import counts unsafe as deletion evidence.

## Test inventory findings

The starting tree contained 29 files named `tests/test_*.py`. Seven were executable `main()`-based KiCad/GUI acceptance programs rather than pytest modules. They are now named `acceptance_*.py` and are explicitly invoked by CI or the two runner scripts that consume them. This removes the misleading pytest naming without removing the underlying coverage.

The current AST inventory lists **209 pytest test functions/methods across 24 modules**, plus seven native acceptance runners and two additional CI validation/evidence scripts. The inventory generator rejects an empty `test_*.py` module, rejects duplicate test method names within a class (Python would silently shadow an earlier definition), verifies the standalone-runner catalogue, and compares generated output against the checked-in inventory.

## Overlap review: no test deletion justified

| Areas that look similar | Why both are retained |
| --- | --- |
| `test_pcb_layout_unit.py` and `acceptance_pcb_layout.py` | The unit suite checks deterministic S-expression parsing, coordinate transforms, net remapping, and project-netclass merging. The acceptance runner exercises KiCad-backed import/merge and validates the generated board with KiCad. |
| `test_editor_workflow.py` and `acceptance_instance_configuration_gui.py` | The unit suite isolates action routing, selection, and transaction/error behavior. The GUI runner drives the actual Tk dialog and checks profile selection and the update/commit contract through a mocked schematic collaborator. It is not a replacement for live IPC coverage. |
| `test_library_browser.py` and `acceptance_library_picker_gui.py` | The former checks browser state/search/selection and event-handling logic. The latter verifies real Tk rendering and X11 keyboard interaction. |
| `test_ipc_headless.py` and `test_pcb_ipc.py` | These probe separate schematic and PCB IPC APIs. They use the real KiCad runtime and are not mock duplicates. |
| `test_publish_ipc_wheel.py`, `test_publish_pinned_ipc_wheel.py`, and `test_publish_release.py` | They cover different release contracts: retry/recovery for the rolling IPC wheel, immutable source-SHA/ABI wheel publication, and versioned plugin ZIP/checksum publication. |
| `test_variants.py` and `test_profiles.py` | Variant tests cover implementation resolution and project/sheet variable expansion. Profile tests cover a higher-level configuration that binds a variant, typed parameter overrides, and a PCB layout, including sync/publish conflicts. |
| `test_stusb4500_reference.py`, `validate_kicad_cli.py`, and the native visual capture | They check, respectively, example structure/topology, KiCad's parser/CLI acceptance of generated files, and rendered visual evidence. |

No test module or test case was removed. A candidate was not considered redundant merely because it shares fixtures or touches the same feature; deletion requires equivalent input variation, assertions, failure conditions, and runtime layer.

## Production-code findings and disposition

| Finding | Evidence and risk | Disposition |
| --- | --- | --- |
| Repeated GitHub API request plumbing | `publish_ipc_wheel.py`, `publish_pinned_ipc_wheel.py`, `publish_release.py`, `dispatch_release.py`, and `verify_release_gate.py` each build authenticated requests and decode API responses. `download_pinned_ipc_wheel.py` has its own read/download path. Retry policy, immutable asset rules, upload recovery, digest checks, and expected 404 behavior are not identical. | Confirmed consolidation candidate, not dead code. Extract a small shared transport only with contract tests for each caller; do not replace the different retry/digest policies with one lowest-common-denominator wrapper. |
| S-expression helpers are private but shared across modules | `repository.py` and `extraction.py` consume helpers from `pcb_layout.py` (`_all`, `_direct`, `_dump`, `_head`, and `_Quoted`). The parser and PCB merge engine consequently have wider coupling than their private names imply. | Structural refactor candidate: move parser/token types and tree traversal into a dedicated KiCad S-expression module, then update imports and tests. No helper was deleted. |
| Large orchestration modules | `plugin.py` combines action registration, IPC operations, Tk dialogs, and workflow coordination; `repository.py` combines library/catalog management, local-copy synchronization, publishing, and dependency graph resolution; `pcb_layout.py` combines parsing, transforms, net mapping, and project settings. | Maintainability candidates for bounded extraction. Their functions are reached through dynamic action registration and cross-module calls, so file size alone is not a dead-code signal. |
| Large mixed-purpose test modules | `test_blocks.py` covers both pure model rules and filesystem/library workflows. Several GUI/unit suites also contain distinct subdomains. | Possible future organization-only split. Current test names and assertions are discoverable, and splitting them now would create churn without eliminating duplicated behavior. |

No production helper was deleted solely because it lacked an obvious direct import. A high-confidence Vulture scan is configured at 100% confidence on plugin core and scripts; lower-confidence findings require review against the plugin manifest, dynamic callbacks, subprocess entry points, and native integration runners.

## Quality-gate changes

The PR adds repository-wide Ruff lint/format checks, a reviewed Pyright surface, a 100%-confidence Vulture scan, an AST-generated test inventory, and a changed-file ratchet for missing annotations, `Any`/generic-`object` annotations, inline suppressions, and public production documentation debt. Runtime-bootstrap `E402` is explicitly ignored with rationale because imports from the source plugin package must follow the runtime `sys.path` setup.

The reviewed Pyright inventory is intentionally a staged subset, not a claim of whole-package static typing. New production modules must be explicitly included or narrowly excluded in that inventory. Future expansion should follow diagnostics and test evidence rather than use global suppressions or fabricate annotations.

## Ongoing rules

- Add a purpose docstring to each new test so the inventory explains intent rather than only restating the function name.
- Keep each test method name unique within its class.
- Keep real KiCad/GUI acceptance runners separate from pytest-discovered modules and register every runner.
- Do not delete a test until its equivalence is demonstrated at the assertion and execution-layer level.
- Do not relax lint/type/debt thresholds to make a failure disappear; fix the code or record a narrow, evidence-backed boundary rule.
