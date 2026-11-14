# Feature reference

This page describes exact action names, files, instance fields, parameter syntax, stored PCB layout behavior, and known limits. Use the [tutorial](tutorials/first-block.md) for a first-use sequence and the [how-to guides](workflows.md) for individual tasks.

## Contents

- [Terminology](#terminology)
- [Action map](#action-map)
- [Browser and manager actions](#browser-and-manager-actions)
- [Block and project files](#block-and-project-files)
- [Facade pins](#facade-pins)
- [Per-instance fields](#per-instance-fields)
- [Parameters and token expansion](#parameters-and-token-expansion)
- [Variants and profiles](#variants-and-profiles)
- [Block dependencies and automatic composition](#block-dependencies-and-automatic-composition)
- [PCB layout descriptors and merge behavior](#pcb-layout-descriptors-and-merge-behavior)
- [Local-copy synchronization](#local-copy-synchronization)
- [Validation reports](#validation-reports)
- [Recursive BOM and ERC](#recursive-bom-and-erc)
- [Locations and settings](#locations-and-settings)
- [Compatibility](#compatibility)

## Terminology

| Term | Meaning |
| --- | --- |
| **Facade symbol** | The ordinary KiCad symbol placed in the project schematic. Its pins form the block's external interface. |
| **Implementation** | The editable schematic tree stored in the shared `.kicad_block` directory. |
| **Variant** | A named choice of implementation schematic for one placed instance. |
| **Profile** | A reusable partial configuration containing a variant, parameter overrides, and an optional PCB layout. |
| **Shared library** | A source folder registered with KiCad Blocks and reused across projects. |
| **Local copy** | An independent project copy of a block implementation under `.kicad_blocks/local/`. |

## Action map

The common Eeschema entry points are separate toolbar actions:

| Entry point | Action | Context |
| --- | --- | --- |
| Eeschema toolbar | **Browse and Place Functional Blocks** | Browse all registered shared libraries. |
| Eeschema toolbar | **Create Block from Sheet** | Select a hierarchical sheet symbol in its parent, or run with no selection from the open sheet. |
| Eeschema toolbar | **Open Block Implementation** | Select one placed block instance. |
| Eeschema task hub | **KiCad Blocks Tasks** | Open the grouped task hub. |

The task hub groups these operations by tab:

| Tab | Actions |
| --- | --- |
| **Use & build** | Browse and place blocks; create block from sheet; open block implementation. |
| **Edit & maintain** | Configure selected instance; edit facade symbol; publish implementation; make selected instance local; synchronize local copy; publishing rebuilds the automatic child-dependency graph. |
| **Checks & PCB** | Validate registered libraries; export recursive BOM; run recursive ERC; apply selected PCB layout; apply multiple PCB layouts. |

The PCB Editor provides a separate **Place Imported Block Layout** action for a merged board copy that contains imported block groups and a placement sidecar.

## Browser and manager actions

The Functional Block Library browser presents these primary actions:

- **Place block** — starts KiCad's native symbol chooser and placement tool for the selected block.
- **Open implementation** — opens the default or selected implementation schematic.
- **Edit selected facade** — edits the library symbol in Symbol Editor. This action is enabled only when one placed instance of the highlighted block is selected in Eeschema.
- **Implementations** — opens the default and named implementation list.
- **Open PCB layout** — opens a stored board file for inspection.
- **Manage PCB layouts** — edits stored boards, implementation links, and the default layout.
- **Refresh** — reloads the registered library list.

The Implementations dialog contains a **Default** row and any named variants. Selecting **Default** enables **Edit default**, which opens the default schematic. Named rows enable **Edit variant**. A variant can be added from an existing schematic, a new blank schematic, or a copy of another library implementation.

The PCB layout manager separates the layout table, assignment controls, and Save/Cancel footer. The **Assign selected layout to** selector can choose **Default**, a named variant, or **No variant link**.

The **Add PCB layout** dialog supports two starting modes: **Create a new empty board** and **Choose an existing PCB file**. The file entry and **Choose file** button are visible only in the existing-file mode. **Use for implementation** associates the new board with **Default** or a named variant.

## Block and project files

A shared library root contains one facade catalog and one directory per block. A typical library looks like this:

~~~text
KiCad/Blocks/
  KBlocks.kicad_sym
  Buck_Converter_<id>.kicad_block/
    manifest.json
    block.kicad_sym
    implementation.kicad_sch
    implementation_1v8.kicad_sch        # optional variant schematic
    implementation_3v3.kicad_sch        # optional variant schematic
    layout_<id>.kicad_pcb                # optional named board layout
    layout_<id>.kicad_pro                # optional matching project settings
~~~

A manifest records the block identity, symbol name, description, revision and asset hashes, facade pins, typed parameters, variants, profiles, PCB layouts, and nested dependencies. File names for variants and layouts are generated from their values or IDs; the example names above are illustrative.

A consuming project receives local symbol and instance data:

~~~text
<project>/
  .kicad_blocks/
    KBlocks.kicad_sym
    instances.json
    local/
      <block>_<local-id>.kicad_block/
        manifest.json
        block.kicad_sym
        implementation.kicad_sch
        ...
~~~

The combined `KBlocks.kicad_sym` catalog is registered with the project. `instances.json` maps placed symbol KIID values to relative local-copy paths. A local copy can provide an independent implementation without changing the facade symbol or project wiring.

## Facade pins

Facade pins are derived from hierarchical labels on the implementation's root sheet. Supported electrical types are:

- `input`
- `output`
- `bidirectional` (also accepted as `bidir`)
- `tristate`
- `passive`

Pin numbers follow root-label declaration order. When a label name is preserved during publishing, its facade pin number, location, and graphics are retained. New labels receive generated initial locations. Duplicate root-label names collapse to one external pin when their definitions agree; conflicting definitions are rejected. Labels on child sheets remain internal.

## Per-instance fields

The facade stores configuration on each placed symbol instance:

| Field | Meaning |
| --- | --- |
| `KB_VARIANT` | Exact implementation-variant value. |
| `KB_PROFILE` | Selected named profile ID; cleared when a linked setting is edited manually. |
| `KB_LAYOUT` | Selected PCB layout ID. |
| `KB_PARAM_<NAME>` | Typed override for a declared block parameter. |

Edit these fields through **KiCad Blocks Tasks → Configure selected instance** or KiCad's **Symbol Properties → Fields** page. They belong to the selected symbol instance, not to the shared library.

## Parameters and token expansion

Parameter declarations are comma-separated. For example:

~~~text
OUTPUT_VOLTAGE:number=3.3, CHANNELS:integer=4, ENABLE:boolean=true, PART=STM32
~~~

Supported kinds are text (the default), number, integer, and boolean. A token such as `{{OUTPUT_VOLTAGE}}` in a supported quoted implementation text field, such as a component Value field, is substituted during recursive BOM/ERC processing.

The selected root instance's overrides are applied to its implementation. Nested dependency instances currently use their declared defaults. Parameters do not evaluate arbitrary expressions, change circuit connectivity, or dynamically reconfigure the top-level schematic.

## Variants and profiles

A variant maps a `KB_VARIANT` string to an alternative implementation schematic.

- Matching is case-insensitive but otherwise exact.
- If a block declares variants, an empty, unresolved, or unknown value is an error. The plugin does not guess a default.
- A block without variants uses `implementation.kicad_sch`.

A named profile is a reusable partial configuration containing a profile name and ID, a variant value, optional typed parameter overrides, and an optional PCB layout ID. Parameters not specified by the profile inherit block defaults.

Applying a profile writes the profile, variant, parameter, and layout fields to the selected facade in one schematic IPC transaction. Editing a linked field turns the instance into a custom configuration and clears `KB_PROFILE`.

## Block dependencies and automatic composition

A parent implementation contains child blocks as ordinary facade symbols. Open the parent implementation, use KiCad's normal **Add Symbol** command to place a facade from the registered `KBlocks` catalog, wire it normally, and save. No separate nesting command or manual child-pin mapping is required.

On block creation, publish, local-copy creation or synchronization, and before recursive BOM/ERC export or registered-library validation, KiCad Blocks exports the saved default implementation's KiCad XML netlist and reconciles `manifest.dependencies` against the facade symbols present in that schematic. It identifies each child by its `KBlocks` library symbol ID, then maps manifest pin names to actual net names using the facade pin numbers from the child manifest. It records the child reference, position in millimetres, rotation (0°, 90°, 180°, or 270°), stable occurrence ID, relative library path, revision, and pin map.

A pin absent from the netlist is recorded as `null` and stays electrically unconnected. KiCad Blocks never guesses a net by matching pin names. Removing a child facade removes its dependency on the next reconciliation. Unregistered child facades, unannotated references, mismatched facade pin numbers, and dependency cycles are reported as errors. Existing occurrence IDs are retained when a child block remains at the same reference.

The default implementation is the source of the library-wide dependency graph; named implementation variants are separate schematic assets. Child blocks remain separate library assets. KiCad Blocks does not turn them into hierarchical sheets or generate connection labels over the user's wiring.

## PCB layout descriptors and merge behavior

A block can store multiple named `.kicad_pcb` layouts. Each descriptor identifies its ID, display name, and board file. It can also name an optional `.kicad_pro` sidecar and a map from facade pins to layout nets. The manifest can select a default layout and associate a different layout with a named variant.

The merge operation:

- Maps facade pins to named layout nets.
- Namespaces internal nets and footprint references.
- Transforms board objects around the declared anchor and applies rotation.
- Copies supported footprints, tracks, vias, zones, and graphics.
- Regenerates object UUIDs and writes a per-instance placement manifest.

For an optional `.kicad_pro` sidecar, named netclasses and relevant exact assignments are merged into the matching copied project settings file. Block-specific wildcard or anchored-regex rules are resolved only against nets inside that stored layout, then converted to exact assignments. Supported bus-vector expressions are expanded to member nets. Unmatched or unsupported patterns are reported. Net-chain members, terminal-pad UUIDs, and chain-class/netclass associations are remapped. Unrelated board setup is not copied.

The destination is a **separate board copy**. The operation does not overwrite a board currently open in PCB Editor. The destination board must provide the layers used by the source layout. Inspect the merged board and project settings before replacing project files.

## Local-copy synchronization

A local-copy baseline tracks asset revisions:

| Change state | Result |
| --- | --- |
| Changed only in the local copy | Publish the local change to the shared library. |
| Changed only in the shared library | Pull the library change into the local copy. |
| Same change on both sides | Converge without a conflict. |
| Different concurrent changes to the same file | Reject before modifying either side; resolve deliberately. |
| External pin/interface change | Requires explicit migration. |

Implementation files, parameter schema, and named-layout metadata participate in synchronization. Per-instance local paths are relative to the project folder. Synchronization does not perform a graphical three-way merge of schematic content.

## Validation reports

**Validate registered libraries** checks manifests, profile consistency, required assets, dependency references, cycles, hashes, and recursive ERC.

It writes:

- `library-validation.json` — aggregate result and findings.
- Per-block ERC report files — implementation-level ERC output.

Broken manifests are reported as validation errors rather than silently skipped.

## Recursive BOM and ERC

**Export recursive BOM** runs KiCad CLI on the selected block implementation and its nested dependencies, then aggregates the component rows for that block tree. Tokens in implementation text fields are expanded using parameter overrides from the selected root instance; nested dependencies use their declared defaults.

**Run recursive ERC** checks the selected implementation and its nested dependencies with KiCad CLI. These actions are separate from the consuming project's native BOM, netlist, and ERC, which see the facade symbol and its external pins only.

## Locations and settings

| Setting | Location or value |
| --- | --- |
| Default shared library folder | `~/Documents/KiCad/Blocks` |
| Override for new blocks | `KICAD_BLOCKS_LIBRARY=/path/to/library` |
| Persisted library preferences | `~/.config/kicad-blocks/settings.json` |
| Theme override | `KICAD_BLOCKS_THEME=system`, `light`, or `dark` |
| Project-local assets | `<project>/.kicad_blocks/` |
| Native Add Symbol shortcut used for placement handoff | `A` |

## Compatibility

See [compatibility and test coverage](explanation/compatibility.md) for the tested interpreter matrix, supported KiCad API surface, packaging status, and current end-to-end coverage limits.
