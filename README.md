# KiCad Encapsulated Blocks

**Project tracker:** [MVP tracking issue #1](https://github.com/Uhrendoktor/kicad-blocks/issues/1). Requirements, decisions, verification evidence, and remaining work are maintained there.

KiCad Blocks is an IPC schematic plugin for **black-box, library-backed schematic symbols**. A placed block is one ordinary KiCad symbol with external pins. Its implementation remains in a separate `.kicad_block` library asset; the plugin does not create a child sheet or copy the internal circuit into the project schematic.

## MVP behavior

- Create a block from an external pin interface (`NAME:type`). The plugin generates a normal `.kicad_sym` facade and a separate editable `implementation.kicad_sch` document.
- Register the block's symbol library in the current project's `sym-lib-table`, reload it through KiCad's IPC API, and place the facade symbol in the active schematic.
- Place existing blocks from the configured library.
- Convert a selected library-linked instance into an independent project-local copy. The plugin changes the selected instance's library identifier without replacing its schematic UUID or coordinates, so existing wire connections are intended to remain attached.
- Synchronize a selected local copy with its library source in both directions. Local-only edits are published, library-only edits are pulled, identical concurrent edits converge, and divergent edits are rejected before either side is changed. External pin/interface changes require an explicit migration.
- Edit a block's separate implementation in another Eeschema window; publish its changed files to advance the library revision.
- Add nested block instances to the library manifest. Each occurrence has a unique instance ID, reference, placement and child-pin-to-parent-net mapping. The plugin composes child facade symbols and global labels into the parent library implementation schematic; multiple instances of the same child are supported. Dependencies use library-relative paths, resolve by block ID and pinned revision, and cycles are rejected without creating hierarchical sheets.
- Optionally store a pre-routed `.kicad_pcb` file with the block asset.

## Important functional boundary

The facade symbol is what KiCad sees in the parent schematic's native ERC, netlist and BOM. The internal components in `implementation.kicad_sch` are a separate document and are **not** automatically included in those project-level outputs. The plugin provides recursive BOM export and recursive internal ERC: each dependency occurrence's implementation schematic is processed by KiCad CLI and reports/BOM rows are aggregated. Nested child facade instances are composed inline into the parent library implementation schematic, with their pins wired through the declared net map; each child implementation remains a separate reusable library asset. An optional PCB layout is stored and versioned with the block, but is not yet applied to the active board. These are not hidden behind a fake native hierarchy.

The internal schematic is editable: open the `implementation.kicad_sch` file from the block's library directory in Eeschema. It starts with global interface labels matching the facade pin names. Internal circuitry can be built with ordinary KiCad symbols and wires.

## Requirements

- KiCad 11 nightly/development build with its Schematic Editor IPC API enabled (Preferences → Plugins).
- Python runtime managed by KiCad for the plugin, with Tkinter available.
- The official KiCad `kicad-python` development bindings from GitLab `main`, required because the released 0.8.0 PyPI package does not yet expose the Schematic IPC API. CI builds a wheel for the current upstream SHA and caches it on the self-hosted runner.

Schematic placement, selection and library reload APIs used here are part of KiCad's newer schematic IPC interface. This plugin does not support the old SWIG-only plugin interface.

## Install

1. Copy `plugin/kicad-blocks` into the KiCad user plugin folder. Typical locations:
   - Linux: `~/.local/share/KiCad/11.0/plugins/kicad-blocks`
   - Windows: `%USERPROFILE%\\Documents\\KiCad\\11.0\\plugins\\kicad-blocks`
   - macOS: `~/Documents/KiCad/11.0/plugins/kicad-blocks`
2. Enable the IPC API in KiCad Preferences → Plugins.
3. Restart KiCad and enable the **KiCad Blocks Manager** schematic action.
4. Use the action and enter one of `create`, `place`, `local`, `sync`, `edit`, `publish`, `bom`, `erc`, or `nest`. The `sync` action on a selected local copy performs bidirectional three-way synchronization.

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

## Continuous integration on the self-hosted runner

The workflow uses the repository's local runner with labels `self-hosted`, `linux`, and `x64`; it does not allocate a GitHub-hosted VM or consume the hosted-runner allowance.

The runner is expected to have Python 3 and package/network access. When KiCad CLI is missing, the workflow installs the official KiCad development-nightly package (`kicad-nightly`) from the KiCad PPA. That install requires root or non-interactive `sudo` inside the runner container. Python test tools and the released `kicad-python` IPC bindings are installed into an isolated virtual environment under `RUNNER_TEMP`. Set up/register the self-hosted runner in the repository's **Settings → Actions → Runners** and keep it online. The job runs only for pushes to `main` and pull requests whose head branch is in this same repository; fork pull requests are deliberately excluded from the local runner. A concurrency group retains only the newest pending validation for each Git ref so sequential pushes do not build an unbounded queue.

The CI pipeline builds the official KiCad Python development IPC bindings from GitLab `main` (cached by upstream commit SHA on the self-hosted runner), runs headless KiCad IPC integration tests for facade placement/nested composition and PCB net/footprint/pad operations, validates generated symbol/schematic assets by CLI export, and exercises recursive BOM and ERC operations against KiCad itself. Local-copy conversion and bidirectional synchronization in a live IPC session, plus full PCB block layout import with routing/zone preservation, remain to be tested end-to-end.

## Local development checks

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests
python tests/validate_kicad_cli.py
```
