# Install and enable KiCad Blocks

This guide installs the current source version, enables the KiCad IPC plugin, and registers a shared library. For a complete first-use walkthrough, continue with the [first-block tutorial](tutorials/first-block.md).

## Check the supported environment

The current full integration target is KiCad **10.99.0 development nightly**, Linux **x86_64**, and **CPython 3.12**. The plugin uses the Schematic Editor IPC API, Tkinter dialogs, and a native symbol-placement handoff.

| Component | Current evidence |
| --- | --- |
| KiCad | 10.99.0 development nightly — full Eeschema, schematic IPC, PCB IPC, and CLI integration |
| Operating system and architecture | Linux x86_64 |
| CPython 3.12 | Full unit, GUI, plugin, schematic, and PCB integration |
| CPython 3.13 and 3.14 | Plugin discovery and managed-environment smoke tests on Linux x86_64; not the full KiCad integration matrix |
| Tkinter | Required by the plugin's dialogs |
| `xdotool` | Required for the Linux native Add Symbol focus and placement handoff |
| KiCad API | Enable the IPC API in Eeschema; enable it in PCB Editor for PCB actions |

Stable KiCad 10.x does not provide the Schematic Editor IPC plugin surface used by KiCad Blocks. KiCad 11 adds that surface, but a stable KiCad 11 build has not yet been validated by this repository's full CI matrix. Windows, macOS, ARM, and non-CPython-3.12 full integration are also unvalidated. See [compatibility and test coverage](explanation/compatibility.md) for the evidence and boundaries.

## Install from a Git checkout

A KiCad Blocks 1.1.0 application ZIP is not currently published. Install the plugin from the source repository:

~~~bash
git clone https://github.com/Uhrendoktor/kicad-blocks.git
cd kicad-blocks
python3.12 scripts/install_plugin.py --version 10.99
~~~

The installer validates the Python runtime and target directory, checks the plugin manifest and entrypoints, stages the plugin, and replaces an existing installation atomically. It prints the exact destination path.

To inspect the destination without changing files, run:

~~~bash
python3.12 scripts/install_plugin.py --version 10.99 --dry-run
~~~

Use `python3.13` or `python3.14` only when you understand the coverage distinction: those interpreters have plugin-discovery smoke-test coverage, while the full KiCad integration target remains CPython 3.12.

## Enable the IPC plugin

1. Start the KiCad 10.99 development nightly build.
2. In Eeschema, open **Preferences → Plugins**.
3. Enable the KiCad API for the Schematic Editor.
4. Set **Path to Python interpreter** to the supported CPython executable. Use the same interpreter minor version that you used to run the installer.
5. Confirm that the system has Tkinter available. KiCad Blocks uses KiCad's native IPC actions to open the Symbol Editor and Place Symbol chooser; no X11 automation utility or particular Add Symbol shortcut is required.
7. Restart KiCad so the plugin environment can be created and its actions can be registered.

The plugin identifier is `org.uhrendoktor.kicadblocks`. KiCad creates a managed Python environment for the plugin and installs the pinned dependencies there. Do not manually install dependencies into that managed environment during normal setup.

The common Eeschema toolbar actions are:

- **Browse and Place Functional Blocks**
- **Create Block from Sheet**
- **Open Block Implementation**

Other operations are grouped under **Tools → External Plugins → KiCad Blocks Tasks** in the **Use & build**, **Edit & maintain**, and **Checks & PCB** tabs. The PCB Editor also provides **Place Imported Block Layout** for an already merged layout group.

Enable the KiCad API in PCB Editor as well when you need to import or position PCB layouts.

## Register a shared library folder

The default destination for newly created blocks is:

~~~text
~/Documents/KiCad/Blocks/
~~~

Open **Browse and Place Functional Blocks**, then choose **Manage Libraries**. The manager can:

- **Add library** — register an existing folder.
- **Create library** — create an empty library folder.
- **Change folder** — change the registered path.
- **Unregister** — remove a folder from the registered list without deleting files.
- **Set default** — choose the destination for newly created blocks.

Registering a folder does not move its contents. Unregistering a folder does not delete it.

To override the default destination, set the environment variable before launching KiCad:

~~~bash
export KICAD_BLOCKS_LIBRARY="$HOME/my-kicad-block-library"
~~~

The browser searches all registered roots. A valid library contains a `KBlocks.kicad_sym` facade catalog and one or more `.kicad_block` block directories.

## First-run checklist

Before creating or placing a block, confirm that:

- The correct KiCad 10.99 nightly build is running.
- The Schematic Editor API is enabled.
- The selected CPython interpreter is supported and has Tkinter.
- `xdotool` is available and the Add Symbol shortcut is still `A`.
- At least one shared library folder is registered, or the default destination is writable.

Continue with the [first-block tutorial](tutorials/first-block.md).

## Troubleshoot plugin startup

### Plugin actions do not appear

Wait for KiCad to finish creating the managed plugin environment, then restart KiCad. Confirm the interpreter path in **Preferences → Plugins** and inspect KiCad's warning messages or API log for dependency-install errors.

### The managed environment needs to be recreated

Close KiCad and remove the plugin's cached environment, then start KiCad again:

~~~text
~/.cache/KiCad/<version>/python-environments/org.uhrendoktor.kicadblocks
~~~

Alternatively, right-click a KiCad Blocks action in the plugin preferences and choose **Recreate Plugin Environment**.

The path above uses KiCad's default Linux cache directory. The directory name is
case-sensitive (`KiCad`, not `kicad`). If `KICAD_CACHE_HOME` is set, use that
custom cache root instead. See the [official KiCad add-on developer guide](
https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
for the platform-specific cache locations.

### Capture startup diagnostics

Use these steps when the plugin environment is created but its actions still fail to load:

1. Check KiCad's warning messages in the editor status bar. On KiCad 10.0.1 and
   later, plugin action standard output and error output are reported there.
2. To enable API trace output, set `KICAD_ENABLE_WXTRACE=1` and
   `WXTRACE=KICAD_API` before launching KiCad.
3. To record API requests and responses, add `EnableAPILogging=1` to the
   `kicad_advanced` file in the KiCad configuration directory.
4. Reproduce the issue and inspect
   `${KICAD_DOCUMENTS_HOME}/<version>/logs/api.log`.
5. Remove `EnableAPILogging=1` when finished. The API log can grow large.

See the [KiCad IPC API debugging instructions](
https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
for the current logging details.

### The block browser opens but contains no blocks

Use **Manage Libraries** to check registered folders. Select a library that contains a `KBlocks.kicad_sym` catalog and `.kicad_block` directories. Run **Validate registered libraries** to inspect manifest and asset errors.

### Native symbol placement cannot take focus

On Linux, install `xdotool` and make sure Eeschema is still open. Keep the default Add Symbol shortcut bound to `A`. The browser cannot safely type the facade name into the native chooser if it cannot identify and focus the Eeschema window.

### PCB actions are unavailable

The block must contain a stored `.kicad_pcb` layout. Enable the IPC API in PCB Editor. The destination board must contain all layers used by the stored layout.
