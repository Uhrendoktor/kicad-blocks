# Repository test and code audit

## Scope and method

The audit covers the tracked Python source roots (`examples/`, `plugin/`, `scripts/`, and
`tests/`), the plugin manifest and action entrypoints, the CI workflow, test inventory,
and release/IPC tooling. It combines source-level call/reference checks, test-purpose
classification, static-analysis boundaries, and the distinct runtime layers exercised by
the acceptance harnesses.

The generated [test inventory](testing-inventory.md) is the authoritative list of test
functions/methods and their purpose. At this revision it records **196 pytest test
functions/methods across 22 modules**, **7 direct KiCad GUI/IPC acceptance runners**, and
**2 test-support/evidence scripts**. The pytest count is a count of discovered test
functions and methods, not a multiplication of parameterized cases.

## Test overlap decisions

The audit found several pairs that look similar by name but protect different behavior
boundaries. They are retained:

| Tests or group | Why both remain |
| --- | --- |
| `test_pcb_layout_unit.py` and `test_pcb_layout.py` | The former exercises deterministic S-expression, geometry, net mapping and validation logic without KiCad. The latter runs the real KiCad CLI/IPC import and validates saved native board objects and output safety. |
| Schematic/editor unit tests and the GUI/IPC runners | Controlled doubles make failure paths deterministic; the dedicated runners verify KiCad registration, native editor behavior, persistence, and IPC contracts. |
| `test_dispatch_release.py`, wheel download/publication tests, release packaging, and release-gate tests | These exercise different transitions and trust boundaries: dispatch idempotency, immutable artifact acquisition, publication integrity, archive contents, and exact-source-SHA release authorization. |
| HTTP response doubles in the release tests | The doubles intentionally model different endpoints and payload/error contracts. Sharing them solely to reduce fixture lines would hide those contracts. |
| Visual documentation capture and functional GUI acceptance | Captures provide reproducible review/release evidence; functional acceptance checks user workflows and saved state. Neither substitutes for the other. |

No behavioral test was removed because of name similarity alone. The audit does not
classify tests as redundant solely because they use similar fixtures or exercise adjacent
workflows. A test is removable only when its protected invariant is already asserted by
another test at the same or a stronger runtime boundary.

## Code cleanup

- Removed the unreferenced `plugin/kicad-blocks/actions.py` and
  `plugin/kicad-blocks/browse_library.py` wrapper modules. The actual manifest actions
  point to scripts under `entrypoints/`; the wrappers were not referenced by the
  manifest or current documentation.
- Consolidated repeated `sys.path` mutation and plugin loading in
  `plugin/kicad-blocks/entrypoints/_bootstrap.py`. The action hub, browser, create-block,
  and open-implementation scripts share this bootstrap. Focused tests cover path setup,
  delegation, manifest consistency, and installation packaging.
- Preserved the root `open_implementation.py` compatibility entrypoint. It remains a
  compatibility surface and was not treated as dead code.
- Retained the separate release and immutable-wheel scripts. Their APIs, side effects,
  and no-overwrite guarantees are distinct, even where their HTTP handling looks alike.

## Static-analysis boundaries

- Ruff uses the curated rules in `pyproject.toml` and runs read-only over all four Python
  source roots. Formatter output is checked, never committed or pushed by CI.
- Pyright checks the reviewed deterministic core, bootstrap boundary, and tooling scripts.
  The dynamic KiCad/Qt plugin adapter remains an explicit host-API exclusion.
- The Python policy check bans explicit `Any`, unreviewed `object` annotations,
  diagnostic suppressions, dynamic `eval`/`exec`, and `os.system`. Recursive JSON and
  S-expression aliases and a KIID Protocol with runtime value validation replace the previous five-file
  blanket `Any` exemption.
- The annotation ratchet prevents changed files in Pyright's reviewed scope from adding
  missing function annotations or generic `object` annotations.
- Vulture runs at 100% confidence on deterministic core modules only. Dynamic GUI
  registration and callbacks are not deleted based on speculative static references.

## Follow-up criteria

Revisit a candidate only with evidence: a repository-wide reference search, a test that
asserts the same invariant at the same or stronger boundary, and an exact CI run showing
the candidate is not part of a runtime/compatibility path. Keep user-visible APIs,
KiCad persistence behavior, board geometry/net semantics, release integrity, and visual
evidence unchanged during cleanup.
