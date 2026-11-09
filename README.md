# KiCad Encapsulated Blocks

A KiCad 11 development/nightly schematic IPC plugin that creates a **symbol-like hierarchical block**: one sheet symbol with named electrical pins on the parent schematic, backed by a separate editable child schematic containing matching hierarchical labels.

> **Compatibility:** schematic IPC editing and schematic plugin actions are KiCad 11 features. KiCad 10 and earlier cannot run this action in Eeschema. KiCad 11 schematic IPC is still under active development. This MVP has passed its pure-Python tests, but has not yet been exercised in a live KiCad desktop session; treat the first run as an integration test and keep a project backup.

## MVP scope

- Create a new block from a name and comma-separated typed pins.
- Place a native hierarchical sheet symbol at a user-entered coordinate.
- Create a child `.kicad_sch` under `.kicad_blocks/` with one matching hierarchical label per pin.
- The child sheet is a normal schematic: add real components and wires there. KiCad's normal hierarchy, ERC and netlist model remains in use.

Not included yet: extracting a selected circuit, a browsable block library, linked/shared updates, automatic detaching, custom arbitrary symbol outlines, and bulk synchronization.

## Install (KiCad 11 nightly/development build)

1. Close KiCad.
2. Copy the **`plugin/kicad-blocks`** directory into the KiCad 11 user plugin directory:
   - Linux: `~/.local/share/KiCad/11.0/plugins/kicad-blocks`
   - Windows: `%USERPROFILE%\Documents\KiCad\11.0\plugins\kicad-blocks`
   - macOS: `~/Documents/KiCad/11.0/plugins/kicad-blocks`
3. Make sure the directory contains `plugin.json`, `main.py`, `requirements.txt`, and `kicad_blocks/`.
4. Start the KiCad 11 development build and open a project in the Schematic Editor. Enable the IPC API/plugin system in Preferences if it is not already enabled.
5. KiCad should create a plugin Python environment and install the dependency listed in `requirements.txt`. The dependency currently points to the official `kicad-python` `main` branch because the stable PyPI release does not yet contain the in-development schematic API.
6. Run **Create Encapsulated Block** from the Schematic Editor's plugin toolbar/action menu.

## Use

The action asks for:

- **Block name**, for example `Buck_Converter`.
- **Pins**, for example `VIN:input, GND:passive, VOUT:output, EN:input`.
- **Position**, the top-left corner in millimetres, for example `100,100`.

Supported pin types: `input`, `output`, `bidirectional` (alias `bidir`), `tristate`, and `passive`. A pin without a type defaults to `passive`.

After creation, the parent sheet contains a native sheet-backed block and the child implementation is saved under `.kicad_blocks/`. Double-click the sheet symbol to open the implementation sheet, then add normal KiCad symbols and wires. The parent pins and child hierarchical labels are matched by name.

The action uses the native `SheetSymbol`/`SheetPin` object model—not a second electrical facade symbol—so this MVP avoids duplicate pins in the netlist.

## Known limitations

- The visible interface is currently KiCad's standard rectangular sheet-symbol body, with the name and electrical ports. A future version can add a custom drawn body/graphics while preserving the same sheet object as the electrical and navigation backend.
- The child sheet is a fresh template; the plugin does not yet move an existing circuit into it.
- This first version creates one independent implementation sheet per placed block. It does not manage a reusable source library or propagate updates.
- Schematic IPC is experimental in the KiCad 11 development line. Pin orientation, child sheet path handling, ERC, netlist export and GUI navigation need a real KiCad 11 smoke test before production use.
- The dependency is fetched from the official KiCad bindings development branch. If KiCad's Python environment cannot access GitLab, install the current `kicad-python` development build manually in that plugin environment.

## Development

```bash
python -m pip install pytest ruff
ruff check plugin tests
pytest -q
python -m compileall -q plugin
```

## Architecture

- `plugin/kicad-blocks/kicad_blocks/model.py`: validates pin definitions and calculates block geometry.
- `plugin/kicad-blocks/kicad_blocks/plugin.py`: connects to the active Eeschema document and creates a native `SheetSymbol` with `SheetPin`s.
- `plugin/kicad-blocks/kicad_blocks/sheet_file.py`: emits the child schematic with matching hierarchical labels and a hierarchical instance path.

The implementation uses KiCad's native hierarchy as the electrical backend. It deliberately does not introduce hidden global-net links or duplicate symbol pins.
