# Feature workflows

This guide explains each KiCad Blocks capability from the editor UI. The [visual evidence page](visual-evidence.md) contains real application captures.

## Where to find commands

### Eeschema toolbar

The three frequent actions are standalone toolbar buttons:

- **Browse and Place Functional Blocks** — search, inspect, configure and place a block.
- **Create Block from Selected Sheet** — extract a reusable block from a hierarchical sheet.
- **Open Block Implementation** — open the implementation or the variant selected on one placed instance.

### KiCad Blocks Tasks

Open **Tools → External Plugins → KiCad Blocks Tasks** for less-frequent actions. The task hub groups actions by workflow:

| Tab | Actions |
| --- | --- |
| **Use & build** | Browse and place blocks; create a block from a sheet; open an implementation. |
| **Edit & maintain** | Configure an instance; edit its facade symbol; publish an implementation; make an instance local; synchronize a local copy; nest a block. |
| **Checks & PCB** | Validate registered libraries; export recursive BOM; run recursive ERC; apply one PCB layout; apply multiple PCB layouts. |

PCB Editor also has **Place Imported Block Layout**. KiCad's IPC action manifest does not expose arbitrary custom submenus, so KiCad Blocks uses individual toolbar actions plus the task hub.

## 1. Create a functional block from a hierarchical sheet

1. In Eeschema, either select one hierarchical sheet symbol in its parent or open the target hierarchical sheet itself with nothing selected.
2. Click **Create Block from Selected Sheet** (or use **Use & build → Create block from sheet**).
3. Enter the block name and optional description in the single form.
4. Choose the shared library destination if needed.

The plugin copies the full subschematic tree into a new `.kicad_block` folder. The source project sheet is left untouched. Each distinct hierarchical-label name on the root sheet becomes one facade pin. Label shapes map to KiCad electrical pin types: input, output, bidirectional, tri-state or passive. Pin numbers follow the order in which labels are encountered.

Nested child sheets are copied recursively as implementation files. Their `Sheetfile` links and hierarchy instance paths are rewritten so the block no longer depends on the original project paths. Labels on nested child sheets do not become facade pins.

Open **Edit implementation** in the library browser to work on the internal schematic with normal KiCad symbols, wires and labels.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/create-block-form-light.png" alt="Single-form Create Functional Block dialog" width="660">
</p>

## 2. Find and place a block

1. Click **Browse and Place Functional Blocks**.
2. Search by block name, description, pin name, parameter or declared variant.
3. Select a block to inspect its facade preview, connections, parameters, implementation choices and PCB layouts.
4. Choose any required variant or named profile.
5. Double-click a result or choose **Place at cursor**.
6. KiCad's native Add Symbol chooser opens. Confirm the selected facade and place it like an ordinary symbol.

The project receives a combined catalog at `.kicad_blocks/KBlocks.kicad_sym` and one stable `KBlocks` row in its `sym-lib-table`. Shared library folders remain separate and reusable across projects.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-light.png" alt="Searchable Functional Block Library browser with facade preview and block overview" width="900">
</p>

### Manage shared library folders

Open **Manage library folders…** from the browser. The manager can add an existing folder, create a new empty library, change a registered folder path, unregister a folder or choose the default destination.

Changing a registered path does not move files. Unregistering a library does not delete it.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-folders-light.png" alt="Registered library folders and folder-management controls" width="900">
</p>

## 3. Configure variants, profiles and parameters

### Implementation variants

A variant maps an exact `KB_VARIANT` value to an alternative implementation schematic.

1. In the browser, select a block and open **Manage variants**.
2. Add, replace, rename or remove an implementation schematic and its value.
3. Select a placed facade instance in Eeschema.
4. Use **Open Block Implementation** to open the implementation that matches the selected instance.

Matching is case-insensitive but otherwise exact. If a block declares variants, an empty, unresolved or unknown value is an error; the plugin does not silently choose a default implementation. Blocks without variants open their single `implementation.kicad_sch`.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/implementation-variants-light.png" alt="Implementation variant editor with 5 V, 9 V and 12 V entries" width="900">
</p>

### Typed parameters

Parameter declarations are comma-separated. For example:

```text
OUTPUT_VOLTAGE:number=3.3, CHANNELS:integer=4, ENABLE:boolean=true, PART=STM32
```

The placed facade stores overrides as `KB_PARAM_<NAME>` symbol fields. To use a parameter in recursive BOM/ERC output, add a token such as `{{OUTPUT_VOLTAGE}}` to a quoted text field in the implementation schematic (for example a component Value field).

The selected root instance's overrides are used for that implementation. Nested dependency instances currently use their declared defaults. Parameters do not evaluate arbitrary formulas, change wires or pins, or reconfigure the parent schematic dynamically.

### Named configuration profiles

A profile applies a related set of values together:

- an exact implementation variant (`KB_VARIANT`);
- a partial typed parameter map (`KB_PARAM_<NAME>`);
- a PCB layout choice (`KB_LAYOUT`).

Select a profile in the placement chooser or use **Configure selected instance** to edit an existing instance. Parameters omitted by a profile inherit the block defaults. The selected profile ID is stored in `KB_PROFILE`. Editing one linked value turns the instance into a custom configuration and clears `KB_PROFILE`.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/instance-configuration-light.png" alt="Instance configuration form applying a profile, variant, parameter and PCB layout" width="850">
</p>

## 4. Edit and publish an implementation

