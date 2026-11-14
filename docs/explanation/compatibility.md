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
| Windows | Not validated; no matching published KiCad Python IPC wheel in this project |
| macOS | Not validated; no matching published KiCad Python IPC wheel in this project |
| Linux ARM or other architectures | Not validated; no matching published wheel |
| Non-CPython interpreters or other Python minor versions | Rejected by the installer |

The production plugin uses Tkinter and its standard file-selection dialogs, and invokes Symbol Editor and Place Symbol through KiCad IPC. Final symbol selection is performed in KiCad's native UI. `xdotool` is confined to the Linux/X11 GUI test harness; it is not a production dependency or runtime fallback. The plugin no longer probes `/proc`, Windows registry, macOS defaults, GNOME settings, KDE configuration files, X11, or Wayland to implement product behavior.

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
