# Changelog

## Unreleased

### Added
- Rework the project landing page and add separate getting-started, feature-workflow, feature-reference, and visual-evidence guides.
- Extend generated UI evidence to include all task-hub tabs and the instance-configuration dialog in light and dark themes.

### Fixed
- Keep the generated visual-capture and publishing inventories in one shared module so newly added UI screenshots do not make the provenance publisher reject the bundle.


### Fixed
- Size and center the library browser and folder manager to the usable display area; keep action buttons visible and replace Treeview's implicit tree column with explicit heading columns so the first row is no longer clipped.
- Extend the light/dark palette to classic Tk controls, table headers, borders, selection colors, and arrow-free scrollbars; add more breathing room and alternating table rows.
- Hide the generated facade Value field by default and remove it from native SVG previews so the symbol graphic is the focus.
- Avoid GUI-only calls to KiCad's headless `LoadAllLibraries` API; report a reload warning only when GUI reload fails after the block has been saved.

### Added
- Create from either a selected hierarchical sheet symbol in its parent, or the currently displayed hierarchical sheet with no selection.
- Replace consecutive creation prompts with one name-and-description form.
- Add library-browser actions for editing the implementation, opening the facade in KiCad Symbol Editor, and managing implementation variants after creation.
- Show a designer-oriented block overview instead of internal IDs and file metadata.

## 1.1.0 — 2026-11-12

### Added
- Restore standalone toolbar actions for browsing/placing blocks, creating from a selected sheet, and opening an implementation; retain the grouped task hub as a menu command.
- Add explicit create, change-path, unregister, and default-folder controls to library management.
- Follow desktop light/dark appearance, with a `KICAD_BLOCKS_THEME` override, and support high-resolution trackpad scrolling in the task hub and library dialogs.

### Changed
- Remove the duplicate “Quick place a block” picker; block selection now happens in the library browser.
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
