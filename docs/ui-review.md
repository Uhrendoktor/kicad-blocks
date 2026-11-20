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
text, flat controls, and compact 29 px table rows. The reusable implementation
now lives in `kicad_blocks.theme` and `kicad_blocks.ui_components`: shared
spacing and typography tokens, semantic light/dark palettes, flat text tabs
with an active underline, and scrollbars that reveal their draggable thumb on
hover or movement. The Functional Block Library uses unbordered preview and
overview surfaces so hierarchy comes from spacing and typography rather than
nested outlines. Use one clear primary action, keep related secondary actions
in the same toolbar, and expose details only where they support the current
task. Preserve KiCad's active color theme, keyboard access, and semantic UI
contracts.

## Window inventory and design decisions

| Window / surface | Primary task | Essential visible content | Move out of the main view | Functional edge cases to prove |
|---|---|---|---|---|
| Functional Block Library | Find a block and start placement or edit its generated facade | Search; result list; selected block name/short purpose; switchable Facade/Schematic preview; implementation and sheet selectors when applicable; compact interface/configuration summary; one action row | Long instructions, repeated library/version/purpose text, full parameter explanations | Empty library, no results, refresh while selection disappears, duplicate block identities, stale catalog, missing facade or schematic preview, named implementation selection, multi-sheet navigation, each toolbar action |
| Edit Facade | Adjust the selected generated facade's external pin layout | Project-local `KBlocks.kicad_sym`; pin number/type/name remain label-derived; edit X/Y/angle/length; optimistic file-hash guard; write, then reload KiCad library | Write the shared library directly or change implementation-derived interface data from the facade editor | Missing project catalog; duplicate or incomplete pin definition; invalid geometry; concurrent catalog edit; library reload failure |
| Manage Libraries | Register and maintain library roots | Registered roots, block counts, default/environment role; Add/Create group; separator; selected-root actions | Paragraph explaining that unregister does not delete data; verbose status sentences | Cancel native chooser, invalid folder, duplicate root, unreadable folder, environment override, attempt to change/remove override, default migration, no selection |
| Place Block | Insert the selected facade in the active sheet | Exact `KBlocks:<symbol>` is inserted at the active page center (including custom/portrait page sizing), focused, selected, and saved by schematic IPC | Global keyboard injection or platform-specific window/chooser automation | Missing project library entry; invalid page settings; focus/selection confirmation fails; unsupported KiCad API version |
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
| Place Block uses supported schematic IPC | `plugin.py::_start_native_symbol_placement` inserts the exact facade at the page center, focuses it, selects it, verifies the selection, and saves. | Existing live Eeschema IPC/GUI test verifies page-aware placement/focus/selection. Cursor-attached placement remains an upstream schematic interactive-move API gap. |
| Edit Facade edits project-local pin layout safely | `plugin.py::_edit_facade_symbol` reads one generated symbol, edits only pin X/Y/angle/length under an optimistic SHA-256 guard, writes the project catalog atomically, and reloads the project library. Publish imports it with existing three-way conflict checks and updates the shared revision. | Pure file-level tests plus real Tk interaction contract; native full Symbol Editor launch remains an upstream API gap |
| All actions use appropriate icons and accessible labels | Shared `_action_button`, `_create_action_icon`, `_add_tooltip`; primary/secondary dialog buttons use the shared helper | Implemented in current Tk dialog source; rendered icon/theme inspection and widget-level coverage still required |
| Plugin child dialogs follow their in-process Tk parent | `_attach_tk_window_to_editor` uses Tk's portable `transient(master)` API; no foreign X11/Wayland window IDs are used | In-process child-dialog ownership is implemented. Cross-process KiCad taskbar/dock grouping remains an open architecture gap because KiCad IPC exposes no portable foreign-window owner handle |
| Non-modal completion feedback | Browser status line and manager status labels; logs retain diagnostic detail | Text reduced in reviewed dialogs; all windows still need rendered inspection at normal/constrained sizes |
| Use OS-themed/native file and directory chooser | `plugin.py::_pick_kicad_file`, `_pick_directory`, `_pick_save_file` call Tk's standard `filedialog` APIs; `tests/test_library_browser.py` covers filters, initial folder/name, parent, default suffix and cancellation | Custom Zenity/KDialog process launching has been removed to follow `AGENTS.md`; real Ubuntu 26 Wayland dialog appearance, theme, parent grouping and returned path still need interactive verification |
| Restore two horizontal Manage Libraries action groups | `plugin.py::_show_library_roots_dialog`; production visual capture `tests/capture_visual_docs.py` | Layout restored in source; refreshed capture must be inspected |

The Functional Block Library preview uses two tabs so facade artwork and the circuit schematic each have enough room to remain legible. The schematic tab renders the selected Default or named implementation with KiCad CLI, and exposes a sheet selector when the implementation contains child sheets. Both views are scrollable; the schematic keeps a paper-white drawing surface in both UI themes so KiCad's native sheet colors retain their intended contrast. The preview is never approximated with a hand-drawn circuit.

## Desktop and platform evidence

The theme follows an explicit `KICAD_BLOCKS_THEME=light|dark` override first, then the color theme read from Eeschema's IPC-reported settings directory. When KiCad does not expose a usable theme (for example, a standalone visual-test host), the shared resolver follows the desktop appearance preference on Windows, macOS, GNOME/GTK, or KDE. The palette is applied to both ttk and classic Tk controls, and every Treeview keeps compact 29 px rows with readable selected and striped states. This fallback does not change the separate open requirement to validate actual native desktop chooser rendering and cross-process window ownership.

The supported KiCad CI GUI tests run under `xvfb-run`; X11 tools may be used only in the test harness. Production code does not inject global input or inspect OS window IDs. Place Block uses schematic IPC to insert at page center, focus, select, and save. The facade pin-layout editor modifies only the project catalog and reloads the library; it does not pretend to be the native Symbol Editor. Cursor-attached movement and launching/selecting an exact library symbol in Symbol Editor remain upstream IPC gaps.

File and directory selection now goes through Tk's standard `filedialog.askopenfilename`, `askdirectory`, and `asksaveasfilename` APIs, as required by `AGENTS.md`; the plugin no longer launches separate Zenity/KDialog processes or maintains a custom file browser. Unit tests verify the arguments passed to those APIs, including initial file/directory, filters, parent, save suffix and cancellation. This does not prove that the chooser is visually native, portal-backed or grouped with KiCad on the target desktop. The actual Ubuntu 26 Wayland session, Tk version, desktop portal/theme, parent ownership and returned path must still be recorded in an interactive test.

## Completion gate

Do not mark the UI review complete until:

- Every production window above has a fresh light/dark screenshot inspected at a normal and constrained size.
- Every visible primary button has a test proving the intended side effect or its correct disabled/prerequisite state.
- Live Eeschema testing confirms page-aware center placement, focus, selection, and save. The Facade Pin Layout editor confirms project-catalog geometry writes, conflict protection, and library reload. Cursor-attached placement and full native Symbol Editor launch require upstream KiCad IPC additions.
- Ubuntu Wayland behavior has been exercised on a real Wayland session; Xvfb is not accepted as a substitute.
- File chooser open/save/directory/cancel/filter/path cases have been run against the actual desktop portal/theme.
- The entire CI suite passes, including documentation, KiCad CLI, editor GUI, PCB IPC, visual audit, and supported Python compatibility checks.
