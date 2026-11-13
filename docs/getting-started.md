# Getting started

This guide takes you from environment checks to the first placed functional block. Read the compatibility section before installing anything.

## Supported environment

V1.1.0 has been validated against this target:

| Component | Validated target |
| --- | --- |
| KiCad | **10.99.0 development nightly** (pre-release development build for KiCad 11) |
| Operating system | Linux |
| Architecture | x86_64 |
| Python runtime | CPython 3.12, 3.13 or 3.14 |
| UI dependency | Tkinter available to the selected Python interpreter |
| IPC | Enable the KiCad IPC API in Eeschema; enable it in PCB Editor if using imported-layout placement |

The following are **not validated/supported by this release**: stable KiCad 10.x, KiCad 11 stable, Windows, macOS, ARM and Python versions/ABIs outside CPython 3.12–3.14. The installer accepting a KiCad 11.0+ version-directory name is not a compatibility claim.

KiCad 9/10 IPC add-ons are limited to PCB Editor. The schematic actions used by KiCad Blocks require the newer schematic IPC interface and do not use the older SWIG-only plugin API.

## Before installation

1. Install the validated KiCad 10.99.0 development-nightly build.
2. Install a matching CPython 3.12, 3.13 or 3.14 interpreter. Ensure it includes Tkinter and virtual-environment support.
3. In Eeschema, open **Preferences → Plugins** and enable **Enable KiCad API**.
4. Set **Path to Python interpreter** to the same supported CPython executable you intend to use. Do not rely on automatic detection if KiCad selects a different interpreter.
5. Keep Eeschema's native **Add Symbol** shortcut bound to `A`. The library browser uses that native shortcut to hand the chosen facade to KiCad's symbol-placement tool.

On Debian/Ubuntu, the matching interpreter's venv/Tk modules are commonly named along the lines of `python3.14-venv` and `python3.14-tk`; use the names available for the installed Python minor version. `xdotool` is used by the automated GUI tests and native placement handoff.

## Install from a release archive

The V1.1.0 installer is included in the Linux x86_64 release ZIP.

1. Open [GitHub Releases](https://github.com/Uhrendoktor/kicad-blocks/releases) and confirm that the V1.1.0 Linux x86_64 archive and checksum file are published. As of **13 November 2026**, V1.1.0 is present in the repository version file, but the installable release package has not yet been published.
2. Download the ZIP and its `.sha256` file. Verify the archive before extraction:

   ```bash
   sha256sum --check kicad-blocks-1.1.0-linux-x86_64.zip.sha256
   ```

   The command should report `OK`.
3. Extract the archive and enter the extracted `kicad-blocks-1.1.0-linux-x86_64` directory.
4. Run the installer with the supported interpreter. For CPython 3.14:

   ```bash
   python3.14 scripts/install_plugin.py --version 10.99
   ```

   Use `python3.12` or `python3.13` when that is the interpreter selected in KiCad. Pass `--documents-home PATH` to override the XDG data directory.
5. Restart KiCad. The plugin is installed below the versioned KiCad IPC add-on directory, using the identifier `org.uhrendoktor.kicadblocks`.

Do not manually pip-install the same requirements into KiCad's managed plugin environment. KiCad creates an isolated environment for the add-on and installs its declared binary wheel dependency.

## Enable the plugin in KiCad

After restarting, Eeschema exposes three standalone toolbar actions:

- **Browse and Place Functional Blocks**
- **Create Block from Selected Sheet**
- **Open Block Implementation**

The grouped **KiCad Blocks Tasks** command remains available under **Tools → External Plugins**. It opens the task hub with three tabs: **Use & build**, **Edit & maintain** and **Checks & PCB**.

In PCB Editor, enable the KiCad IPC API there as well. The separate **Place Imported Block Layout** action is used after a block layout has been merged into a project-board copy.

The current KiCad IPC action manifest does not expose a parent/group/submenu field. Therefore, the three common schematic operations are separate toolbar actions and the remaining operations are grouped inside the task hub; the plugin cannot create a custom nested Tools submenu.

## Choose a block-library folder

The default shared library location is:

```text
~/Documents/KiCad/Blocks/
```

Use **Manage library folders…** in the browser to register existing folders, create a new empty library, change a registered path, unregister a folder or choose the default destination for new blocks.

Changing a registered path does not move its files. Unregistering a library never deletes it. The browser lists blocks from all registered folders.

To override the default destination:

```bash
export KICAD_BLOCKS_LIBRARY="$HOME/my-kicad-block-library"
```

Registered roots and the default destination are stored in:

```text
~/.config/kicad-blocks/settings.json
```

Each shared folder contains an aggregate `KBlocks.kicad_sym` catalog and one directory per block. A project receives a combined catalog at `.kicad_blocks/KBlocks.kicad_sym`, so symbols from the registered libraries are available through one stable project-library row.

## First-use workflow

### Place an existing block

1. Open a saved KiCad project in Eeschema.
2. Select **Browse and Place Functional Blocks**.
3. Search by block name, description, pin name, parameter or declared variant.
4. Select a result and review its facade preview, connections, settings, implementation choices and layout options.
5. Choose a variant or named profile when the block defines them.
6. Select **Place at cursor** (or double-click a result). KiCad's native Add Symbol chooser opens with the block facade selected; confirm it and place the symbol normally.

The chooser's profile selection can apply several instance-local values together. The placed facade remains an ordinary KiCad symbol.

### Create a block from your own sheet

1. In the parent sheet, select one hierarchical sheet symbol; or open the target hierarchical sheet itself and leave nothing selected.
2. Click **Create Block from Selected Sheet**.
3. Enter the block name and description in the single form.
4. The plugin copies the implementation into the default shared library and derives external facade pins from root-sheet hierarchical labels.
5. Review the new block in the browser. Edit the implementation or facade as needed, then publish implementation changes.

Continue with the [feature workflows](workflows.md) for local copies, nested composition, recursive checks and PCB layout reuse.

## Troubleshooting

### KiCad reports that a required wheel is unavailable

Confirm that the configured interpreter is CPython 3.12, 3.13 or 3.14 on Linux x86_64 and that KiCad is using that exact executable. The plugin requirements select an ABI-specific prebuilt wheel; other interpreters will not match it.

If an earlier installation left a broken managed environment, close KiCad and remove the failed environment below:

```text
~/.cache/kicad/<version>/python-environments/org.uhrendoktor.kicadblocks
```

Reopen KiCad with the supported interpreter selected and let it recreate the environment. You can also right-click one of the plugin actions in **Preferences → Plugins** and choose **Recreate Plugin Environment**.

### Actions are missing

Check the plugin installation directory, ensure the IPC API is enabled in the relevant editor, review the KiCad plugin log and restart KiCad. Schematic actions are registered in Eeschema; imported-layout placement is scoped to PCB Editor.

### The browser opens but shows no blocks

Open **Manage library folders…** and confirm that at least one shared folder is registered and contains a valid `KBlocks.kicad_sym` catalog plus one or more `.kicad_block` directories. Library validation can identify malformed manifests, missing assets or broken dependencies.

### PCB layout placement is unavailable

The source block must include a stored `.kicad_pcb` layout. Enable the IPC API in PCB Editor, apply the layout to create a separate board copy, then use **Place Imported Block Layout**. The project board must already contain every layer used by the stored layout.
