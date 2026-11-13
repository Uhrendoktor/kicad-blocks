# Repository test and redundancy audit

## Scope and method

The audit examined the complete recursive Git tree of the repository's main branch at
`f526a7284c89a5ef22c4eba0ae54602f24638c10` (83 tracked files), including all
`tests/test_*.py` modules, production Python packages, release scripts, examples,
documentation and both GitHub Actions workflows. Test-module routing was compared
with the workflow's actual script invocations. Similar-looking tests were judged by
their execution layer and behavior under test rather than by filename alone.

The branch now adds an authoritative inventory and executable collection contract.
See [TEST_INVENTORY.md](TEST_INVENTORY.md).

## Confirmed finding repaired

`tests/test_instance_configuration_gui.py` is a substantial real-Tk integration
check. It drives the instance configuration form, changes a profile, verifies the
parameter/variant/layout selections, submits one schematic IPC transaction and
checks the final instance fields. Before this change it had no invocation in
`.github/workflows/ci.yml`; pytest also collected zero cases from it because it is
a standalone `main()` script. It was therefore effectively unexecuted in CI.

The GUI workflow now invokes it under Xvfb. The inventory validator requires every
standalone test script to have a `main()` entry point and an explicit CI reference,
so this class of orphaned test is caught automatically.

## Apparent overlaps retained because they cover different contracts

| Area | Similar-looking checks | Why both are retained |
| --- | --- | --- |
| PCB layout | `test_pcb_layout_unit.py` and `test_pcb_layout.py` | One checks pure parsing, coordinate transforms, nets and netclasses; the other exercises actual stored-layout application through KiCad. Removing either would lose a distinct unit or native-integration layer. |
| PCB IPC | `test_pcb_ipc.py` and `test_pcb_layout.py` | The former probes the native IPC primitives and board fixture; the latter validates the composed layout-application workflow. |
| Block extraction | `test_blocks.py` and `test_extraction.py` | The block suite covers the broad repository lifecycle; extraction tests focus on hierarchical sheet selection, interface derivation, facade synchronization and nested-sheet preservation. Similar subject matter does not make the assertions interchangeable. |
| Wheel publication | `test_publish_ipc_wheel.py` and `test_publish_pinned_ipc_wheel.py` | The former covers ordinary release upload/retry recovery; the latter enforces immutable source-pinned assets and digest-based idempotency. |
| Plugin discovery | `test_plugin_registration.py` on CPython 3.12, 3.13 and 3.14 | Repetition is the supported-interpreter ABI matrix, not duplicate execution in one environment. |
| Python/visual CI | Main self-hosted job and hosted visual-audit job both run pytest, compile, lint and KiCad CLI validation | The hosted job is an independent environment that preserves visual review when the local KiCad runner is unavailable. Removing its checks would weaken that fallback. |

## Repository structure and code-maintenance observations

- The production implementation is concentrated in large modules, notably
  `plugin.py`, `repository.py` and `pcb_layout.py`. This is a maintainability
  concern, not evidence that their code is unused. Splitting them should be a
  behavior-preserving refactor with import/API tests, not a speculative deletion.
- Release tooling has similarly named helpers for distinct stages: building an
  archive, dispatching its workflow, validating the exact source commit, publishing
  immutable IPC wheels, publishing ordinary release assets and publishing visual
  evidence. The separate tests exercise different side-effect and integrity
  contracts; no safe consolidation was justified by name similarity alone.
- The small plugin entry-point modules and icon variants are manifest/UI resources,
  not standalone application implementations. Removing them based on their size
  would risk breaking KiCad action discovery or theme-specific UI.
- The existing pytest modules use both pytest functions and unittest-style test
  classes. The inventory parser recognizes both forms; it does not count helpers or
  nested local functions as independent tests.
- The full suite is deliberately repeated on a second runner. This costs CI time,
  but it provides an independent hosted validation path and is not a redundant
  assertion inside a single test process.

## Cleanup decision

No production function or test case was deleted in this audit. A reliable dead-code
claim needs reference analysis across dynamic KiCad action registration, IPC
callbacks, manifest-loaded entry points and runtime-discovered resources. The
repository is highly dynamic at those boundaries; a filename-only or text-search
heuristic would produce unsafe false positives.

The concrete waste found was an integration script that CI never executed. It is
now wired into CI, and routing/collection checks prevent future orphaned test
modules. Other suspected overlaps above are explicitly documented rather than
removed without proof that their assertions are redundant.
