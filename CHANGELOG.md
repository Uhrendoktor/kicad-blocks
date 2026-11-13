# Changelog

Changes are grouped by project version. Items under **Unreleased** describe changes merged after the latest numbered source version.

## Unreleased

### Added
- Create blocks from either a selected hierarchical sheet symbol in its parent schematic or the currently open hierarchical sheet when no sheet symbol is selected.
- Use one name-and-description form when creating a block.
- Add browser actions for opening implementations, editing the facade in KiCad Symbol Editor, and managing default and named implementations.
- Show a designer-oriented block overview with the purpose, external interface, configurable settings, implementations, PCB layouts, and profiles.
- Extend the generated UI evidence with the PCB layout manager and both Add PCB layout starting modes.

### Changed
- Reorganize the documentation around a first-use tutorial, task-focused how-to guides, conceptual explanations, and technical reference.
- Update the compatibility guide to distinguish the full CPython 3.12 KiCad integration run from CPython 3.13/3.14 plugin-discovery smoke tests.
- Refresh the action map and procedures for the current library browser, implementation editor, and PCB layout manager.

### Fixed
- Size and center the library browser and Manage Libraries window within the usable display area; keep action buttons visible and prevent the first table row from being clipped.
- Apply the light/dark palette to classic Tk controls, table headers, borders, selection colors, and scrollbars; add spacing and alternating table rows.
- Hide the generated facade Value field by default and remove it from native SVG previews so the symbol artwork remains the focus.
- Avoid GUI-only calls to KiCad's headless `LoadAllLibraries` API; report reload warnings only when GUI reload fails after the block has been saved.
- Keep visual-capture and publishing inventories in one shared module so new screenshots remain included in provenance-checked publishing.
- Document the current toolbar action name, **Create Block from Sheet**, and the working **Edit default** implementation action.
- Correct installation guidance so a source checkout is not presented as a published plugin release package.
- Add automated checks for broken local Markdown links, heading anchors, and missing image descriptions.

## 1.1.0 — 2026-11-12

### Added
- Restore standalone toolbar actions for browsing/placing blocks, creating from a selected sheet, and opening an implementation; retain the grouped task hub as a menu command.
- Add explicit create, change-path, unregister, and default-folder controls to library management.
- Follow desktop light/dark appearance, with a `KICAD_BLOCKS_THEME` override, and support high-resolution trackpad scrolling in the task hub and library dialogs.
- Add named instance profiles that bind a variant, typed parameters, and an optional PCB layout.
- Add multiple named PCB layouts and support an optional matching `.kicad_pro` sidecar.
- Validate shared libraries, manifests, revisions, dependency graphs, and recursive ERC.

### Changed
- Create opaque blocks from selected hierarchical sheets; facade pins and electrical types are derived from root-sheet hierarchical labels.
- Preserve nested hierarchical-sheet trees inside a block by copying child schematics, rewriting `Sheetfile` references, and rebasing instance paths.
- Edit facade symbols in KiCad Symbol Editor and reconcile catalog artwork with revisioned block assets.
- Use KiCad's native cursor-move tool to position imported PCB block layouts.

### Verification and support
- Full KiCad integration validated on KiCad nightly 10.99.0, Linux x86_64, with CPython 3.12.
- Plugin discovery and managed-environment checks also pass with CPython 3.13 and 3.14 on Linux x86_64; these are not full KiCad integration runs for those interpreters.
- Stable KiCad 10.x, KiCad 11 stable, Windows, macOS, ARM, and untested KiCad builds are not yet validated by this repository's full integration suite.
- The historical V1 `v1` tag remains unchanged.

### Known test-coverage limits
- The Symbol Editor shortcut handoff is unit-tested, but a complete live GUI save/edit/publish sequence is not automated.
- The PCB imported-group action is exercised, but the full mouse-driven commit/cancel sequence is not automated end to end.
- Recursive hierarchical-sheet extraction is tested with a two-level fixture and KiCad CLI validation; a manual GUI exercise on a complex multi-sheet design remains recommended.
