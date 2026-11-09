# KiCad Encapsulated Blocks

KiCad Blocks is an IPC schematic plugin for **black-box, library-backed schematic symbols**. A placed block is one ordinary KiCad symbol with external pins. Its implementation remains in a separate `.kicad_block` library asset; the plugin does not create a child sheet or copy the internal circuit into the project schematic.

## MVP behavior

- Create a block from an external pin interface (`NAME:type`). The plugin generates a normal `.kicad_sym` facade and a separate editable `implementation.kicad_sch` document.
- Register the block's symbol library in the current project's `sym-lib-table`, reload it through KiCad's IPC API, and place the facade symbol in the active schematic.
- Place existing blocks from the configured library.
- Convert a selected library-linked instance into an independent project-local copy. The plugin changes the selected instance's library identifier without replacing its schematic UUID or coordinates, so existing wire connections are intended to remain attached.
- Synchronize a selected local copy from its source. Unchanged files are updated; local-only edits are preserved; conflicting edits are rejected without overwriting either side. An upstream external-pin interface change is rejected for manual migration.
- Edit a block's separate implementation in another Eeschema window; publish its changed files to advance the library revision.
- Add nested block dependencies to the library manifest. Dependencies are stored as a graph and cycles are rejected; no hierarchical sheets are created.
- Optionally store a pre-routed `.kicad_pcb` file with the block asset.

## Important functional boundary

The facade symbol is what KiCad sees in the parent schematic's native ERC, netlist and BOM. The internal components in `implementation.kicad_sch` are a separate document and are **not** automatically included in those project-level outputs. The MVP does not yet provide a merged block BOM, recursive internal ERC, or a PCB-layout application/import operation. Nested dependencies are recorded and cycle-checked, but inserting a child implementation into a parent implementation is not yet automated. An optional PCB layout is stored and versioned with the block, but is not yet merged into the active board. These are not hidden behind a fake native hierarchy.

The internal schematic is editable: open the `implementation.kicad_sch` file from the block's library directory in Eeschema. It starts with global interface labels matching the facade pin names. Internal circuitry can be built with ordinary KiCad symbols and wires.

## Requirements

- KiCad 11 nightly/development build with its Schematic Editor IPC API enabled (Preferences → Plugins).
- Python runtime managed by KiCad for the plugin, with Tkinter available.
- The KiCad `kicad-python` development bindings, installed from the official GitLab `master` branch via `requirements.txt`. The new schematic API is still under development and is not included in the released PyPI package version used here.

Schematic placement, selection and library reload APIs used here are part of KiCad's newer schematic IPC interface. This plugin does not support the old SWIG-only plugin interface.

## Install

1. Copy `plugin/kicad-blocks` into the KiCad user plugin folder. Typical locations:
   - Linux: `~/.local/share/KiCad/11.0/plugins/kicad-blocks`
   - Windows: `%USERPROFILE%\\Documents\\KiCad\\11.0\\plugins\\kicad-blocks`
   - macOS: `~/Documents/KiCad/11.0/plugins/kicad-blocks`
2. Enable the IPC API in KiCad Preferences → Plugins.
3. Restart KiCad and enable the **KiCad Blocks Manager** schematic action.
4. Use the action and enter one of `create`, `place`, `local`, `sync`, `edit`, `publish`, or `nest`.

The default shared library location is `~/Documents/KiCad/Blocks`. The create/nest action lets you select another directory; the selection is remembered in `~/.config/kicad-blocks/settings.json`. Set `KICAD_BLOCKS_LIBRARY` to override it. Local symbol-library entries use `${KIPRJMOD}` paths so project-local block copies remain portable when the project folder moves.

## Pin definitions

Example:

```text
VIN:input, GND:passive, VOUT:output, EN:input
```

Supported electrical types: `input`, `output`, `bidirectional` (`bidir` alias), `tristate`, and `passive`. Pins are numbered in the order entered.

## On-disk format

Each block is stored as a folder ending in `.kicad_block`:

```text
Buck_Converter_<id>.kicad_block/
  manifest.json
  block.kicad_sym
  implementation.kicad_sch
  layout.kicad_pcb        # optional
```

A local copy is stored under the project directory:

```text
<project>/.kicad_blocks/local/<block>_<local-id>.kicad_block/
```

Its manifest records the source block, source revision and per-file sync baseline. Local copies are not silently overwritten when both the library and local version have changed the same file.

## Development and verification

Run unit tests and syntax checks:

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests
```

The GitHub Actions workflow installs KiCad CLI, exports the generated facade symbol and implementation schematic to SVG, and runs `pytest`/`ruff`. That checks KiCad's native parsers for generated files; it does not substitute for a live KiCad 11 GUI/IPC session test of the instance-localization and synchronization actions.
