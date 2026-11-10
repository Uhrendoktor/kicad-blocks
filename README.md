# KiCad Encapsulated Blocks

**Project tracker:** [MVP tracking issue #1](https://github.com/Uhrendoktor/kicad-blocks/issues/1). Requirements, decisions, verification evidence, and remaining work are maintained there.

KiCad Blocks is an IPC schematic plugin for **black-box, library-backed schematic symbols**. A placed block is one ordinary KiCad symbol with external pins. Its implementation remains in a separate `.kicad_block` library asset; the plugin does not create a child sheet or copy the internal circuit into the project schematic.

## MVP behavior

- Create a block from an external pin interface (`NAME:type`). The plugin generates a normal `.kicad_sym` facade and a separate editable `implementation.kicad_sch` document. The create dialog can optionally attach a pre-routed `.kicad_pcb` and its matching `.kicad_pro` netclass/settings sidecar.
- All block facades in one shared library are aggregated into a single `KBlocks.kicad_sym` catalog using unique symbol names. A project registers one stable `KBlocks` row in `sym-lib-table`; block additions update that catalog and reload the same nickname through KiCad's IPC API.
- Place existing blocks from the configured library.
- Convert a selected library-linked instance into an independent project-local implementation copy. The facade symbol ID, KIID, coordinates and wiring remain unchanged. A project sidecar at `.kicad_blocks/instances.json` binds that schematic instance KIID to its local implementation assets, allowing two instances of the same facade to use different local copies without adding symbol-library rows.
- Synchronize a selected local copy with its library source in both directions. Local-only edits are published, library-only edits are pulled, identical concurrent edits converge, and divergent edits are rejected before either side is changed. External pin/interface changes require an explicit migration.
- Edit a block's separate implementation in another Eeschema window; publish its changed files to advance the library revision.
- Add nested block instances to the library manifest. Each occurrence has a unique instance ID, reference, placement and child-pin-to-parent-net mapping. The plugin composes child facade symbols and global labels into the parent library implementation schematic; multiple instances of the same child are supported. Dependencies use library-relative paths, resolve by block ID and pinned revision, and cycles are rejected without creating hierarchical sheets.
- Optionally store a pre-routed `.kicad_pcb` file with the block asset. The `pcb` action merges its footprints, routed tracks, vias, zones and graphics into a separate copy of a selected project board, maps facade pins to project nets, and namespaces internal nets and footprint references.

## Important functional boundary

The facade symbol is what KiCad sees in the parent schematic's native ERC, netlist and BOM. The internal components in `implementation.kicad_sch` are a separate document and are **not** automatically included in those project-level outputs. The plugin provides recursive BOM export and recursive internal ERC: each dependency occurrence's implementation schematic is processed by KiCad CLI and reports/BOM rows are aggregated. Nested child facade instances are composed inline into the parent library implementation schematic, with their pins wired through the declared net map; each child implementation remains a separate reusable library asset. Stored PCB layouts can be applied to a separate output copy of a project board. The operation maps external facade pins to existing or new board nets, namespaces internal nets and footprint references, transforms the layout about its declared anchor, supports counter-clockwise rotation in degrees, and regenerates object UUIDs. When the block includes `layout.kicad_pro`, it merges named netclass definitions and exact net assignments into a matching copied project `.kicad_pro` file; class name conflicts are resolved by instance namespacing. Existing project settings are preserved. Broad netclass patterns and chain-derived rules, plus unrelated board design setup, are not imported. It deliberately does not overwrite a board open in PCB Editor. The project board must already have all layers used by the block layout. Review the resulting board and project settings in PCB Editor before replacing the project files. These are not hidden behind a fake native hierarchy.

The internal schematic is editable: open the `implementation.kicad_sch` file from the block's library directory in Eeschema. It starts with global interface labels matching the facade pin names. Internal circuitry can be built with ordinary KiCad symbols and wires.

## Requirements

- An IPC-capable KiCad nightly/development build with the Schematic Editor IPC API enabled (Preferences → Plugins). CI currently verifies KiCad nightly **10.99.0** on Linux.
- Python runtime managed by KiCad for the plugin, with Tkinter available.
- The official KiCad `kicad-python` development bindings. The self-hosted CI builds the current upstream `main` wheel, stores it as the `kicad-python-dev` GitHub Release asset, and the plugin's `requirements.txt` downloads that prebuilt wheel. KiCad's plugin manager installs requirements in binary-wheel-only mode, so the published binary is required; a Git/VCS source requirement will not install through that manager.
- The currently published wheel targets CPython 3.12 on Linux x86_64, matching the verified KiCad nightly 10.99.0 runner. Stable-release, other Python ABI, Windows and macOS compatibility have not yet been verified.

Schematic placement, selection and library reload APIs used here are part of KiCad's newer schematic IPC interface. This plugin does not support the old SWIG-only plugin interface.

## Install

1. Clone or download this repository.
2. From its root, run the installer with the KiCad version directory used on your machine:

   ```bash
   python3 scripts/install_plugin.py --version 11.0
   ```

   The installer uses KiCad's per-version user plugin directory below the user-data root: `<user-data-root>/kicad/<version>/plugins/org.uhrendoktor.kicadblocks`. Default roots are `~/.local/share/KiCad` on Linux, `~/Documents/KiCad` on macOS, and `%USERPROFILE%\\Documents\\KiCad` on Windows. Use `--documents-home PATH` to override the root. For a nightly build reporting version `10.99`, use `--version 10.99`.

3. In KiCad, open Preferences → Plugins and enable **Enable KiCad API**. On Linux, verify that the selected Python interpreter is Python 3.10 or newer. Restart KiCad.
4. The plugin uses the reverse-DNS KiCad IPC identifier `org.uhrendoktor.kicadblocks`. The first time KiCad discovers the plugin, it creates an isolated environment and installs the package dependencies declared in `plugin/kicad-blocks/requirements.txt`. The **KiCad Blocks Manager** action appears after environment setup completes. This may take a little while on the first launch. Do not manually pip-install the same requirements into the KiCad environment.
5. Use the action with one of `create`, `place`, `local`, `sync`, `edit`, `publish`, `bom`, `erc`, `pcb`, or `nest`. The `sync` action on a selected local copy performs bidirectional three-way synchronization.

To reinstall after changing the plugin's requirements or if its environment is damaged, right-click one of its actions in Preferences → Plugins and choose **Recreate Plugin Environment**, or remove the existing plugin folder and run the installer again. The plugin environment is stored under KiCad's versioned cache directory, typically `~/.cache/kicad/<version>/python-environments/org.uhrendoktor.kicadblocks` on Linux.

See the official [KiCad add-on developer documentation](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/) for plugin discovery, action registration, and isolated Python environments.

The default shared library location is `~/Documents/KiCad/Blocks`. The create/nest action lets you select another directory; the selection is remembered in `~/.config/kicad-blocks/settings.json`. Set `KICAD_BLOCKS_LIBRARY` to override it. The shared folder contains one `KBlocks.kicad_sym` catalog plus individual `.kicad_block` asset directories. The project registers only the catalog path. Per-instance local links are stored as paths relative to the project directory, so local implementation copies remain portable when the project folder moves.

## Pin definitions

Example:

```text
VIN:input, GND:passive, VOUT:output, EN:input
```

Supported electrical types: `input`, `output`, `bidirectional` (`bidir` alias), `tristate`, and `passive`. Pins are numbered in the order entered.

## On-disk format

Each block is stored as a folder ending in `.kicad_block`. The shared library root also contains the aggregate facade catalog:

```text
KiCad/Blocks/
  KBlocks.kicad_sym             # one stable project symbol-library entry
  Buck_Converter_<id>.kicad_block/
    manifest.json
    block.kicad_sym             # that block's source facade asset
    implementation.kicad_sch
    layout.kicad_pcb            # optional
    layout.kicad_pro            # optional netclass/settings source
```

All facade symbols are named uniquely using the block slug and ID suffix, and merged into `KBlocks.kicad_sym`. The project does not need a new symbol-library table row when another block is added.

A local copy is stored under the project directory:

```text
<project>/
  .kicad_blocks/
    instances.json              # schematic KIID -> relative local-copy path
    local/<block>_<local-id>.kicad_block/
      manifest.json
      block.kicad_sym
      implementation.kicad_sch
      layout.kicad_pcb            # optional
      layout.kicad_pro            # optional netclass/settings source
```

Local copies reuse the shared `KBlocks:<symbol>` facade; only the implementation asset is local. The sidecar binds a specific schematic symbol KIID to its independent block folder. Its manifest records the source block, source revision and per-file sync baseline. Local copies are not silently overwritten when both the library and local version have changed the same file.

## Continuous integration on the self-hosted runner

The workflow uses the repository's local runner with labels `self-hosted`, `linux`, and `x64`; it does not allocate a GitHub-hosted VM or consume the hosted-runner allowance.

The runner is expected to have Python 3 and package/network access. When KiCad CLI is missing, the workflow installs the official KiCad development-nightly package (`kicad-nightly`) from the KiCad PPA. That install requires root or non-interactive `sudo` inside the runner container. Python test tools are installed into an isolated virtual environment under `RUNNER_TEMP`; the official `kicad-python` development wheel is restored from the Actions cache keyed by its upstream commit SHA. Set up/register the self-hosted runner in the repository's **Settings → Actions → Runners** and keep it online. The job runs only for pushes to `main` and pull requests whose head branch is in this same repository; fork pull requests are deliberately excluded from the local runner. A concurrency group retains only the newest pending validation for each Git ref so sequential pushes do not build an unbounded queue.

The CI pipeline builds the official KiCad Python development IPC bindings from GitLab `main` (cached by upstream commit SHA on the self-hosted runner), publishes the tested CPython 3.12/Linux x86_64 wheel to the `kicad-python-dev` GitHub Release asset for the plugin manager, validates generated symbol/schematic assets and recursive BOM/ERC with KiCad CLI, launches the real Eeschema GUI under Xvfb, verifies KiCad's plugin discovery and managed environment using the unmodified production requirements, and runs schematic/PCB IPC integration tests. The GUI test makes a real graphical global-label edit in the opened implementation, saves it, invokes the plugin Publish action, synchronizes a local copy that existed before the edit, and verifies both saved schematics with KiCad CLI. The IPC tests cover facade-only placement, per-instance local copies, bidirectional synchronization and conflicts, nested composition, and pre-routed PCB layout merge to a separate board copy.

The latest green run is [#163](https://github.com/Uhrendoktor/kicad-blocks/actions/runs/42913077552) on commit `b4c640ef6b9e8f91149828a4ee7de22f5e9676ef`: 48 Python tests and 5 subtests passed; Ruff, KiCad CLI asset/BOM/ERC validation, Eeschema GUI edit/Publish/Sync, plugin discovery/managed environment, schematic IPC, nested composition, PCB IPC and rotated PCB-layout merge/reload all passed. The PCB integration verified the optional `.kicad_pro` sidecar merge, collision-safe netclass naming, exact net assignments, and refusal to overwrite existing output board/settings files. The create dialog now lets a user attach both a `.kicad_pcb` layout and its optional `.kicad_pro` project settings asset to a reusable block. Broad pattern/chain netclass rules, unrelated board setup merging and live in-place editing of an already-open board remain unsupported.

## Local development checks

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests
python tests/validate_kicad_cli.py
```
