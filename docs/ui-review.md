# UI review and interaction contract

This page is the working design review for the KiCad Blocks desktop UI. It is intentionally explicit about what has and has not been verified. A visually consistent button is not considered complete unless its action is proven against the real editor state and its failure modes are tested.

## Information hierarchy

Dialogs should present, in this order:

1. The task and the object being changed.
2. The fields and actions required to complete that task.
3. Essential consequences or constraints that can prevent data loss or explain a choice.
4. Secondary guidance in keyboard/focus-accessible tooltips or a single subdued tip.

Do not repeat a block description in multiple panels, explain a field immediately below its own label, or show permanent instructions for actions that already have tooltips and keyboard bindings. Keep meaningful button text alongside semantic icons for primary actions. Tooltips supplement labels; they do not replace them.

Every modal must support Escape/cancel where cancellation is safe, have a useful initial focus, and preserve the invoking editor context. Destructive or irreversible actions require a clear consequence and an appropriate confirmation.

## Density and visual hierarchy

The shared panels follow the compact slate treatment established in
`kicad-jlcpcb-interface`: restrained surfaces, a cool accent, quiet secondary
text, flat controls, and compact 29 px table rows. Use one clear primary action,
keep related secondary actions in the same toolbar, and group unrelated actions
with restrained spacing rather than extra explanatory cards. Prefer concise
context over repeated descriptions, and expose details only where they support
the current task. Preserve the selected KiCad theme and the existing keyboard,
accessibility, and semantic UI contracts.

## Window inventory and design decisions

