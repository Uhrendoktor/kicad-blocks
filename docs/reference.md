# Feature reference

Use this page when you need exact action names, stored filenames, instance fields, parameter rules or technical limits. For step-by-step tasks, see the [workflow guides](workflows.md).

## Contents

- [Terminology](#terminology)
- [Action map](#action-map)
- [Block folder and project files](#block-folder-and-project-files)
- [Facade pins](#facade-pins)
- [Per-instance fields](#per-instance-fields)
- [Parameters and token expansion](#parameters-and-token-expansion)
- [Variants and profiles](#variants-and-profiles)
- [Nested block composition](#nested-block-composition)
- [PCB layout descriptors and merge behavior](#pcb-layout-descriptors-and-merge-behavior)
- [Local-copy synchronization](#local-copy-synchronization)
- [Validation reports](#validation-reports)
- [Recursive BOM and ERC](#recursive-bom-and-erc)
- [Locations and settings](#locations-and-settings)
- [Compatibility and known limits](#compatibility-and-known-limits)

## Terminology

| Term | Meaning |
| --- | --- |
| **Facade symbol** | The ordinary KiCad symbol placed in the project schematic. Its pins are the block's external interface. |
| **Implementation** | The editable schematic tree stored in the shared `.kicad_block` folder. |
| **Variant** | A named choice of implementation schematic for a placed instance. |
| **Profile** | A reusable set of instance settings: variant, parameter overrides and optional PCB layout. |
| **Shared library** | The source folder registered with KiCad Blocks and reused across projects. |
| **Local copy** | An independent project copy of one block implementation, stored under `.kicad_blocks/local/`. |

## Action map

### Eeschema

| Entry point | Action | Selection / context |
| --- | --- | --- |
| Toolbar | **Browse and Place Functional Blocks** | Open a saved project and browse registered shared libraries. |
| Toolbar | **Create Block from Selected Sheet** | Select a hierarchical sheet symbol in its parent, or run with nothing selected from the open sheet. |
| Toolbar | **Open Block Implementation** | Select exactly one placed block instance. |
| KiCad Blocks Tasks | **Configure selected instance** | Select exactly one block instance. |
| Library browser / task hub | **Edit facade symbol** | Select exactly one block instance. |
| KiCad Blocks Tasks | **Publish implementation** | Choose a shared library block to publish. |
| KiCad Blocks Tasks | **Make selected instance local** | Select one library-linked instance. |
| KiCad Blocks Tasks | **Synchronize local copy** | Select one instance already linked to a local copy. |
| KiCad Blocks Tasks | **Nest a block** | Choose a parent block and a child block. |
| KiCad Blocks Tasks | **Validate registered libraries** | Checks all registered shared library roots. |
| KiCad Blocks Tasks | **Export recursive BOM** | Select one block instance. |
| KiCad Blocks Tasks | **Run recursive ERC** | Select one block instance. |
| KiCad Blocks Tasks | **Apply selected PCB layout** | Select one instance with a stored layout. |
| KiCad Blocks Tasks | **Apply multiple PCB layouts** | Select two or more instances with stored layouts. |

### PCB Editor

| Entry point | Action | Context |
| --- | --- | --- |
| PCB Editor action | **Place Imported Block Layout** | Open the generated board copy with a `.kicad-blocks-placement.json` sidecar, then choose one imported instance group. |

The KiCad IPC action manifest does not expose a custom nested menu tree. The three frequent schematic operations are separate toolbar actions; the other commands are grouped in the task hub's **Use & build**, **Edit & maintain** and **Checks & PCB** tabs.

## Block folder and project files

A shared library root contains one facade catalog and one directory per block:

```text
KiCad/Blocks/
  KBlocks.kicad_sym
  Buck_Converter_<id>.kicad_block/
    manifest.json
    block.kicad_sym
    implementation.kicad_sch
    implementation_1v8.kicad_sch       # optional variant
    implementation_3v3.kicad_sch       # optional variant
    layout.kicad_pcb                   # optional default layout
    layout.kicad_pro                   # optional layout settings
```

The manifest records the block identity, symbol name, description, revision and asset hashes, pins, typed parameters, variants, named profiles, layout alternatives and nested dependencies.

When a block is placed, KiCad Blocks creates project-local catalog and instance data:

```text
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
```

The project uses one combined `KBlocks` catalog row. `instances.json` maps each placed symbol KIID to a relative local-copy path. A local copy can use an independent implementation without changing the facade symbol or project wiring.

## Facade pins

The facade is derived from hierarchical labels on the root implementation sheet. Supported electrical types are:

- `input`
- `output`
- `bidirectional` (also accepted as `bidir`)
- `tristate`
- `passive`

Pin numbers follow label declaration order. When a label name is preserved during publishing, its facade pin number, location and graphics are retained. New labels receive generated initial locations. Duplicate root-label names produce one external facade pin. Labels on nested child sheets remain internal.

## Per-instance fields

The facade stores implementation choices on each placed symbol:

| Field | Meaning |
| --- | --- |
| `KB_VARIANT` | Exact implementation-variant value. |
| `KB_PROFILE` | Selected named profile ID; cleared when a linked setting is edited manually. |
| `KB_LAYOUT` | Selected PCB layout ID. |
| `KB_PARAM_<NAME>` | Typed override for a declared block parameter. |

Edit these through **KiCad Blocks Tasks → Configure selected instance** or KiCad's **Symbol Properties → Fields** page. The fields belong to the selected instance, not to the shared library as a whole.

## Parameters and token expansion

Parameter declarations are comma-separated. For example:

```text
OUTPUT_VOLTAGE:number=3.3, CHANNELS:integer=4, ENABLE:boolean=true, PART=STM32
```

Supported kinds are text (the default), number, integer and boolean. A token such as `{{OUTPUT_VOLTAGE}}` in a quoted text field in the implementation schematic (for example a component Value field) is substituted during recursive BOM/ERC processing.

The selected root instance's overrides are applied to its implementation. Nested dependency instances currently use their declared defaults. Parameters do not evaluate arbitrary expressions, change circuit connectivity or dynamically reconfigure the top-level schematic.

## Variants and profiles

A variant maps a `KB_VARIANT` string to an alternative implementation schematic.

- Matching is case-insensitive but otherwise exact.
- If a block declares variants, an empty, unresolved or unknown value is an error. The plugin does not guess a default.
- A block without variants uses `implementation.kicad_sch`.

A named profile is a reusable partial configuration containing a profile name and ID, a variant value, optional typed parameter overrides and an optional PCB layout ID. Parameters not specified by the profile inherit block defaults.

Applying a profile writes the profile, variant, parameter and layout fields to the selected facade in one schematic IPC transaction. Editing a linked field turns the instance into a custom configuration and clears `KB_PROFILE`.

## Nested block composition

Each nested dependency occurrence records a unique instance ID, reference, placement, orientation (0°, 90°, 180° or 270°) and child-pin-to-parent-net mapping. KiCad Blocks composes the rotated child facade and labels at the transformed pin endpoints in the parent implementation.

The child remains a separate library asset. Cyclic dependencies are rejected. The child implementation is not created as a native hierarchical sheet; its facade is composed into the parent implementation.

## PCB layout descriptors and merge behavior

A block may expose multiple named `.kicad_pcb` layouts. Each descriptor identifies its ID, display name and board file; it may also name an optional `.kicad_pro` sidecar and a map from facade pins to layout nets. Unless the manifest specifies another default, the first layout ID is selected by default.

The merge operation:

- maps facade pins to named layout nets;
- namespaces internal nets and footprint references;
- transforms board objects around the declared anchor and rotates them counter-clockwise;
- copies footprints, tracks, vias, zones and graphics;
- regenerates object UUIDs and writes a per-instance placement manifest.

For an optional `.kicad_pro` sidecar, named netclasses and relevant exact assignments are merged into a matching copied project settings file. Block-specific wildcard or anchored-regex rules are resolved only against nets inside that stored layout, then converted to exact assignments. Supported bus-vector expressions are expanded to member nets. Unmatched or unsupported patterns are reported. Net-chain members, terminal-pad UUIDs and chain-class/netclass associations are remapped. Unrelated board setup is not copied.

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

Implementation files, parameter schema and named-layout metadata participate in synchronization. Per-instance local paths are relative to the project folder.

## Validation reports

**Validate registered libraries** checks manifests, profile consistency, required assets, dependency references, cycles, hashes and recursive ERC.

It writes:

- `library-validation.json` — aggregate result and findings.
- Per-block ERC report files — implementation-level ERC output.

Broken manifests are reported as validation errors rather than silently skipped.

## Recursive BOM and ERC

**Export recursive BOM** runs KiCad CLI on the selected block implementation and its nested dependencies, then aggregates the component rows for that block tree. Tokens in implementation text fields are expanded using parameter overrides from the selected root instance; nested dependencies use their declared defaults.

**Run recursive ERC** checks the selected implementation and its nested dependencies with KiCad CLI. These actions are separate from the project's native BOM, netlist and ERC, which see the facade symbol and its external pins only.

## Locations and settings

| Setting | Location or value |
| --- | --- |
| Default shared library folder | `~/Documents/KiCad/Blocks` |
| Override for new blocks | `KICAD_BLOCKS_LIBRARY=/path/to/library` |
| Persisted library preferences | `~/.config/kicad-blocks/settings.json` |
| Theme override | `KICAD_BLOCKS_THEME=system|light|dark` |
| Project-local assets | `<project>/.kicad_blocks/` |
| Native Add Symbol shortcut used for placement handoff | `A` |

## Compatibility and known limits

Validated V1.1.0 target: KiCad **10.99.0 development nightly**, Linux **x86_64**, CPython **3.12, 3.13 and 3.14**. Stable KiCad 10.x, KiCad 11 stable, Windows, macOS and ARM are not validated.

Current functional limits:

- Native project-level BOM, netlist and ERC see the facade symbol only. Use recursive BOM/ERC for the internal circuitry.
- A variant selects the implementation to open. It does not dynamically change the parent schematic or arbitrary nested instances.
- Nested dependencies are composed inline into the parent implementation schematic; they are not native nested sheet symbols.
- The IPC action manifest does not support custom nested menu trees.
- The imported PCB layout action uses KiCad's native cursor tool; the complete mouse-driven commit/cancel sequence in PCB Editor is not automated in CI.
- The Symbol Editor shortcut handoff is unit-tested, but a complete live GUI save/edit/publish sequence is not yet part of CI.
- Recursive extraction is tested with a two-level fixture and KiCad CLI validation. A manual GUI exercise on a complex multi-sheet project remains recommended.
