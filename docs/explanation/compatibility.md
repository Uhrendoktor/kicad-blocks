# Compatibility and test coverage

This page separates the product's current test evidence from targets that the installer may recognize but that have not yet been validated by the repository's full integration suite.

## Current validated target

| Component | Full integration target |
| --- | --- |
| KiCad | 10.99.0 development nightly |
| Operating system | Linux |
| CPU architecture | x86_64 |
| Python | CPython 3.12 |
| GUI dependencies | Tkinter and native `filedialog`; Xvfb/xdotool are test-harness-only in Linux CI |
| KiCad interfaces | Eeschema IPC, PCB IPC, KiCad CLI |

The main-branch CI run [#43521663508](https://github.com/Uhrendoktor/kicad-blocks/actions/runs/43521663508) records the earlier full product baseline. The later exact-head validation [#44706326103](https://github.com/Uhrendoktor/kicad-blocks/actions/runs/44706326103), on test-code commit `aa4fc8d2d44e3ce9646b17647215047cd47d0c74`, passed all five jobs and verified a named Eeschema AT-SPI window on CPython 3.12, 3.13, and 3.14. This is a smoke test for the Eeschema application tree, not proof that the plugin's Tk windows or their individual controls are screen-reader accessible.

## Runtime dependency portability

The production runtime now uses cross-platform libraries and APIs for these shared feature paths:

| Dependency/API | Upstream platform support | Integration note |
| --- | --- | --- |
| Python Tkinter / ttk / `filedialog` | Windows, macOS, and Unix-like systems | The same toolkit API is used on all OS families. Native appearance and Linux portal integration still require real desktop verification. |
| `platformdirs` | Windows, macOS, Linux and other Unix-like systems | Resolves the host's standard user configuration directory; Linux keeps the existing XDG location. |
| `resvg_py` | Windows, macOS, Linux, Android | Prebuilt wheels for the supported project Python versions; Rust renderer is bundled in the wheel, with no system Cairo dependency. |
| Pillow | Windows, macOS, Linux | Cross-platform image API; actual wheel availability depends on the Python ABI and architecture. |
| KiCad Python IPC API | KiCad supports Windows, macOS, Linux | The IPC transport is cross-platform. This repository's current published Python IPC wheels are still Linux x86_64 only, as an explicit temporary packaging exception. |

Upstream references: [Python Tkinter](https://docs.python.org/3/library/tkinter.html), [platformdirs supported platforms](https://platformdirs.readthedocs.io/en/latest/index.html), [resvg_py supported OS/architecture and Python wheel matrix](https://resvg-py.readthedocs.io/en/latest/installation.html), [Pillow platform support](https://pillow.readthedocs.io/en/stable/), and [KiCad IPC API](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/).

This table documents upstream library capability, not full validated KiCad Blocks compatibility. A lightweight CI matrix now creates a real Tk root and runs the seven shared file/directory chooser-wrapper contracts on Windows and macOS. Those tests stub the OS chooser callbacks and verify initial paths, file filters, parent arguments, fallback behavior, and cancellation; they do not display or automate the native OS windows. Full KiCad editor/IPC integration remains Linux-only because this project currently publishes KiCad Python IPC wheels only for Linux x86_64. The IPC wheel packaging limitation is separate from the current KiCad editor-action API gap.

## Interpreter coverage

The supported Linux x86_64 IPC dependency wheels are built for CPython 3.12, 3.13, and 3.14.

The depth of validation differs by interpreter:

| Interpreter | CI coverage |
| --- | --- |
| CPython 3.12 | Full unit, KiCad CLI, Eeschema GUI, plugin discovery, schematic IPC, PCB IPC, and PCB layout integration |
| CPython 3.13 | Plugin discovery and managed-environment smoke test; wheel import |
| CPython 3.14 | Plugin discovery and managed-environment smoke test; wheel import |

Do not infer that the full KiCad integration matrix has been run on CPython 3.13 or 3.14 from the existence of those wheels or the installer accepting those interpreter versions.

## KiCad API boundary

The current KiCad add-on API documentation states that KiCad 9 and 10 support IPC add-ons in PCB Editor only, while KiCad 11 adds IPC add-ons in Schematic Editor. KiCad Blocks needs the Schematic Editor plugin surface, so stable KiCad 10.x is not a usable target for the schematic workflows.

As of **2026-11-14**, the latest stable release announcement is [KiCad 10.0.8, published 2026-10-30](https://www.kicad.org/blog/2026/10/KiCad-10.0.8-Release/). The validated 10.99.0 build is a development nightly, not a stable release. Although the installer accepts KiCad 11.0 or newer version directories, stable KiCad 11 has not yet been validated by this repository's full integration matrix.

Primary references:

- [KiCad IPC API status for add-on developers](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
- [KiCad 10.0.8 release announcement](https://www.kicad.org/blog/2026/10/KiCad-10.0.8-Release/)

## Platform coverage

| Target | Status |
| --- | --- |
| Linux x86_64 | Full integration target |
| Windows | Tk/file-dialog wrapper contracts run in CI; full KiCad integration not validated (no matching published IPC wheel) |
| macOS | Tk/file-dialog wrapper contracts run in CI; full KiCad integration not validated (no matching published IPC wheel) |
| Linux ARM or other architectures | Not validated; no matching published wheel |
| Non-CPython interpreters or other Python minor versions | Rejected by the installer |

The production plugin uses Tkinter and its standard file-selection dialogs. It does not use `/proc`, Windows registry, macOS defaults, GNOME settings, KDE configuration files, X11, or Wayland to implement product behavior. `xdotool` is confined to the Linux/X11 GUI test harness; it is not a production dependency or runtime fallback.

### KiCad editor-action API boundary

The current stable Eeschema IPC surface supports exact library-backed insertion, viewport focus, selection, and project-catalog editing. It does not expose the two operations needed for full native-interaction parity: attach a newly inserted schematic symbol to the cursor, or launch the GUI Symbol Editor for a specific library item.

| API | Supported behavior | Current product use / limitation |
| --- | --- | --- |
| `Schematic.place_symbol_from_library(lib_id, position, ...)` | Inserts the exact generated facade at an explicit coordinate | Place Block uses the active page center, snapped to KiCad's schematic grid. |
| `FocusOnItems` | Navigates to the owning sheet and centers specified KIIDs in Eeschema | Used immediately after insertion so the new block is visible. |
| `EditorCommandsHandler.add_to_selection(items)` | Adds specified items to the active Eeschema selection | Used to select the newly inserted block after it has been focused. |
| `Board.interactive_move(items)` | Selects board items and starts the native interactive move tool | PCB-only API. Eeschema has no `Schematic.interactive_move(items)` counterpart in the current server. |
| Eeschema `SCH_ACTIONS::placeSymbol` and `SCH_ACTIONS::move` | Native Add Symbol and Move tools | The tools exist, but Eeschema does not register the generic `RunAction` IPC handler. |
| `API_HANDLER_SYMBOL::OpenLibraryItem` | Loads a specified `LIB_ID` in an already-running Symbol Editor context | Does not create the GUI Symbol Editor frame. |
| `OpenDocument` / `open_document()` | Opens a document in a headless context | The Python API documents this as headless-only; it is not a visible Symbol Editor launch path. |

#### Current Place Block behavior

Place Block registers/refreshes the project's combined `KBlocks.kicad_sym` catalog, inserts the exact `KBlocks:<symbol>` facade at the active sheet's page center (including custom and portrait page dimensions), focuses it with `FocusOnItems`, selects the returned KIID, verifies that selection, and saves. This is the strongest supported cross-platform Eeschema IPC path currently available. The user can then adjust the selected symbol using Eeschema's normal Move command.

Cursor-attached placement still requires an upstream schematic equivalent of `Board.interactive_move(items)`. The expected API should resolve the supplied KIID, establish the reference point, select it, and transfer an active API commit to Eeschema's native move tool. Confirm should commit placement; Escape should roll it back. That keeps the pointer, snapping, grid, undo, and cancel behavior inside KiCad and avoids platform-specific input automation.

#### Current Edit Facade behavior

Edit Facade opens the portable Facade Pin Layout editor. It lists the generated symbol's pins and allows the user to adjust X/Y position, orientation, and length. Each accepted operation writes only the selected pin geometry to the project-local `KBlocks.kicad_sym` catalog, uses a SHA-256 optimistic concurrency check to reject stale edits, and reloads the project library. Pin names, numbers, and electrical types remain derived from the implementation's hierarchical labels. The normal **Publish** action imports the project edit with the repository's three-way conflict checks and writes a new shared-library revision.

This is not presented as the full KiCad Symbol Editor. Native artwork and other Symbol Editor operations still require a stable cross-platform Eeschema IPC request that creates/opens a GUI Symbol Editor document and loads an exact `LIB_ID`. The existing `OpenLibraryItem` request can load an item only after that editor context is open. The plugin does not disable Edit Facade, fake a Symbol Editor window, or use OS-specific keyboard/window automation.

The IPC transport is cross-platform, but the missing editor operations are API gaps in the Eeschema server rather than Linux/Wayland or Windows/macOS differences.

The Place Block implementation probes the connected schematic object for an `interactive_move` method at runtime. When the Python binding and Eeschema server both provide the schematic counterpart, it begins an API commit, inserts the facade at a temporary page-center coordinate, and transfers that same commit to the native move tool. Confirm/cancel then remain one undoable operation. If the method is absent or Eeschema returns an unsupported-handler error, the plugin pushes the same commit and completes the existing page-center/focus/select/save workflow. This fallback is intentional and cross-platform; the Place action is never disabled and no OS-specific input automation is used. The current tested KiCad nightly does not yet expose the method, so its actual placement remains page-center based.

### Cross-process window ownership

Tk's portable transient relationship is used only between windows created by the same plugin process. KiCad IPC does not currently expose a portable native window handle or foreign-window ownership API, so the plugin does not attempt to force its separate top-level window into KiCad's taskbar/dock group. Cross-process ownership, stacking, and parent activation remain unverified requirements; X11-specific parenting is not an acceptable substitute.

## Packaging state

The repository `VERSION` file currently contains `1.1.0`. A packaged KiCad Blocks 1.1.0 application ZIP is not yet published in GitHub Releases. The published release assets currently provide the pinned KiCad Python IPC dependency wheels, not a complete user-installable KiCad Blocks release archive.

Use the source installation procedure in [Install and enable KiCad Blocks](../getting-started.md). The immutable historical `v1` tag is not a substitute for a current 1.1.0 application package.

## Native file chooser evidence boundary

The plugin currently calls Tk's standard `filedialog` API. On Windows and macOS, Tk routes its chooser command to the platform file dialog implementation. The Ubuntu 24.04 GUI validation runner, however, uses the distribution's Tk 8.6.18 runtime. Tk's current Unix/X11 chooser is Tk's own dialog implementation, not an unconditional XDG portal call.

There is an upstream development path, but it must not be counted as shipped support yet: [TIP 763](https://core.tcl-lang.org/tips/doc/trunk/tip/763.md) is still Draft/Pending and proposes routing Tk's existing open/save/directory chooser commands through `org.freedesktop.portal.FileChooser` on X11. The [`x11-desktop-portal` Tk source branch](https://core.tcl-lang.org/tk/timeline?r=x11-desktop-portal) records implementation commits and says it was tested with KDE Plasma 6.6, GTK and GNOME 50 portal backends. This branch is not the current stable Tk dependency, and the proposal's stated scope is X11; do not infer Wayland/Hyprland parity from it.

GTK4 provides another candidate: `Gtk.FileDialog` (since GTK 4.10) supports asynchronous open/save/folder selection, filters and initial paths; GTK's native chooser API documents Windows `IFileDialog`, macOS `NSOpenPanel`/`NSSavePanel`, and use of `org.freedesktop.portal.FileChooser` when available. Its parent argument is a `GtkWindow`, while KiCad Blocks currently creates Tk windows. Adopting it requires a deliberate parent/ownership architecture, not a detached helper that opens an unowned dialog.

Until a portal-capable Tk build or an adopted GTK backend is integrated and exercised in real sessions, the product must continue to describe the Linux chooser path accurately and leave requirement R6 partial.

## Accessibility evidence boundary

Linux CI runs the Eeschema plugin-discovery workflow inside a private D-Bus session, enables the AT-SPI bus and GTK ATK bridge before Eeschema starts, then resolves Eeschema's X11 client PID from its editor window and inspects the process through `xa11y`. The exact-head run [#44710888957](https://github.com/Uhrendoktor/kicad-blocks/actions/runs/44710888957), on `376bfeb2747ecf5d9150971c3b15a21c029581df`, passed this check on CPython 3.12, 3.13, and 3.14; each run exposed a named top-level Eeschema `window`.

The same integration test also attempts a non-blocking probe of the Python process that owns the Facade Pin Layout Tk window. In this run, xa11y reported `SelectorNotMatchedError` for the Tk PID and listed only the Eeschema process as an AT-SPI registry candidate. The Tk window therefore did not expose an application tree to this AT-SPI session. This is consistent with the separate Tk 8.6 runtime; the Eeschema wxWidgets tree is not a proxy for plugin Tk controls. The result does not audit individual Eeschema controls, keyboard navigation, or screen-reader usability. Ubuntu 24.04's packaged Tk 8.6 does not implement TIP 733's `tk accessible` API; issue #41 tracks evaluating a Tcl/Tk 9.1-enabled Python runtime and the platform-specific screen-reader checks still required.

## What CI does not prove

Passing CI is evidence for the scenarios exercised by the current tests. It does not establish compatibility with untested KiCad releases, operating systems, architectures, or hotkey configurations.

Known coverage gaps include:

- A complete live Symbol Editor save/edit/publish GUI sequence is not automated.
- The PCB imported-group action is tested, but the full mouse-driven commit/cancel sequence is not automated end to end.
- Recursive extraction is tested with a two-level fixture and KiCad CLI validation; complex real-world multi-sheet projects still warrant a manual review.
- Imported PCB layouts must be reviewed in PCB Editor. Required board layers must exist, unrelated global board setup is not copied, and the merge creates a separate board copy.
