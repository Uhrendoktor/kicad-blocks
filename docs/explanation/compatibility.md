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

The post-merge main-branch CI run [#43521663508](https://github.com/Uhrendoktor/kicad-blocks/actions/runs/43521663508) passed both `python-and-kicad` and `visual-ui-audit` on merge commit `d75f0b1f0ef59e14868a84e7730cbc517089e0b7`. It exercised unit tests, lint, KiCad CLI validation, Eeschema GUI editing and publishing, plugin discovery, schematic and PCB IPC, layout merging, and rendered UI evidence.

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

The current supported KiCad schematic API does not provide the editor operations needed for full Place Block and Edit Facade workflows:

| API | Documented behavior | Why it is insufficient |
| --- | --- | --- |
| `Schematic.place_symbol_from_library(lib_id, position, ...)` | Creates a symbol at explicit schematic coordinates | Does not start the interactive placement cursor by itself. |
| `EditorCommandsHandler.add_to_selection(items)` | Adds specified items to the active Eeschema selection | Selects the symbol but does not initiate movement or attach it to the pointer. |
| `Board.interactive_move(items)` | Selects board items and starts the native interactive move tool | PCB-only API; Eeschema has no `Schematic.interactive_move(items)` equivalent in the current server. |
| Eeschema `SCH_ACTIONS::placeSymbol` and `SCH_ACTIONS::move` | Native Add Symbol and Move tools | Both tools exist in Eeschema, but Eeschema does not register the generic `RunAction` IPC handler needed to invoke them directly. |
| `API_HANDLER_SYMBOL::OpenLibraryItem` | Loads a specific library item into Symbol Editor using a typed `LibraryIdentifier` | Registered by Symbol Editor and usable through the underlying protobuf request even though the current high-level Python wrapper only exposes footprint loading. |
| `API_HANDLER_SYMBOL::GetOpenDocuments` | Reports the active symbol documents and exact library IDs | Used to verify that `KBlocks:<symbol>` actually loaded; window title alone is not treated as proof. |
| `KiCad.run_action(action)` | Runs an arbitrary KiCad tool action where the editor registers its handler | The generic handler is registered in PCB Editor, not Eeschema; Eeschema returns `no handler available for request of type kiapi.common.commands.RunAction`. |

The supported Python wrapper is documented in the [KiCad Python KiCad class](https://docs.kicad.org/kicad-python/kicad.html) and [Schematic class](https://docs.kicad.org/kicad-python/schematic.html). The IPC transport is cross-platform, but Eeschema's current command handler does not expose interactive move or generic tool dispatch. The plugin therefore uses the native desktop interaction layer only to start KiCad's own editor tools; the exact library object is still loaded and verified through IPC.

For placement, the plugin activates Eeschema's native **Add Symbol** tool, supplies the fully qualified `KBlocks:<symbol>` identifier to KiCad's chooser, and returns control with the symbol attached to the cursor. The user moves it and clicks to place, or presses Escape to cancel. For facade editing, the plugin opens Symbol Editor through the native Tools menu and then calls `OpenLibraryItem` directly, verifying the exact active `KBlocks:<symbol>` entry through `GetOpenDocuments`.

Platform input adapters are deliberately isolated: `xdotool` targets the verified Eeschema window on X11; `wtype` sends virtual-keyboard events on Wayland; `pynput` supplies keyboard events on Windows and macOS; macOS uses System Events to select the named Symbol Editor menu item. Windows/macOS may require Accessibility permission, and Wayland compositors must permit the virtual-keyboard protocol. These adapters start native KiCad tools only; they do not edit schematic files directly or emulate the move geometry.

### Cross-process window ownership

Tk's portable transient relationship is used only between windows created by the same plugin process. KiCad IPC does not currently expose a portable native window handle or foreign-window ownership API, so the plugin does not attempt to force its separate top-level window into KiCad's taskbar/dock group. Cross-process ownership, stacking, and parent activation remain unverified requirements; X11-specific parenting is not an acceptable substitute.

## Packaging state

The repository `VERSION` file currently contains `1.1.0`. A packaged KiCad Blocks 1.1.0 application ZIP is not yet published in GitHub Releases. The published release assets currently provide the pinned KiCad Python IPC dependency wheels, not a complete user-installable KiCad Blocks release archive.

Use the source installation procedure in [Install and enable KiCad Blocks](../getting-started.md). The immutable historical `v1` tag is not a substitute for a current 1.1.0 application package.

## What CI does not prove

Passing CI is evidence for the scenarios exercised by the current tests. It does not establish compatibility with untested KiCad releases, operating systems, architectures, or hotkey configurations.

Known coverage gaps include:

- A complete live Symbol Editor save/edit/publish GUI sequence is not automated.
- The PCB imported-group action is tested, but the full mouse-driven commit/cancel sequence is not automated end to end.
- Recursive extraction is tested with a two-level fixture and KiCad CLI validation; complex real-world multi-sheet projects still warrant a manual review.
- Imported PCB layouts must be reviewed in PCB Editor. Required board layers must exist, unrelated global board setup is not copied, and the merge creates a separate board copy.