| Window / surface | Primary task | Essential visible content | Move out of the main view | Functional edge cases to prove |
|---|---|---|---|---|
| Functional Block Library | Find a block and start placement or edit its generated facade | Search; result list; selected block name/short purpose; switchable Facade/Schematic preview; implementation and sheet selectors when applicable; compact interface/configuration summary; one action row | Long instructions, repeated library/version/purpose text, full parameter explanations | Empty library, no results, refresh while selection disappears, duplicate block identities, stale catalog, missing facade or schematic preview, named implementation selection, multi-sheet navigation, each toolbar action |
| Edit Facade | Edit the exact generated library symbol shown in the selected record's preview | Selected block record; native Symbol Editor frame; exact `OpenLibraryItem` IPC request; `GetOpenDocuments` confirms the selected library ID | Typing into an unverified tree search or treating an acknowledged action as proof the target symbol loaded | Missing catalog/symbol; API handler not registered yet; already-open editor state; unsaved symbol changes; reload failure |
| Manage Libraries | Register and maintain library roots | Registered roots, block counts, default/environment role; Add/Create group; separator; selected-root actions | Paragraph explaining that unregister does not delete data; verbose status sentences | Cancel native chooser, invalid folder, duplicate root, unreadable folder, environment override, attempt to change/remove override, default migration, no selection |
| Place Block handoff | Start native cursor placement for the selected facade | Selected block is known; Eeschema's native Add Symbol action is started; the exact `KBlocks:<symbol>` ID is chosen; symbol follows the cursor until click/Escape | Fixed-coordinate insertion or making the user re-search the library chooser | Missing library entry; unavailable Eeschema focus; chooser does not open; platform input backend unavailable; user cancels with Escape |
| Create Block from Sheet | Name the reusable block from a selected hierarchical sheet | Source sheet, required block name, optional description, Create/Cancel | Long extraction explanation; the source sheet and resulting facade rules belong in documentation/tooltips | Empty name, duplicate name, invalid hierarchy, missing labels, canceled action, registration failure rollback, original project unchanged |
| Instance configuration | Change one placed instance's preset and local fields | Instance reference/name; profile; variant; typed parameters; PCB layout; Save/Cancel | Paragraphs explaining profile/variant concepts; per-field descriptions shown permanently | Missing required facade fields, stale instance KIID, deleted or renamed variant, unknown profile/layout, invalid typed value, update rollback, only intended instance changes |
| Record picker | Choose a library block for an operation that needs one | Search, result rows, selected record summary, Select/Cancel | Keyboard instruction paragraph; long descriptions | Empty list, filter to zero, single-result shortcut, multiple matches, stale record after refresh, cancel |
| Add PCB layout | Add a board source and associate it with an implementation | Layout name; blank/existing-file mode; source chooser when needed; implementation association; Add/Cancel | Repeated help explaining file field visibility or what a blank board means | Cancel source chooser, invalid/missing file, wrong extension, duplicate layout name/id, no variant, blank-board creation, sidecar handling, add failure leaves no partial files |
| PCB Layout Manager | Maintain stored board layouts and implementation links | Layout table; selected layout; default role; association selector; Add/Remove/Default/Assign; Close; every mutation autosaves | Multi-sentence explanation of the no-unlink rule; show as a short subdued constraint or tooltip | Remove default, remove last layout, remap variants when their layout is removed, preserve multi-links, stale selected row, save failure with inline retry-on-close, generated board opens |
| Implementation details | Add or change a named schematic implementation | Variant value; starting-point mode; source file or source implementation; Use/Cancel | Explanation of the internal KB_VARIANT field; keep in tooltip | Blank value, duplicate/case-insensitive selector, missing source, bad extension, copy source disappears, cancel, new blank schematic valid |
| Implementations manager | Review and maintain default and named schematics | one Default row plus named alternatives; Add/Edit/Set as default/Remove; every mutation autosaves; Close retries failed persistence | Paragraph describing fallback precedence; a short subdued “Empty KB_VARIANT uses the default” is sufficient | Edit default vs alternative, duplicate selector, promote an alternative to Default, preserve PCB layout links, save errors, save before opening new alternative |
| Legacy Task Hub | Compatibility access to grouped actions | Three workflow tabs; compact icon-and-label actions; Close. Not the intended workflow. | Do not route common create/place/publish work through this panel | Legacy actions keep correct selection-dependent enabled state; unavailable actions report clear prerequisites |
| Error / confirmation / report dialogs | Recover or understand the result of an operation | Concise issue/result, affected object/path, one clear recovery step, appropriate actions | Tracebacks and internal details in user-facing text; log details remain in logs | Errors are not swallowed; cancel is distinct from failure; file/registration side effects are rolled back; success is reported only after completion |
| KiCad-owned documents and external choosers | Open Symbol Editor/Eeschema/PCB Editor or choose a file | Native editor/desktop chooser; correct owner and project | Plugin-specific fake file browser or parallel duplicate window | Multiple editor processes, focus/stacking, minimize/restore, cancel, selected path/filter, native chooser theme, taskbar grouping |

## Requirement-to-code-to-test traceability

The statuses below describe the current implementation checkpoint, not final acceptance.

