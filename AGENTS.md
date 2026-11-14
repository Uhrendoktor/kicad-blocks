# KiCad Blocks — UI and workflow acceptance rules

This file is the durable design contract for UI changes. It consolidates the review findings tracked in [issue #1](https://github.com/Uhrendoktor/kicad-blocks/issues/1) and the regression ledger recorded there. Treat every rule below as an acceptance requirement.

## Functional behavior comes first

- Block placement is the primary workflow. The Functional Block Library must register and reload the selected facade in the active project, invoke KiCad's native Place Symbol action, and leave the facade attached to the editor cursor.
- Place Block and Edit Facade must use documented, supported KiCad schematic IPC operations. Do not call unstable `KiCad.run_action()` unless the corresponding request handler is confirmed in the target Eeschema server and the workflow can address the exact library symbol. Do not synthesize global keyboard input, add X11/Wayland-specific runtime fallbacks, or tell users to complete core workflows manually. If KiCad lacks the required API, report the exact capability gap and keep the feature acceptance requirement open.
- A test that mocks window discovery and keypresses independently is not an end-to-end placement test. Exercise the hand-off against a real Eeschema process in the GUI test environment.
- Tk child dialogs must use the portable Tk parent/transient relationship. Do not mutate foreign native window IDs or use X11/Wayland/OS-specific helpers from production code. KiCad IPC currently exposes no portable cross-process window-owner handle; therefore KiCad taskbar/dock grouping and foreign-window activation remain an explicit unresolved requirement until a dependency/API with documented support on Windows, macOS, and Linux is selected and validated. Do not claim this is solved by an X11-only test.

## One shared UI system

- Reuse common helpers for dialog geometry, KiCad window ownership, themed action buttons, tooltips, and file selection. Do not add dialog-specific one-off conventions when a shared helper can express the same behavior.
- Construct the widgets first, calculate requested geometry after layout, and then size/center the window. The initial geometry must fit the primary action buttons on supported display sizes.
- No dialog may open with Add, Save, Use, Cancel, or other primary actions clipped or below the initial viewport. Include a constrained-screen test or visual capture for dialogs whose layout changes.
- Use the operating system's standard file and directory chooser through Tk's native `filedialog` API. Do not build custom filesystem-tree browsers.
- Keep primary actions in one coherent toolbar where practical. Use meaningful icons with visible text labels and tooltips; icons must clarify an action rather than decorate or duplicate the label.
- Avoid ornamental borders, repeated button rows, redundant controls, and status text that competes with the task. Prefer a single clear path for each action.

## Block and implementation semantics

- The implementation manager must make the default implementation explicit and editable. Empty `KB_VARIANT` resolves to that selected implementation; an explicit unknown selector remains an error.
- Editing or renaming an implementation must preserve associated PCB layout metadata or require the user to resolve it explicitly.
- Every stored PCB layout must be associated with at least one implementation. There must be no "No link", "Not linked", or unlink option in the UI.
- Accepted Add/Remove/Assign/default operations in the PCB layout and implementation managers must persist immediately. A visible **Save & close** action may retry a failed write; it must not be the normal commit step.
- The default library root must not be unregisterable. Changing its location must use the explicit default-folder migration path.
- Creating a PCB layout must have a visible, complete workflow: choose or create a board, save it to the block, update the list, and open the saved board. Validate generated board files with KiCad CLI or IPC, not only string assertions.

## Create Block from Sheet regression contract

- Test the registered `create-block` action through `run_action("create-block")`, not only the private helper.
- Exercise the actual visible Create Block form from that registered action. The form must have a mapped owner; do not withdraw the Tk root and then create a transient child of it. The GUI test harness may drive the visible form, but production must not use global input automation.
- Exercise a real KiCad IPC `SheetSymbol` selection and run the production Create Block workflow against it. A mocked selection parser or stubbed details dialog is not sufficient for the GUI regression test.
- Also test an empty selection while a child sheet is active; the created block must come from that active sheet, not silently from the root.
- Verify the new block manifest, extracted hierarchical-label pins, project symbol catalog, symbol-library table registration, and KiCad CLI exports. Assert the original project sheets remain unchanged.
- Cover multiple selections, non-sheet selections, empty/missing `Sheetfile`, missing sheet UUID, unresolvable relative paths, missing hierarchical labels, typed-selection IPC failures, and registration failures. Invalid operations must not leave a partial library block behind.
- Validate hand-written KiCad hierarchy fixtures with `kicad-cli sch export svg` before starting the IPC server. Keep root sheet-instance metadata only in the root schematic; do not invent a root `sheet_instances` block in child files.

## Required review before merging

1. Trace the workflow from the user's entry point to the actual KiCad operation; do not stop at a button callback or a mocked helper.
2. Record the suspected mechanism, strongest alternative explanations, a falsifier, and the expected observable change in the debugging ledger on issue #1.
3. Run the relevant unit tests, compile checks, lint, KiCad CLI validation, real Eeschema GUI smoke tests, and visual UI audit.
4. Inspect the generated screenshots at the supported light/dark themes and display sizes. A green unit suite does not prove that controls are visible or windows are grouped correctly.
5. State clearly in the PR which behavior was proven in CI and which still requires an interactive desktop check.
