# KiCad Encapsulated Blocks

An early KiCad 11 nightly/development IPC plugin for creating **sheet-backed schematic blocks**. A block appears as one hierarchical sheet symbol with named electrical ports; its implementation is a normal editable child schematic.

This uses KiCad's native hierarchy and net connectivity. It does not create a duplicate facade symbol, hidden global nets, or an alternate netlist.

## What this MVP does

- Prompts for a block name, external pins and placement coordinates.
- Creates one native hierarchical sheet symbol with input/output/bidirectional/tristate/passive ports.
- Creates the matching hierarchical labels in the child schematic using KiCad's schematic IPC API.
- Uses KiCad's own document model to create and save the child sheet file.
- Keeps the child implementation as an ordinary schematic that can contain normal components, wires and labels.
- Double-click the sheet symbol to open the internal implementation.

## Requirements

- **KiCad 11 nightly/development build.** KiCad 9 and 10 do not expose the Schematic Editor plugin actions and sheet-editing API used here.
- Enable the KiCad IPC API in Preferences → Plugins.
- Python interpreter selected for KiCad's plugin environment must have Tkinter available for the simple input dialogs. On Debian/Ubuntu this may require `python3-tk`.
- The plugin installs the current `kicad-python` development bindings from the official KiCad GitLab repository because the in-development schematic types are not yet part of every stable PyPI release.

## Install

1. Download or clone this repository.
2. Copy the **`plugin/kicad-blocks`** folder into the KiCad 11 user plugin directory:
   - Linux: `~/.local/share/KiCad/11.0/plugins/kicad-blocks`
   - Windows: `%USERPROFILE%\Documents\KiCad\11.0\plugins\kicad-blocks`
   - macOS: `~/Documents/KiCad/11.0/plugins/kicad-blocks`
3. Ensure the folder contains `plugin.json`, `requirements.txt`, `main.py`, and `kicad_blocks/`.
4. Start KiCad 11 nightly, open a saved project schematic and enable the IPC API.
5. Wait for KiCad to create the plugin environment and install `requirements.txt`.
6. Run **Create Encapsulated Block** from the Schematic Editor plugin actions.

## Use

Example pin definition:

```text
VIN:input, GND:passive, VOUT:output, EN:input
```

Pin types: `input`, `output`, `bidirectional` (or `bidir`), `tristate`, and `passive`. A pin without a type defaults to passive. Up to 32 pins are supported.

The plugin creates a file such as `buck_converter_block_01.kicad_sch` in the project directory. The parent symbol uses a hidden filename field and displays its instance name and pin interface. The child sheet contains the corresponding hierarchical labels, ready to connect to the internal circuit.

## Current scope and limitations

- The graphical body is KiCad's native rectangular sheet symbol. Arbitrary custom symbol outlines are not implemented yet.
- The plugin creates a fresh implementation sheet; circuit extraction from a selected region is not implemented.
- Every placement gets its own child implementation file. Shared block libraries and linked-instance synchronization are not implemented.
- The current KiCad 11 schematic API is experimental and can change before KiCad 11 is released.
- This repository's automated tests mock the IPC object model. A live Eeschema integration run is still required to confirm behavior with the user's exact KiCad nightly build and plugin runtime.

## Development checks

```bash
python -m pip install pytest ruff
ruff check plugin tests
pytest -q
python -m compileall -q plugin
```

The test suite validates pin parsing and the plugin's IPC call sequence with test doubles. It does not replace an actual KiCad GUI/CLI integration test.
