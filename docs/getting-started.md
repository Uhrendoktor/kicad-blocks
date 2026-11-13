# Getting started

This page takes you from a supported KiCad setup to placing a block in a project. For a specific task after installation, use the [workflow guides](workflows.md).

## Supported environment

The currently validated environment is:

| Component | Validated target |
| --- | --- |
| KiCad | **10.99.0 development nightly** (development build for KiCad 11) |
| Operating system | Linux |
| Architecture | x86_64 |
| Python | CPython 3.12, 3.13 or 3.14 |
| Python modules | Tkinter and virtual-environment support |
| KiCad API | Enable the IPC API in Eeschema; also enable it in PCB Editor for PCB-layout actions |

Stable KiCad 10.x, KiCad 11 stable, Windows, macOS, ARM and other Python versions are not validated. The installer's version-directory check is not a compatibility guarantee.

KiCad Blocks uses the newer schematic IPC interface. The older KiCad 9/10 add-on API is limited to PCB Editor and is not sufficient for the schematic commands.

## Install the current development version

A packaged KiCad Blocks release ZIP is not published yet. For now, install from a Git clone:

```bash
git clone https://github.com/Uhrendoktor/kicad-blocks.git
cd kicad-blocks
python3.14 scripts/install_plugin.py --version 10.99
```

Use `python3.12`, `python3.13` or `python3.14` to match the interpreter you will select in KiCad. The installer copies the plugin from `plugin/kicad-blocks` into the user's KiCad IPC add-on directory. To preview the destination without installing, add `--dry-run`. Pass `--documents-home PATH` to override the XDG data directory.

Check [Releases](https://github.com/Uhrendoktor/kicad-blocks/releases) for a packaged installer when one becomes available. Do not treat GitHub's automatically generated source-code ZIP as a prebuilt plugin package.

## Enable the KiCad API

In Eeschema:

1. Open **Preferences → Plugins**.
2. Enable **Enable KiCad API**.
3. Set **Path to Python interpreter** to the supported CPython executable used for installation.
4. Keep KiCad's **Add Symbol** shortcut bound to `A`. KiCad Blocks uses the native symbol-placement command after you choose a block.

Restart KiCad after installation. Eeschema should show these toolbar actions:

- **Browse and Place Functional Blocks**
- **Create Block from Selected Sheet**
- **Open Block Implementation**

The remaining commands are grouped under **Tools → External Plugins → KiCad Blocks Tasks**. They are arranged in three tabs: **Use & build**, **Edit & maintain** and **Checks & PCB**.

Enable the KiCad IPC API in PCB Editor as well when you want to apply a stored PCB layout or place an imported layout group.

## Choose a block library

On Debian or Ubuntu, install the matching interpreter's Tkinter and virtual-environment packages if they are not already present (often named `python3.14-tk` and `python3.14-venv`; use the package names for your Python minor version).

The default shared library folder is:

```text
~/Documents/KiCad/Blocks/
```

Open **Manage library folders…** from the block browser to register a folder that already contains blocks, create a new empty library, or change the default destination.

Registering a folder does not move its files. Unregistering it does not delete it.

To change the default destination for newly created blocks, set this environment variable before launching KiCad:

```bash
export KICAD_BLOCKS_LIBRARY="$HOME/my-kicad-block-library"
```

A shared library can be a normal folder that you distribute with Git, a shared drive or an archive. Once the folder is registered, browse its blocks from any project.

## Place a block

1. Open a saved KiCad project in Eeschema.
2. Choose **Browse and Place Functional Blocks**.
3. Search by block name, description, pin name, parameter or implementation variant.
4. Select a result to inspect its pins, description and available configurations.
5. Choose a variant or profile if the block defines them.
6. Choose **Place at cursor** or double-click the result.
7. KiCad's native **Choose Symbol** dialog opens with the facade selected. Confirm it and place the symbol as usual.

The project receives a local symbol catalog under `.kicad_blocks/`. The shared library remains in its original folder; you do not need to copy its implementation sheets into the project.

The screenshot below shows the STUSB4500 example selected in the placement chooser, with a 5 V variant and a named PDO profile selected.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-profile.png" alt="STUSB4500 block selected in the placement chooser with the 5 V implementation and 5 V / 2.4 A profile" width="900">
</p>

## Create a block from your own sheet

1. In the parent schematic, select the hierarchical sheet symbol you want to package. You can also open that sheet directly and run the command with nothing selected.
2. Choose **Create Block from Selected Sheet**.
3. Enter a name and description.
4. Choose the shared library destination.
5. Open the new block in the browser and review its facade and implementation.

KiCad Blocks copies the selected sheet and its child sheets into the library. It leaves the source project untouched. The selected sheet's root hierarchical labels become the external pins of the facade symbol; labels on child sheets remain internal.

Continue with [Create and maintain blocks](workflows.md#create-a-block-from-a-hierarchical-sheet) for publishing changes, creating local copies and composing nested blocks.

## Troubleshooting

### The plugin actions do not appear

Confirm that the plugin is installed in the KiCad version directory you launched, that the IPC API is enabled in Eeschema, and that KiCad is using the supported CPython interpreter. Restart KiCad and inspect the plugin log if the actions are still missing.

### KiCad reports that the Python wheel is unavailable

The validated wheel is specific to Linux x86_64 and CPython 3.12, 3.13 or 3.14. Check the interpreter selected in **Preferences → Plugins**. KiCad manages an isolated environment for the add-on; do not install the same dependencies into that environment manually.

If the managed environment is broken, close KiCad and remove the plugin environment below:

```text
~/.cache/kicad/<version>/python-environments/org.uhrendoktor.kicadblocks
```

Reopen KiCad and allow it to recreate the environment. You can also right-click a plugin action in **Preferences → Plugins** and choose **Recreate Plugin Environment**.

### The browser opens but shows no blocks

Open **Manage library folders…** and check that a library folder is registered. A valid library contains a `KBlocks.kicad_sym` catalog and one or more `.kicad_block` directories. Use **Validate registered libraries** to find missing files or invalid block manifests.

### PCB layout actions are unavailable

The block must contain a stored `.kicad_pcb` layout. Enable the IPC API in PCB Editor, apply the layout to create a separate board copy, then use **Place Imported Block Layout**. The destination board must contain the layers used by the stored layout.

## Next

- [Create, configure and maintain blocks](workflows.md)
- [See the STUSB4500 example and generated images](../examples/stusb4500/README.md)
- [Look up exact fields, files and limitations](reference.md)