| User requirement | Implementation / tests | Current status |
|---|---|---|
| Place Block uses KiCad's native cursor workflow | `plugin.py::_start_native_symbol_placement` starts Eeschema's native Add Symbol tool, chooses the exact `KBlocks:<symbol>` ID using the platform input bridge, and leaves the native symbol attached to the cursor. | Code and widget contracts updated; exact behavior still requires the live Eeschema GUI test on CI plus real Wayland/Windows/macOS verification |
| Edit Facade loads the exact Symbol Editor library item | `plugin.py::_edit_facade_symbol` opens Symbol Editor with KiCad's native frame action/menu, sends `OpenLibraryItem` for `KBlocks:<symbol>`, and verifies the active document with `GetOpenDocuments`. | Exact-item IPC request and verification contracts added; live GUI validation still required on CI and target desktops |
| All actions use appropriate icons and accessible labels | Shared `_action_button`, `_create_action_icon`, `_add_tooltip`; primary/secondary dialog buttons use the shared helper | Implemented in current Tk dialog source; rendered icon/theme inspection and widget-level coverage still required |
| Plugin child dialogs follow their in-process Tk parent | `_attach_tk_window_to_editor` uses Tk's portable `transient(master)` API; no foreign X11/Wayland window IDs are used | In-process child-dialog ownership is implemented. Cross-process KiCad taskbar/dock grouping remains an open architecture gap because KiCad IPC exposes no portable foreign-window owner handle |
| Non-modal completion feedback | Browser status line and manager status labels; logs retain diagnostic detail | Text reduced in reviewed dialogs; all windows still need rendered inspection at normal/constrained sizes |
| Use OS-themed/native file and directory chooser | `plugin.py::_pick_kicad_file`, `_pick_directory`, `_pick_save_file` call Tk's standard `filedialog` APIs; `tests/test_library_browser.py` covers filters, initial folder/name, parent, default suffix and cancellation | Custom Zenity/KDialog process launching has been removed to follow `AGENTS.md`; real Ubuntu 26 Wayland dialog appearance, theme, parent grouping and returned path still need interactive verification |
| Restore two horizontal Manage Libraries action groups | `plugin.py::_show_library_roots_dialog`; production visual capture `tests/capture_visual_docs.py` | Layout restored in source; refreshed capture must be inspected |

The Functional Block Library preview uses two tabs so facade artwork and the circuit schematic each have enough room to remain legible. The schematic tab renders the selected Default or named implementation with KiCad CLI, and exposes a sheet selector when the implementation contains child sheets. Both views are scrollable; the schematic keeps a paper-white drawing surface in both UI themes so KiCad's native sheet colors retain their intended contrast. The preview is never approximated with a hand-drawn circuit.

## Desktop and platform evidence

The system-theme path preserves the native Tk colors while applying the shared Treeview geometry (36 px rows) so text is not clipped. It does **not yet** read KiCad's selected color theme into the Tk palette. KiCad stores the active color theme in its settings and stores theme definitions as JSON in the settings `colors` directory; wiring that active theme into the plugin palette remains an open task.

The supported KiCad CI GUI tests run under `xvfb-run`; the harness may use X11 tools to inspect plugin windows and exercise visible controls. Production hand-off uses KiCad's native actions and platform input bridges (xdotool on X11, wtype on Wayland, pynput on Windows/macOS, and AppleScript for the macOS Tools menu). These are used only to start KiCad-native tools; exact symbol selection is then verified through IPC. Real desktop verification is still required, especially for Wayland compositor policy, Windows/macOS Accessibility permissions, and chooser focus. The visual audit captures production windows at 1600×1000; it does not yet provide the required constrained-size captures.

File and directory selection now goes through Tk's standard `filedialog.askopenfilename`, `askdirectory`, and `asksaveasfilename` APIs, as required by `AGENTS.md`; the plugin no longer launches separate Zenity/KDialog processes or maintains a custom file browser. Unit tests verify the arguments passed to those APIs, including initial file/directory, filters, parent, save suffix and cancellation. This does not prove that the chooser is visually native, portal-backed or grouped with KiCad on the target desktop. The actual Ubuntu 26 Wayland session, Tk version, desktop portal/theme, parent ownership and returned path must still be recorded in an interactive test.

## Completion gate

Do not mark the UI review complete until:

- Every production window above has a fresh light/dark screenshot inspected at a normal and constrained size.
- Every visible primary button has a test proving the intended side effect or its correct disabled/prerequisite state.
- Live Eeschema testing confirms the exact native chooser attaches the selected facade to the cursor; Edit Facade confirms the exact `KBlocks:<symbol>` document through Symbol Editor IPC.
- Ubuntu Wayland behavior has been exercised on a real Wayland session; Xvfb is not accepted as a substitute.
- File chooser open/save/directory/cancel/filter/path cases have been run against the actual desktop portal/theme.
- The entire CI suite passes, including documentation, KiCad CLI, editor GUI, PCB IPC, visual audit, and supported Python compatibility checks.
