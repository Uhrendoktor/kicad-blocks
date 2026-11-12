# Changelog

## 1.1.0 — 2026-11-12

### Added
- Create opaque blocks from selected hierarchical sheets; facade pins and electrical types are derived from root-sheet hierarchical labels.
- Preserve nested hierarchical-sheet trees inside a block by copying child schematics, rewriting Sheetfile references, and rebasing instance paths.
- Edit facade symbols in KiCad Symbol Editor and reconcile catalog artwork with revisioned block assets.
- Configure named instance profiles that bind an implementation variant, typed parameters, and PCB layout.
- Validate shared libraries, manifests, revisions, dependency graphs, and recursive ERC.
- Place merged PCB block layouts with KiCad's native cursor-move tool.

### Verification and support
- Validated on KiCad nightly 10.99.0, Linux x86_64, with CPython 3.12, 3.13 and 3.14 dependency wheels.
- Stable KiCad 10.x, Windows, macOS, ARM, other Python ABIs, and untested KiCad builds are not supported by this release.
- The historical V1 v1 tag remains unchanged.

### Known test-coverage limits
- The Symbol Editor shortcut handoff is unit-tested but not yet covered by a live GUI save/edit test.
- The PCB cursor-driven commit/cancel sequence is not yet covered end-to-end by GUI automation.
- Recursive hierarchical-sheet extraction is tested with a two-level fixture and KiCad CLI validation; a manual GUI exercise on a complex multi-sheet project remains recommended.
