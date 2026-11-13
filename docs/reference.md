# Feature and data reference

A compact technical reference for the UI actions, block assets, per-instance settings and supported boundaries.

## Action map

### Schematic editor

| Entry point | Action | Selection / context |
| --- | --- | --- |
| Toolbar | **Browse and Place Functional Blocks** | Open a saved project and browse registered shared libraries. |
| Toolbar | **Create Block from Selected Sheet** | Select one hierarchical sheet symbol in its parent, or run with nothing selected from the open hierarchical sheet. |
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
| KiCad Blocks Tasks | **Apply selected PCB layout** | Select one block instance with a stored layout. |
| KiCad Blocks Tasks | **Apply multiple PCB layouts** | Select two or more block instances with stored layouts. |

### PCB editor

| Entry point | Action | Context |
| --- | --- | --- |
| PCB Editor action | **Place Imported Block Layout** | Open the generated board copy with a `.kicad-blocks-placement.json` sidecar. Choose one imported instance to move. |

The task hub tabs are **Use & build**, **Edit & maintain** and **Checks & PCB**. The IPC action manifest does not expose arbitrary nested menu/group registration; the three frequent schematic commands are individual toolbar actions, with the remaining commands grouped in the task hub.

## Block folder format

A shared library root contains a stable facade catalog and one directory per block:

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

A manifest records block identity, symbol name, human-readable name/description, revision and asset hashes, pins, typed parameters, implementation variants, named profiles, layout alternatives and nested dependencies.

When a block is placed in a project, KiCad Blocks creates:

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

The project uses one combined `KBlocks` catalog row. `instances.json` maps a placed schematic symbol KIID to a relative local-copy path. A local copy can use an independent implementation without changing the facade symbol or project wiring.

## Facade pins

The facade is derived from root implementation hierarchical labels. Supported electrical types are:

- `input`
- `output`
- `bidirectional` (also accepted as `bidir`)
- `tristate`
- `passive`

Pin numbers follow label declaration order. When a label name is preserved during publishing, its facade pin number, location and graphics are retained. New labels receive generated initial locations. Duplicate root label names produce one external facade pin. Nested child-sheet labels remain internal.

## Per-instance fields

The facade stores implementation choices on each placed symbol:

| Field | Meaning |
| --- | --- |
| `KB_VARIANT` | Exact implementation-variant value. |
| `KB_PROFILE` | Selected named profile ID; cleared when a linked setting is edited manually. |
| `KB_LAYOUT` | Selected PCB layout ID. |
| `KB_PARAM_<NAME>` | Typed override for a declared block parameter. |

These fields can be edited through **KiCad Blocks Tasks → Configure selected instance** or through KiCad's native **Symbol Properties → Fields** page. They belong to the selected instance, not the shared library as a whole.

## Parameters and token expansion

Parameter declarations are comma-separated. Example:

```text
OUTPUT_VOLTAGE:number=3.3, CHANNELS:integer=4, ENABLE:boolean=true, PART=STM32
```

Supported kinds include text (the default), number, integer and boolean. A token such as `{{OUTPUT_VOLTAGE}}` in a quoted text field of the implementation schematic is substituted during recursive BOM/ERC processing.

The selected root instance's parameter overrides are applied to its implementation. Nested dependency instances currently use their declared defaults. The mechanism does not evaluate arbitrary expressions, change circuit connectivity or dynamically reconfigure the top-level schematic.

## Variants and profiles

A variant maps a `KB_VARIANT` string to an implementation schematic. Matching is case-insensitive but otherwise exact. If a block declares variants, unresolved, empty or unknown values are errors; the plugin does not guess a default implementation. A block without variants uses `implementation.kicad_sch`.

A named profile is a reusable partial configuration containing a profile name and ID, an exact variant value, optional typed parameter overrides and an optional PCB layout ID. Parameters not specified by the profile inherit block defaults. Applying a profile writes profile, variant, parameter and layout fields to the selected facade in one schematic IPC transaction. Editing a linked field converts the instance to a custom configuration and clears `KB_PROFILE`.

## PCB layout descriptors and merge behavior

A block may expose multiple layouts. A layout descriptor identifies its ID, display name and `.kicad_pcb` file; it may also name an optional `.kicad_pro` sidecar and a facade-port-to-layout-net map. The first layout ID is the default unless the manifest specifies another.

The merge operation:

- maps facade pins to named layout nets;
- namespaces internal nets and footprint references;
- transforms board objects around the declared anchor and rotates them counter-clockwise;
- copies footprints, tracks, vias, zones and graphics;
- regenerates UUIDs and writes the per-instance placement manifest.

For an optional `.kicad_pro` sidecar, named netclasses and relevant exact assignments are merged into a matching copied project settings file. Block-specific wildcard/anchored-regex rules are resolved only against nets inside that stored layout, then converted to exact assignments. Supported bus-vector expressions are expanded to real member nets. Unmatched/unsupported patterns are reported. Net-chain members, terminal-pad UUIDs and chain-class/netclass associations are remapped. Unrelated board setup is not copied.

The destination is a separate board copy. The operation does not overwrite a board currently open in PCB Editor. The destination board must provide the layers used by the source layout. Inspect the merged board and project settings before replacing project files.

## Library and local-copy synchronization

The local-copy baseline tracks asset revisions:

| Change state | Result |
| --- | --- |
| Changed only in local copy | Publish local change to the shared library. |
| Changed only in shared library | Pull the library change into the local copy. |
| Same change on both sides | Converge without a conflict. |
| Different concurrent changes to the same file | Reject before mutating either side; resolve deliberately. |
| External pin/interface change | Requires explicit migration. |

Implementation files, parameter schema and named-layout metadata participate in synchronization. Per-instance local paths are relative to the project folder.

## Library validation and reports

**Validate registered libraries** checks manifests, profile consistency, required assets, dependency references, cycles, hashes and recursive ERC. It writes:

- `library-validation.json` — aggregate result and findings.
- Per-block ERC report files — implementation-level ERC output.

Broken manifests are surfaced as validation errors rather than silently skipped.

## UI and configuration

- Default shared library folder: `~/Documents/KiCad/Blocks`.
- Default-folder override: `KICAD_BLOCKS_LIBRARY=/path/to/library`.
- Persisted library preferences: `~/.config/kicad-blocks/settings.json`.
- Theme override: `KICAD_BLOCKS_THEME=system|light|dark`.
- Project-local assets: `<project>/.kicad_blocks/`.
- KiCad Add Symbol shortcut used by native placement handoff: `A`.

## Compatibility and known boundaries

Validated V1.1.0 target: KiCad **10.99.0 development nightly**, Linux **x86_64**, CPython **3.12, 3.13 and 3.14**. Other operating systems, architectures, interpreter ABIs, stable KiCad 10.x and KiCad 11 stable are not validated.

Additional boundaries:

- Native project-level BOM/netlist/ERC sees the facade symbol only; use recursive BOM/ERC for internal circuitry.
- A variant selects the implementation to open. It does not dynamically change the parent schematic or arbitrary nested instances.
- Nested dependencies are composed inline into the parent implementation schematic; they are not native nested sheet symbols.
- The IPC action manifest does not support a custom nested menu tree.
- The imported PCB layout move uses KiCad's native cursor tool; current CI does not automate the full mouse-driven commit/cancel sequence in PCB Editor.
- Symbol Editor's native shortcut handoff is unit-tested, but a full live GUI save/edit/publish sequence is not yet part of CI.
- Recursive hierarchical extraction is tested with a two-level fixture and KiCad CLI validation; a manual GUI exercise on a complex multi-sheet project remains recommended.
