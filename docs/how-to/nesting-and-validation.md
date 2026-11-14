# Compose block dependencies and validate libraries

Child blocks are composed through ordinary schematic symbols. There is no separate nesting command and no manual pin-map form.

## Add a child block to an implementation

1. Open the parent block with **Open Block Implementation**.
2. In Eeschema, start KiCad's normal **Add Symbol** action and choose a facade from the registered **KBlocks** library.
3. Place the child facade and connect its pins using ordinary wires, labels, and junctions.
4. Annotate the schematic if the new symbol has an unresolved reference, then save.
5. Choose **Publish implementation**. KiCad Blocks detects child facades and reads their actual pin-to-net connections from KiCad's exported netlist.

The dependency manifest is derived from the saved schematic. KiCad Blocks records each child reference, placement, orientation, library identity, and pin-to-net mapping. Pin numbers—not matching pin names—associate the netlist with the child's facade interface. An intentionally unconnected pin remains unconnected; no same-name net is invented.

The dependency graph is reconciled when a block is first created, whenever a library implementation is published, when a project-local copy is made or synchronized, before recursive BOM/ERC export, and during registered-library validation. Removing a child facade removes that dependency the next time the parent is reconciled. Dependency cycles and stale or unresolved child facades are reported rather than silently accepted.

The default implementation is the source used for the library-wide dependency graph. Named variants remain separate implementation files.

For the stored data model and reconciliation behavior, see [automatic block composition](../reference.md#block-dependencies-and-automatic-composition).

## Validate registered libraries

1. Choose **KiCad Blocks Tasks → Checks & PCB → Validate registered libraries**.
2. Review the aggregate report.
3. Open the per-block findings for missing assets, invalid manifests, revision/hash errors, profile inconsistencies, unresolved dependencies, cycles, or recursive ERC violations.

Validation checks all registered shared-library roots. It reconciles each saved default implementation before checking the resulting dependency graph. It does not silently skip a block because its manifest is malformed.

The aggregate report is named `library-validation.json`. Per-block ERC reports are written alongside the validation output. See [validation reports](../reference.md#validation-reports).

## Export a recursive BOM

1. Select one placed block instance in Eeschema.
2. Choose **KiCad Blocks Tasks → Checks & PCB → Export recursive BOM**.
3. Inspect the generated BOM for the root implementation and its automatically detected child dependencies.

The action refreshes dependency metadata from the saved implementation before export. The selected root instance's typed parameter overrides are applied to supported text tokens. Nested child instances use the child block's declared parameter defaults.

## Run recursive ERC

1. Select one placed block instance.
2. Choose **KiCad Blocks Tasks → Checks & PCB → Run recursive ERC**.
3. Review the KiCad CLI output and generated report.

The action refreshes dependency metadata from the saved implementation before checking the recursive tree. Recursive BOM/ERC is separate from the consuming project's native BOM, netlist, and ERC. Native project-level outputs see the facade and external pins only; they do not automatically include components in the separately stored implementation.

## Keep the limits in view

A successful library validation does not prove electrical correctness of every downstream design. Review the actual circuit, component selection, layout, and target-board requirements. See [compatibility and test coverage](../explanation/compatibility.md) for known integration-test boundaries.
