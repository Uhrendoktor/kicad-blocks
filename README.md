# KiCad Encapsulated Blocks

**Project tracker:** [MVP tracking issue #1](https://github.com/Uhrendoktor/kicad-blocks/issues/1). Requirements, decisions, verification evidence, and remaining work are maintained there.

KiCad Blocks is an IPC schematic plugin for **black-box, library-backed schematic symbols**. A placed block is one ordinary KiCad symbol with external pins. Its implementation remains in a separate `.kicad_block` library asset; the plugin does not create a child sheet or copy the internal circuit into the project schematic.

## MVP behavior

- Create a block by selecting one hierarchical sheet symbol in Eeschema and running **Create block from selected sheet**. KiCad Blocks copies that sheet's complete subschematic into the block implementation; the original project sheet remains untouched. Every distinct hierarchical-label name in that implementation becomes one facade pin, with the KiCad label shape mapped automatically to the matching electrical pin type (input, output, bidirectional, tri-state or passive). There is no manual pin-entry step. Add, rename or remove hierarchical labels in the implementation and use **Publish Library Block** to synchronize the facade pin names and electrical types. When a label name remains the same, its existing facade pin number, location and pin graphics are preserved; newly added pins receive generated initial locations. Nested hierarchical sheets are copied recursively into the block as internal implementation files. Their labels do not become facade pins. The extractor rewrites relative `Sheetfile` references and rebases hierarchy instance paths so the copied tree no longer depends on the original project files.
- All block facades in one shared library are aggregated into a single `KBlocks.kicad_sym` catalog using unique symbol names. A project registers one stable `KBlocks` row in `sym-lib-table`; block additions update that catalog and reload the same nickname through KiCad's IPC API.
- Browse the shared block library in a searchable overview with a facade preview, pin/electrical types, parameters, implementation variants and PCB layouts. Double-click a row or choose **Place at cursor** to hand the selected facade to KiCad's native interactive placement tool; the symbol follows the mouse until clicked into the schematic. The browser and manager minimize during placement so they do not cover the schematic; press Esc to finish, then restore the browser from the taskbar to choose another block.
- Convert a selected library-linked instance into an independent project-local implementation copy. The facade symbol ID, KIID, coordinates and wiring remain unchanged. A project sidecar at `.kicad_blocks/instances.json` binds that schematic instance KIID to its local implementation assets, allowing two instances of the same facade to use different local copies without adding symbol-library rows.
- Synchronize a selected local copy with its library source in both directions. Local-only asset, parameter-schema and named-layout metadata edits are published; library-only edits are pulled; identical concurrent edits converge; and divergent edits are rejected before either side is changed. Layout choices and asset files are synchronized together. External pin/interface changes require an explicit migration.
- Edit a block's separate implementation in another Eeschema window; publish its changed files to advance the library revision. To edit the facade artwork or pin placement, select a placed block instance and choose **Edit facade symbol** in the manager; this invokes Eeschema's native **Edit Library Symbol** action (Ctrl+Shift+E), opening the real library symbol in KiCad Symbol Editor. Save the symbol there, then use **Publish Library Block**; the publish step reconciles its pins with the implementation's hierarchical labels while retaining the edited symbol-body graphics. Manager actions that need a library block (Publish, recursive BOM/ERC and nested composition) use a searchable picker: type to filter, press Down to browse, Enter to select, or Esc to cancel. The manager's **Validate block library** action scans every shared block, checks manifest/profile consistency, dependency references, required assets and SHA-256 revisions, then runs recursive KiCad ERC and writes `library-validation.json` plus per-block ERC reports. Broken manifests are listed instead of silently omitted.
- Add nested block instances to the library manifest. Each occurrence has a unique instance ID, reference, placement, orientation (0°, 90°, 180° or 270°) and child-pin-to-parent-net mapping. The plugin composes rotated child facade symbols and global labels at the exact transformed pin endpoints in the parent library implementation schematic; multiple instances of the same child are supported. The live KiCad IPC test verifies two recursive levels (90° child and 270° leaf), including each implementation's facade orientation and exact mapped pin-label coordinates/spin styles. Dependencies use library-relative paths, resolve by block ID and pinned revision, and cycles are rejected without creating hierarchical sheets.
- Store one or more named, pre-routed `.kicad_pcb` layout alternatives with a block, each with an optional matching `.kicad_pro` netclass/settings sidecar and an explicit facade-pin-to-layout-net map. Define optional named configuration profiles in the block manifest (or through the Create Block profile prompts). Each profile binds an exact `KB_VARIANT` implementation value, typed parameter overrides and a PCB layout ID. The generated facade stores the selected profile in a hidden `KB_PROFILE` field; the manager's **Configure block instance** form applies all linked values together. Manual edits switch the saved instance to a custom configuration. The facade carries a hidden `KB_LAYOUT` custom field initialized to the first layout. Use the manager's **Configure block instance** action to edit `KB_VARIANT`, typed `KB_PARAM_<NAME>` values and `KB_LAYOUT` together in one form. Saving writes the selected symbol through KiCad schematic IPC as one undoable change; it does not modify the shared library. The same fields remain editable through KiCad's native **Symbol Properties → Fields** tab. `pcb` uses that per-instance choice when merging footprints, routed tracks, vias, zones and graphics into a separate copy of the project board. `pcb-many` does the same for multiple selected instances, each with its own selected layout, net map, position, anchor and rotation. Both actions namespace internal nets and footprint references, then save an adjacent `.kicad-blocks-placement.json` sidecar containing the exact UUIDs of the imported top-level board objects for each schematic block instance. The generated board copy opens separately in PCB Editor. Use the PCB-scoped **Place Imported Block Layout** action to select one recorded instance and start KiCad's native interactive move tool; the footprints, tracks, vias, zones and graphics belonging to that block follow the cursor together. Click to commit the placement or press Esc to cancel it. For `pcb-many`, invoke the action once for each recorded instance. The numeric position prompt is no longer needed; the pre-merge position is only a staging point. The source board remains untouched. Existing blocks with a single legacy `layout_file` continue to work.

## Block variants (V1)

V1 keeps variant selection deliberately small: each facade has one hidden KB_VARIANT field, and the block manifest maps exact values to implementation schematics.

- Define the value normally in KiCad, for example as a project text variable named SUPPLY_VOLTAGE or as a custom field on the hierarchical sheet symbol that contains the block.
- Set `KB_VARIANT` with the manager's **Configure block instance** form, or directly in **Symbol Properties → Fields** to a literal value such as `3V3` or a KiCad text-variable reference such as `${SUPPLY_VOLTAGE}`.
- When creating a block, optionally select variant implementation .kicad_sch files and enter the exact value that selects each one (for example 1V8 and 3V3). The files are copied into the block asset.
- Use the separate **Open Block Implementation** add-on action with exactly one facade selected. It resolves the field in project/sheet context and opens the matching schematic in Eeschema.

Matching is case-insensitive but otherwise exact. If a block declares variants, an empty, unresolved or unknown value is an error; V1 never silently opens the default circuit. Blocks without variants continue to open their single implementation.kicad_sch. Project-local copies carry their own mappings and variant implementation files; variant metadata participates in local-copy synchronization.

V1 does not support expression syntax, automatic propagation into nested block instances, dynamic circuit changes in the parent schematic, or double-click/right-click interception. Double-clicking a facade still opens KiCad's normal Symbol Properties dialog; use the standalone schematic add-on button or KiCad's Plugins action to open its implementation. The variant choice currently controls this open action only; recursive BOM/ERC and PCB layout operations continue to use their existing base implementation/default layout behavior. Edit KB_VARIANT through the normal Symbol Properties fields. V2 adds named profiles that bind a declared variant, parameter overrides and PCB layout together. The selected profile is stored on the facade instance; recursive export behavior remains a separate capability.

## Important functional boundary

The facade symbol is what KiCad sees in the parent schematic's native ERC, netlist and BOM. The internal components in `implementation.kicad_sch` are a separate document and are **not** automatically included in those project-level outputs. The plugin provides recursive BOM export and recursive internal ERC: each dependency occurrence's implementation schematic is processed by KiCad CLI and reports/BOM rows are aggregated. Nested child facade instances are composed inline into the parent library implementation schematic, with their pins wired through the declared net map; each child implementation remains a separate reusable library asset. Stored PCB layouts can be merged into a separate output copy of a project board. The operation maps external facade pins to existing or new board nets, namespaces internal nets and footprint references, transforms the layout about its declared anchor, supports counter-clockwise rotation in degrees, and regenerates object UUIDs. A board-sidecar manifest preserves the regenerated top-level UUIDs for each imported block instance. In PCB Editor, the PCB-scoped **Place Imported Block Layout** action resolves those IDs against the active board and calls KiCad's native `Board.interactive_move()`; the whole imported object set then follows the cursor/grid and is committed by a click or canceled with Esc. It never selects unrelated project-board objects. When the selected layout includes a `.kicad_pro` sidecar, it merges named netclass definitions and exact net assignments into a matching copied project `.kicad_pro` file; class name conflicts are resolved by instance namespacing. Existing project settings are preserved. Block netclass wildcard and anchored-regex patterns are resolved against only the net names present in the stored PCB layout, then converted to exact assignments on the mapped/namespaced nets in the output project; the original broad rules are not copied into the parent project, so they cannot affect unrelated nets. Supported bus-vector patterns are expanded to their actual member nets. Unmatched/unsupported bus patterns are reported. PCB net-chain topology is copied as well: member nets are mapped to the imported board nets, chain names are instance-namespaced, terminal-pad UUIDs are rebound to the imported footprint pads, and `net_chain_classes` / `net_chain_netclasses` entries are mapped to the new chain names and imported netclass names. Chain metadata without a corresponding board chain is reported rather than copied as a dangling rule. Unrelated board design setup is not imported. It deliberately does not overwrite a board open in PCB Editor. The project board must already have all layers used by the block layout. Review the resulting board and project settings in PCB Editor before replacing the project files. These are not hidden behind a fake native hierarchy.

The internal schematic is editable: open the `implementation.kicad_sch` file from the block's library directory in Eeschema. It starts with global interface labels matching the facade pin names. Internal circuitry can be built with ordinary KiCad symbols and wires.

### Parameter and layout behavior

Example parameter declaration in the Create Block dialog:

```text
OUTPUT_VOLTAGE:number=3.3, CHANNELS:integer=4, ENABLE:boolean=true, PART=STM32
```

Each placed facade stores overrides in its own `KB_PARAM_<NAME>` symbol fields and its implementation choice in `KB_VARIANT`. Edit all instance-local configuration in the manager's **Configure block instance** form, or use **Symbol Properties → Fields** in Eeschema for direct field editing. Add tokens such as `{{OUTPUT_VOLTAGE}}` to quoted text fields in the implementation schematic (for example a component's Value field) to make the recursive BOM/ERC operations render those values. The root instance's overrides are used for the selected block; nested dependency instances currently use their declared defaults. Native project-level BOM/netlist/ERC still see only the facade symbol and its external pins. This feature does not evaluate arbitrary formulas, alter wires/pins, or reconfigure a live schematic dynamically.

