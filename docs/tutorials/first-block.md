# Create and place your first block

This tutorial takes one existing hierarchical-sheet circuit and turns it into a reusable library block. At the end, the source design remains intact, the library contains a separate implementation, and a consuming project contains one ordinary facade symbol.

The tutorial assumes that KiCad Blocks is installed and that a shared library folder is registered. Complete [installation and setup](../getting-started.md) first.

## 1. Prepare a hierarchical-sheet circuit

Open a KiCad project that contains a working hierarchical sheet.

The hierarchical labels on the **root sheet you package** become the block's external pins. Labels on child sheets remain internal to the implementation. Use unique root-label names and choose the correct electrical shape for each external connection.

If you want a concrete reference circuit to inspect, see the [STUSB4500 example](../../examples/stusb4500/README.md). It is a generated datasheet-derived schematic example, not a ready-made block library.

## 2. Create the block

1. Open the parent schematic in Eeschema.
2. Select the hierarchical sheet symbol that you want to package.
3. Choose **Create Block from Sheet** from the toolbar. The visible creation form identifies the source sheet.
4. Enter a block name and optional description.
5. Create the block in the configured default shared library.

You can also open the target hierarchical sheet directly and run **Create Block from Sheet** with nothing selected. KiCad Blocks uses the currently open sheet.

The plugin copies the selected sheet and its child schematic files into a new `.kicad_block` folder. It does not remove or rewrite the source project. The root labels become the facade pins; the copied schematic tree becomes the editable implementation.

## 3. Inspect the generated block

Open **Browse and Place Functional Blocks** and select the new entry.

Review:

- The block description and external pin list.
- The facade symbol preview.
- The default implementation and any named variants.
- Declared parameters and named profiles.
- Any stored PCB layout options.

The default implementation is the schematic copied from the selected sheet. Open it to check that the copied design is complete and that all required child sheets and library dependencies are present.

## 4. Inspect the facade in a consuming project

1. Open the project that will consume the block.
2. In Eeschema, choose **Browse and Place Functional Blocks**.
3. Search for the block name or one of its pin, parameter, or implementation names.
4. Select the block and inspect its preview, external pins, variants, and profiles.

The browser can register the selected facade in the consuming project's combined `KBlocks` catalog under `.kicad_blocks/`. The shared library remains in its registered location. The implementation schematic is not copied into the parent project.

**Interactive placement limitation:** on the tested KiCad build, the supported schematic IPC API does not expose the operation required to attach the selected library symbol to the interactive placement cursor. The Place block action reports this capability gap; it does not claim a symbol has been placed. See the [compatibility notes](../explanation/compatibility.md).

## 5. Configure one instance

Select the placed facade and open **KiCad Blocks Tasks → Configure selected instance**.

Choose a named profile or set its implementation variant, typed parameter values, and PCB layout. Save the settings. They are written to that placed symbol instance; other instances and the shared library are unchanged.

For the exact instance fields and supported parameter types, see [per-instance fields and parameters](../reference.md#per-instance-fields).

## 6. Check the result

Run **Validate registered libraries** to check the shared library assets, manifests, dependency graph, revisions, and recursive ERC.

For implementation-level results, select the block instance and run **Export recursive BOM** or **Run recursive ERC**. Native project-level ERC, netlist, and BOM still see only the facade symbol and its external pins.

## Expected result

The shared library contains a reusable `.kicad_block` folder with a facade symbol and an editable implementation schematic tree. The consuming project contains one ordinary symbol instance and its local KiCad Blocks metadata. No child implementation sheet is inserted into the parent project.

Continue with the [how-to guides](../workflows.md) to publish updates, create local copies, compose blocks, and reuse PCB layouts.