The implementation is a native KiCad schematic in the shared block folder. Select **Edit implementation** in the browser or use **Open Block Implementation** on a placed instance.

To publish changes:

1. Save the implementation schematic in Eeschema.
2. Open **KiCad Blocks Tasks → Edit & maintain → Publish implementation**.
3. Select the block from the searchable picker if prompted.
4. The plugin refreshes the block revision and reconciles facade pins from root hierarchical labels.
5. The updated shared symbol catalog is registered in the current project.

When a hierarchical-label name is unchanged, the matching facade pin's number, location and graphics are retained. New names receive generated initial locations.

### Edit facade artwork

Select the placed block instance in Eeschema, then choose **Edit facade** in the browser or **Edit facade symbol** in the task hub. The plugin invokes KiCad's native **Edit Library Symbol** command to open the library symbol in Symbol Editor. Save the artwork/pin placement there, then publish the implementation so the facade pins are reconciled against hierarchical labels while preserving edited body graphics.

## 5. Make an instance local and synchronize it

Use this when one project needs a customized implementation without changing the shared library block.

1. Select exactly one library-linked facade instance in Eeschema.
2. Run **Make selected instance local**.
3. The plugin creates an independent implementation copy under `.kicad_blocks/local/` and records the instance-to-copy mapping in `.kicad_blocks/instances.json`.
4. Edit the local implementation in Eeschema.
5. Run **Synchronize local copy** on that selected instance.

The facade symbol ID, instance KIID, coordinates and wiring remain unchanged when the copy is created. Local-only changes can be published to the library; library-only changes can be pulled into the project. Identical concurrent edits converge. Divergent edits to the same file are rejected before either side is modified, so the conflict can be resolved deliberately. External pin/interface changes require an explicit migration.

Local links are stored relative to the project directory to keep project copies portable when moved.

## 6. Compose a nested block

Use **KiCad Blocks Tasks → Edit & maintain → Nest a block** to select a parent block and a child block.

Each dependency occurrence records a unique instance ID, reference, placement, orientation (0°, 90°, 180° or 270°) and child-pin-to-parent-net mapping. KiCad Blocks composes the rotated child facade symbol and global labels at the transformed pin endpoints in the parent implementation schematic.

The child remains a separate reusable library asset. Cyclic dependencies are rejected. The plugin does not create a child hierarchical sheet or flatten the child implementation into an untraceable copy.

## 7. Validate libraries and run recursive checks

### Validate registered libraries

Choose **Checks & PCB → Validate registered libraries**. The validator scans every registered shared block and checks:

- manifest and profile consistency;
- required assets and SHA-256 revision hashes;
- dependency references and dependency cycles;
- recursive ERC for implementation schematics.

It writes a `library-validation.json` summary and per-block ERC reports. Broken manifests are reported rather than silently omitted.

### Export recursive BOM

Select one placed block instance and run **Export recursive BOM**. KiCad CLI processes the block implementation and nested dependencies; exported rows are aggregated for the selected block tree. Tokens in implementation value fields are rendered using root-instance parameter overrides and nested defaults.

### Run recursive ERC

Select one placed block instance and run **Run recursive ERC**. The implementation and its nested dependencies are checked recursively using KiCad CLI. This is separate from native project ERC, which sees the facade symbol only.

## 8. Reuse pre-routed PCB layouts

A block may include one or more named `.kicad_pcb` layout alternatives. Each layout can have an optional matching `.kicad_pro` project-settings sidecar and an explicit map from facade pins to layout nets.

### Apply one or several layouts

- Select one block instance and use **Apply selected PCB layout**.
- Select multiple block instances and use **Apply multiple PCB layouts**.

The plugin merges footprints, routed tracks, vias, zones and graphics into a **separate copy** of the project board. It maps external pins to existing or new board nets, namespaces internal nets and footprint references, transforms around the block's declared anchor and supports counter-clockwise rotation. It regenerates object UUIDs and writes a sibling `.kicad-blocks-placement.json` sidecar that records imported top-level objects for each schematic instance.

The optional project-settings sidecar merges named netclass definitions and relevant mapped net assignments into a matching copied project file. Pattern-based rules are resolved only against nets present in the stored block layout and converted to exact assignments in the output. Supported bus vectors are expanded; unsupported or unmatched patterns are reported. PCB net-chain topology and class associations are mapped too. Unrelated board design setup is not imported.

The plugin does not overwrite a board open in PCB Editor. The project board must already contain all layers used by the block layout. Review the copied board and project settings before replacing project files.

### Place the imported group in PCB Editor

1. Open the generated board copy in PCB Editor.
2. Run the PCB-scoped **Place Imported Block Layout** action.
3. The plugin resolves the recorded UUIDs for one imported block instance and hands those objects to KiCad's native interactive move tool.
4. Move the complete imported group with the cursor/grid and click to commit; press **Esc** to cancel.
5. For a multi-instance merge, repeat the action for each recorded instance.

Only the recorded objects for that instance are moved; unrelated board objects are not selected.

## Important functional boundary

The facade is the project-level schematic object. Native project BOM/netlist/ERC therefore sees only that symbol and its external pins. It does not automatically include components inside the separate implementation. Use **Export recursive BOM** and **Run recursive ERC** for implementation-aware results.

PCB layout merge is a file-generation workflow, not live in-place editing of a board already open in PCB Editor. Inspect generated files before using them in the project.