Layout descriptors are part of the block manifest. A new block's `layouts` list stores an `id`, display `name`, board `file`, optional `project_file` and optional `ports` mapping for each alternative. `default_layout` is the first layout ID. The selected instance stores the chosen ID in `KB_LAYOUT`; this keeps the implementation choice with the schematic symbol when the file is saved or copied. Profiles store a partial parameter map, exact `KB_VARIANT` value and layout ID. Parameters omitted from a profile inherit the block defaults. Selecting a profile applies all three sets of values in one editor action; changing any linked value saves the instance as a custom configuration (`KB_PROFILE` is cleared).

## Requirements

- KiCad **10.99 development nightly** (the pre-release development build for KiCad 11) or KiCad 11.0+. Enable the KiCad IPC API in Preferences → Plugins for the Schematic Editor and PCB Editor actions you use. Stable KiCad 10.0.8 and all 10.0.x releases are unsupported: KiCad 9/10 IPC add-ons are limited to PCB Editor; the Schematic Editor add-on scope is introduced in KiCad 11. CI verifies nightly **10.99.0** on Linux.
- Python runtime managed by KiCad for the plugin, with Tkinter available. On Debian/Ubuntu, install Tkinter and the small X11 automation utility used for native cursor placement with `sudo apt install python3-tk xdotool`. The placement handoff sends Eeschema's default **Add Symbol** shortcut (`A`) to the active editor, then filters KiCad's native symbol chooser; keep that shortcut bound to `A`.
- The official KiCad `kicad-python` development bindings. V1.1 pins the upstream source revision in `plugin/kicad-blocks/kicad-python.lock` and uses a version-addressed GitHub Release asset (`kicad-python-76b47d25`). CI verifies the asset digest and refuses to replace different bytes. The separate `kicad-python-dev` asset remains a mutable development channel. KiCad's plugin manager installs requirements in binary-wheel-only mode, so the published binary wheel is required; a Git/VCS source requirement will not install through that manager.
- The currently published `kicad-python` development wheel is tagged `cp312-cp312-linux_x86_64`; `plugin.json` therefore declares Python `min_version` 3.12, and the documented/tested runtime is CPython 3.12 on Linux x86_64. The installer explicitly rejects Windows, macOS, other CPU architectures, and KiCad 10 stable rather than leaving an installation whose required IPC API/wheel cannot load. KiCad 11 stable, other Python ABIs and other platforms need matching tested wheels before they can be advertised as supported.

