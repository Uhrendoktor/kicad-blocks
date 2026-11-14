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

## Window inventory and design decisions

| Window / surface | Primary task | Essential visible content | Move out of the main view | Functional edge cases to prove |
|---|---|---|---|---|
| Functional Block Library | Find a block and start placement or edit its generated facade | Search; result list; selected block name/short purpose; generated facade preview; compact interface/configuration summary; one action row | Long instructions, repeated library/version/purpose text, full parameter explanations | Empty library, no results, refresh while selection disappears, duplicate block identities, stale catalog, missing facade preview, each toolbar action |
| Edit Facade | Open the generated library symbol shown in the selected record's preview | Selected block record; exact `library_nickname:symbol_name`; native Symbol Editor with its library-tree search focused | Any requirement to select or place a schematic instance; separate explanation of library vs instance edits | Zero, one, or multiple placed instances (all must work); missing catalog/symbol; duplicate or conflicting library record; reload failure; native action rejected; user cancels or manually completes the focused search |
| Manage Libraries | Register and maintain library roots | Registered roots, block counts, default/environment role; Add/Create group; separator; selected-root actions | Paragraph explaining that unregister does not delete data; verbose status sentences | Cancel native chooser, invalid folder, duplicate root, unreadable folder, environment override, attempt to change/remove override, default migration, no selection |
| Place Block handoff | Leave KiCad ready to place the selected facade | Selected block is known; native Add Symbol tool and chooser are opened in the invoking editor | Separate custom placement wizard when the library browser already provides search and preview | IPC status accepted/rejected/unknown, missing editor, xdotool absent, Wayland native-window lookup unavailable, chooser never opens, wrong chooser gets focus, user cancels, stale library reload |
| Create Block from Sheet | Name the reusable block from a selected hierarchical sheet | Source sheet, required block name, optional description, Create/Cancel | Long extraction explanation; the source sheet and resulting facade rules belong in documentation/tooltips | Empty name, duplicate name, invalid hierarchy, missing labels, canceled action, registration failure rollback, original project unchanged |
| Instance configuration | Change one placed instance's preset and local fields | Instance reference/name; profile; variant; typed parameters; PCB layout; Save/Cancel | Paragraphs explaining profile/variant concepts; per-field descriptions shown permanently | Missing required facade fields, stale instance KIID, deleted or renamed variant, unknown profile/layout, invalid typed value, update rollback, only intended instance changes |
| Record picker | Choose a library block for an operation that needs one | Search, result rows, selected record summary, Select/Cancel | Keyboard instruction paragraph; long descriptions | Empty list, filter to zero, single-result shortcut, multiple matches, stale record after refresh, cancel |
| Add PCB layout | Add a board source and associate it with an implementation | Layout name; blank/existing-file mode; source chooser when needed; implementation association; Add/Cancel | Repeated help explaining file field visibility or what a blank board means | Cancel source chooser, invalid/missing file, wrong extension, duplicate layout name/id, no variant, blank-board creation, sidecar handling, add failure leaves no partial files |
| PCB Layout Manager | Maintain stored board layouts and implementation links | Layout table; selected layout; default role; association selector; Add/Remove/Default/Assign; Save/Cancel | Multi-sentence explanation of the no-unlink rule; show as a short subdued constraint or tooltip | Remove default, remove last layout, remap variants when their layout is removed, preserve multi-links, stale selected row, save failure, cancel with dirty changes, generated board opens |
| Implementation details | Add or change a named schematic implementation | Variant value; starting-point mode; source file or source implementation; Use/Cancel | Explanation of the internal KB_VARIANT field; keep in tooltip | Blank value, duplicate/case-insensitive selector, missing source, bad extension, copy source disappears, cancel, new blank schematic valid |
| Implementations manager | Review and maintain default and named schematics | Base/default and named variants table; Add/Edit/Default/Remove; Save/Cancel | Paragraph describing fallback precedence; a short subdued “Empty KB_VARIANT uses the default” is sufficient | Edit base vs variant, duplicate selector, remove default, preserve PCB layout links, save errors, save before opening new variant, cancel dirty edits |
| Task hub | Find less frequent operations by workflow | Three workflow tabs; compact icon-and-label actions; Close | Intro paragraph and repeated descriptions beside each button | Every visible action dispatches the correct action ID; closing the hub preserves the intended editor context; unavailable actions report clear prerequisites |
| Error / confirmation / report dialogs | Recover or understand the result of an operation | Concise issue/result, affected object/path, one clear recovery step, appropriate actions | Tracebacks and internal details in user-facing text; log details remain in logs | Errors are not swallowed; cancel is distinct from failure; file/registration side effects are rolled back; success is reported only after completion |
| KiCad-owned documents and external choosers | Open Symbol Editor/Eeschema/PCB Editor or choose a file | Native editor/desktop chooser; correct owner and project | Plugin-specific fake file browser or parallel duplicate window | Multiple editor processes, focus/stacking, minimize/restore, cancel, selected path/filter, native chooser theme, taskbar grouping |

