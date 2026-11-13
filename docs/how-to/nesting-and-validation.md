# Nest blocks and validate libraries

Use nesting to build a larger implementation from reusable child blocks. Use validation and recursive exports to inspect the stored implementation graph rather than only the facade symbols in a parent schematic.

## Nest a block

1. Choose **KiCad Blocks Tasks → Edit & maintain → Nest a block**.
2. Select the parent block and the child block.
3. Map each child facade pin to the intended net in the parent implementation.
4. Set the child placement and orientation.
5. Save the composition and validate the updated library.

Each child occurrence is stored as a library dependency with a unique instance ID, reference, position, orientation, and pin-to-net map. Supported orientations are 0°, 90°, 180°, and 270°. The child facade and its internal labels are composed into the parent implementation; the plugin does not create a native child hierarchical sheet. Dependency cycles are rejected.

For the data model and exact dependency semantics, see [nested block composition](../reference.md#nested-block-composition).

## Validate registered libraries

1. Choose **KiCad Blocks Tasks → Checks & PCB → Validate registered libraries**.
2. Review the aggregate report.
3. Open the per-block findings for missing assets, invalid manifests, revision/hash errors, profile inconsistencies, dependency problems, or recursive ERC violations.

Validation checks all registered shared-library roots. It does not silently skip a block because its manifest is malformed.

The aggregate report is named `library-validation.json`. Per-block ERC reports are written alongside the validation output. See [validation reports](../reference.md#validation-reports).

## Export a recursive BOM

1. Select one placed block instance in Eeschema.
2. Choose **KiCad Blocks Tasks → Checks & PCB → Export recursive BOM**.
3. Inspect the generated BOM for the root implementation and its nested dependencies.

The selected root instance's typed parameter overrides are applied to supported text tokens in the implementation. Nested child instances use the child block's declared parameter defaults.

## Run recursive ERC

1. Select one placed block instance.
2. Choose **KiCad Blocks Tasks → Checks & PCB → Run recursive ERC**.
3. Review the KiCad CLI output and generated report.

Recursive BOM/ERC is separate from the consuming project's native BOM, netlist, and ERC. Native project-level outputs see the facade and external pins only; they do not automatically include components in the separately stored implementation.

## Keep the limits in view

A successful library validation does not prove electrical correctness of every downstream design. Review the actual circuit, component selection, layout, and target-board requirements. See [compatibility and test coverage](../explanation/compatibility.md) for known integration-test boundaries.