Schematic placement, selection and library reload APIs used here are part of KiCad's newer schematic IPC interface. This plugin does not support the old SWIG-only plugin interface.

## Install

1. Download the **KiCad Blocks 1.1.0** release ZIP for Linux x86_64 / CPython 3.12 from the [GitHub Releases page](https://github.com/Uhrendoktor/kicad-blocks/releases). Verify it with the accompanying `.zip.sha256` file, then extract the archive.
2. From the extracted `kicad-blocks-1.1.0-linux-x86_64-cp312` directory, run the bundled installer:

   ```bash
   python3 scripts/install_plugin.py --version 10.99
   ```

   The installer uses KiCad's per-version user plugin directory below the Linux user-data root: `<user-data-root>/kicad/<version>/plugins/org.uhrendoktor.kicadblocks`. The verified nightly build reports version `10.99`, so use `--version 10.99`. The installer also accepts KiCad 11.0+ directories for future builds, but V1.1 does not advertise those targets as verified until a matching binding wheel and CI target exist. Use `--documents-home PATH` to override the root. The ZIP includes the installer, plugin package, README, license, changelog and a `RELEASE_INFO.json` provenance record.

3. In KiCad, open Preferences → Plugins and enable **Enable KiCad API**. On the currently supported Linux build, verify that KiCad's selected Python interpreter is CPython 3.12. Restart KiCad.
4. The plugin uses the reverse-DNS KiCad IPC identifier `org.uhrendoktor.kicadblocks`. The first time KiCad discovers the plugin, it creates an isolated environment and installs the package dependencies declared in `plugin/kicad-blocks/requirements.txt`. The **KiCad Blocks Manager** and **Open Block Implementation** actions appear after environment setup completes. This may take a little while on the first launch. Do not manually pip-install the same requirements into the KiCad environment.
5. Use **Browse Block Library** to search, preview and place blocks. Select a block and click **Place at cursor** (or double-click its row); KiCad's own symbol chooser is used to start interactive placement, and the facade then follows the mouse. The browser and manager minimize while placing so the schematic stays visible; after pressing Esc, restore the browser from the taskbar to select another block. Use **Open Block Implementation** to open the selected facade's resolved variant. The **KiCad Blocks Manager** remains for create, per-instance configuration guidance, local-copy/sync, publish, recursive BOM/ERC, PCB layout application and nesting.

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

The CI pipeline builds the official KiCad Python development IPC bindings from GitLab `main` (cached by upstream commit SHA on the self-hosted runner), validates generated symbol/schematic assets and recursive BOM/ERC with KiCad CLI, launches the real Eeschema GUI under Xvfb, verifies KiCad's plugin discovery and managed environment using the unmodified production requirements, and runs schematic/PCB IPC integration tests. Only after all validation passes does it attempt to publish the CPython 3.12/Linux x86_64 wheel to the `kicad-python-dev` GitHub Release asset; this release upload is best-effort and cannot mask the test result. The GUI test makes a real graphical global-label edit in the opened implementation, saves it, invokes the plugin Publish action, synchronizes a local copy that existed before the edit, and verifies both saved schematics with KiCad CLI. The IPC tests cover facade-only placement, per-instance local copies, bidirectional synchronization and conflicts, nested composition, and pre-routed PCB layout merge to a separate board copy. PCB layout tests additionally validate the per-instance UUID manifest and the exact KIID list passed to KiCad's native cursor move action.

The latest exact-head verification for V2 is [#287](https://github.com/Uhrendoktor/kicad-blocks/actions/runs/43199906708) on commit `336bb38dabb91ce3f101c9c557d7ea65cbe2685e`: **117 Python tests and 27 subtests passed**; Ruff, KiCad CLI asset/BOM/ERC validation, Eeschema GUI edit/Publish/Sync, plugin discovery/managed environment, schematic IPC/composition, PCB IPC and PCB layout merge/reload all passed. The PCB registration tests verify that the new `Place Imported Block Layout` action is installed with PCB Editor scope. Placement tests verify sidecar ownership, per-instance UUID groups, complete KIID resolution, and that the PCB action entrypoint calls `Board.interactive_move()` for only the selected group's recorded items. The native move API and plugin scope are documented KiCad interfaces; the current CI does not yet automate a live PCB Editor mouse-driven commit/cancel sequence. The schematic IPC test composed two recursive levels (parent→child at 90°, child→leaf at 270°), reopened both implementation schematics, and verified each mapped global label's exact transformed pin endpoint and rotated label orientation. The PCB integration verified the optional `.kicad_pro` sidecar merge, collision-safe netclass naming, exact and pattern-resolved net assignments, copied net-chain membership with remapped terminal-pad UUIDs and chain-class/netclass associations, and refusal to overwrite existing output board/settings files. The create dialog lets a user attach both a `.kicad_pcb` layout and its optional `.kicad_pro` project settings asset to a reusable block. Unsupported or unmatched bus-pattern syntax is reported rather than copied as a broad rule; unrelated global board setup and live in-place editing of an already-open board remain unsupported. The tested target is KiCad nightly 10.99.0 on Linux x86_64 / CPython 3.12.

## Local development checks

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests
python tests/validate_kicad_cli.py
```
