# Architecture and design model

KiCad Blocks separates a circuit's reusable implementation from the symbol that appears in a consuming project's schematic. This page explains that boundary and the consequences for variants, local copies, nested dependencies, and PCB layouts.

## Facade and implementation

A block has two main parts:

- The **facade symbol** is an ordinary KiCad library symbol. Its pins are the block's external electrical interface.
- The **implementation** is an editable hierarchical schematic tree stored in a shared `.kicad_block` library directory.

When a block is created from a selected hierarchical sheet, KiCad Blocks copies that sheet and its child sheets into the library. It derives facade pins from the hierarchical labels on the root sheet. Child-sheet labels remain internal. The original project is left unchanged.

A consuming project gets a combined `KBlocks.kicad_sym` catalog under `.kicad_blocks/` and a normal symbol instance in the schematic. The implementation tree is not inserted as a child sheet in that project.

## What the parent project sees

The facade is the boundary between the consuming project and the stored implementation.

Native project-level schematic ERC, netlist, and BOM see the facade symbol and its external pins. They do not automatically traverse the separately stored implementation. Use KiCad Blocks' recursive ERC and BOM actions to inspect the stored implementation and nested dependency tree.

This distinction is intentional: the project schematic stays compact and the library remains the source of the complete circuit. It also means the designer must validate the hidden implementation with the recursive tools and review that the facade accurately represents the intended external interface.

## Variants, profiles, and parameters

These concepts answer different questions:

- A **variant** chooses which complete schematic implementation is associated with a placed instance.
- A **profile** is a named preset that can select a variant, typed parameter overrides, and an optional PCB layout.
- A **parameter** is a declared typed value. Tokens in supported quoted implementation text fields can be expanded during recursive BOM/ERC processing.

A variant does not dynamically rewrite the parent schematic or automatically alter arbitrary nested instances. A parameter does not evaluate arbitrary expressions or change electrical connectivity. Parameters are metadata and supported text-token substitutions, not a general circuit simulator or schematic compiler.

The fields are stored on each placed facade, so two instances of the same library block can use different configurations.

## Shared libraries and local copies

The shared library is the source used to create and publish reusable blocks. Projects can register one or more shared library roots, then browse them from different projects.

A project can also convert one placed shared-library instance into a local copy. The local implementation is stored under `.kicad_blocks/local/` and linked to the facade's KiCad symbol KIID through project metadata. The facade, reference, position, and wiring remain associated with that instance.

Synchronization compares local and shared assets against a common baseline. One-sided changes can be published or pulled. Identical changes converge. Different changes to the same tracked file stop for deliberate resolution; the plugin does not attempt a graphical three-way schematic merge.

## Automatic block composition

Child blocks are ordinary facade symbols inside a parent's editable implementation. When opening an implementation, KiCad Blocks registers the facade catalog from all configured shared libraries so the designer can use Eeschema's normal **Add Symbol** command. The designer places a child facade and connects its pins with ordinary schematic wires and labels.

The saved schematic is the source of truth. On publish—and before recursive BOM/ERC export or registered-library validation—KiCad Blocks exports the implementation's KiCad XML netlist, recognizes each placed `KBlocks` facade by library symbol ID, and maps its manifest pin numbers to the actual net names. It derives reference, position, orientation, and connectivity from the schematic, then rebuilds the parent's dependency metadata. A pin with no net remains unconnected; pin names are never used to guess connectivity.

Removing a child facade removes its dependency the next time the parent is reconciled. Existing child occurrences retain their IDs when the child and reference remain the same. Missing or unresolved facades, reference or pin-interface mismatches, and dependency cycles are reported as errors. This mechanism does not add global labels, alter the designer's wiring, or create native hierarchical sheet symbols. The child implementation stays a separate, reusable library asset.

The library-wide dependency graph currently follows the default implementation. Named variants remain separate implementation files.

## PCB layouts

A block can contain multiple named PCB board files, optional KiCad project settings, and pin-to-net mappings. The default layout can be selected for the block; named variants can have their own associated layouts.

Applying a block layout maps the facade interface to board nets and merges supported board objects into a separate copy of the destination project board. The output is intentionally separate so the source board is not silently overwritten. The destination must already contain the layers required by the imported layout.

## When this model is a good fit

Use KiCad Blocks when a circuit should be shared as a library asset, represented by a normal symbol in each project, configured independently per instance, and maintained through explicit library/local synchronization.

Use native hierarchical sheets or KiCad's native Design Blocks when the design should remain inside the project and the native project-level tools should operate directly on its full hierarchy. These tools solve related but different problems.