## Requirement-to-code-to-test traceability

The statuses below describe the current implementation checkpoint, not final acceptance.

| User requirement | Implementation / tests | Current status |
|---|---|---|
| Place Block must not fail only because xdotool is absent | `plugin.py::_native_run_action_status`, `_start_native_symbol_placement`; `tests/test_library_browser.py` covers accepted/rejected IPC, no-xdotool native chooser, and refusal to inject keys without a verified Wayland target; existing `tests/test_plugin_registration.py` exercises X11 handoff with real Eeschema | Improved; native chooser path and X11 handoff tested, actual Wayland cursor handoff remains unverified |
| Edit Facade must edit the generated facade for the highlighted library record without requiring a placed instance | `plugin.py::_edit_facade_symbol` validates the record's generated catalog symbol and opens `common.Control.showSymbolEditor`, `showLibraryTree`, `libraryTreeSearch`; `tests/test_editor_workflow.py` covers no-instance, exact search target, native action rejection and X11/Wayland input paths; `tests/test_plugin_registration.py` and `tests/create_block_ipc_integration.py` exercise the real editor with zero placed instances and the shared-source catalog alias | Implementation and live zero-instance GUI test added; latest CI result pending |
| All actions use appropriate icons and accessible labels | Shared `_action_button`, `_create_action_icon`, `_add_tooltip`; primary/secondary dialog buttons use the shared helper | Implemented in current Tk dialog source; rendered icon/theme inspection and widget-level coverage still required |
| Plugin windows belong to KiCad on Ubuntu Wayland | `_set_window_geometry`, `_attach_tk_window_to_editor`; existing X11 transient-owner tests | X11 behavior covered; Ubuntu 26 Wayland taskbar ownership/focus/stacking not yet proven |
| Reduce descriptive text; use contextual help | Browser overview, instance configuration, picker, implementation dialogs, task hub; hover and focus tooltips | Text reduced in reviewed dialogs; all windows still need rendered inspection at normal/constrained sizes |
| Use OS-themed/native file and directory chooser | `_desktop_file_dialog`, `_pick_kicad_file`, `_pick_directory`, `_pick_save_file`; `tests/test_library_browser.py` covers GTK/Zenity mode/filter, portal preference, cancellation and Tk fallback | Backend selection covered by unit tests; actual GNOME/KDE portal appearance, parent ownership and path return on the user's desktop remain unverified |
| Restore two horizontal Manage Libraries action groups | `plugin.py::_show_library_roots_dialog`; production visual capture `tests/capture_visual_docs.py` | Layout restored in source; refreshed capture must be inspected |

## Desktop and platform evidence

The supported KiCad CI GUI tests use a virtual display. They can prove production widget behavior and X11/Xvfb interactions, but they cannot establish Wayland compositor behavior.

The current Linux file chooser preference is GTK/Zenity on GNOME-like desktops and KDialog on KDE/Plasma. GTK's file chooser can delegate to the XDG file chooser portal when the portal backend is installed and enabled. That is a capability preference, not proof that this machine's portal is active or that the returned dialog is grouped under KiCad. On Wayland, a portal parent requires a surface handle exported via xdg-foreign; a Tk X11 window ID is not a valid Wayland parent identifier. The current helper therefore does not claim native portal ownership. The actual Ubuntu Wayland session, compositor, portal backend, theme, parent handling, and returned path must be recorded in a desktop test.

## Completion gate

Do not mark the UI review complete until:

- Every production window above has a fresh light/dark screenshot inspected at a normal and constrained size.
- Every visible primary button has a test proving the intended side effect or its correct disabled/prerequisite state.
- Place Block has a live KiCad GUI placement scenario. Edit Facade opens the generated symbol identified by the highlighted record, with zero placed instances, and verifies the Symbol Editor title.
- Ubuntu Wayland behavior has been exercised on a real Wayland session; Xvfb is not accepted as a substitute.
- File chooser open/save/directory/cancel/filter/path cases have been run against the actual desktop portal/theme.
- The entire CI suite passes, including documentation, KiCad CLI, editor GUI, PCB IPC, visual audit, and supported Python compatibility checks.
