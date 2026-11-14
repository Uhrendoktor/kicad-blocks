# Create, find, and place blocks

Use this guide to turn a hierarchical sheet into a library block or place an existing block in a project. For the complete first-use sequence, see the [first-block tutorial](../tutorials/first-block.md).

## Create a block from a sheet

1. Open the parent schematic in Eeschema and select the hierarchical sheet symbol to package. Alternatively, open the target sheet itself and run the command with no selection.
2. Choose **Create Block from Sheet**. The creation form opens in a visible plugin window and identifies the source sheet.
3. Enter the required block name and optional description.
4. Create the block in the configured default shared library.

KiCad Blocks copies the selected sheet and its child schematic files into a new `.kicad_block` directory. It leaves the source project unchanged. The root sheet's hierarchical labels become the facade pins; child-sheet labels remain internal.

Open the generated block in the browser and review the pin list and implementation before sharing it. See [facade pin rules](../reference.md#facade-pins).

## Register an existing library

Open **Browse and Place Functional Blocks**, then choose **Manage Libraries**.

- Choose **Add library** to register an existing folder.
- Choose **Create library** to create an empty folder.
- Choose **Change folder** to point a registration at another location.
- Choose **Set default** to select the destination for newly created blocks.
- Choose **Unregister** to remove a non-default folder from the list without deleting its files. The active default library cannot be unregistered; use **Change folder** to migrate that registration.

The browser searches all registered libraries. A folder that contains no valid `.kicad_block` assets will not show usable entries.

## Find and place a block

1. Open a saved project in Eeschema.
2. Choose **Browse and Place Functional Blocks**.
3. Type in the search field. Search covers the block name, description, pin names, declared parameters, variants, and layout names.
4. Select a result to inspect its description, facade, pins, configurable settings, implementations, profiles, and PCB layout options.
5. Review the intended implementation and configuration.

The project receives a combined `KBlocks.kicad_sym` catalog and `.kicad_blocks/` metadata when the library entry is registered. The shared library stays in its original folder; implementation schematics are not copied into the parent project.

**Interactive placement limitation:** on the tested KiCad build, the supported schematic IPC API does not expose a way for the plugin to attach the selected library symbol to KiCad's interactive placement cursor. The Place block action reports this capability gap instead of opening an unrelated chooser or injecting desktop input. This part of the workflow remains unavailable until KiCad exposes the required API.

## Open or manage a stored PCB layout

The browser has two distinct actions:

- **Open PCB layout** opens a stored board asset in PCB Editor for inspection.
- **Manage PCB layouts** opens the block's layout manager, where you can add, remove, set defaults, and link layouts to implementations.

To apply a stored board layout to a project, use the **Apply selected PCB layout** task. Follow [manage and apply PCB layouts](pcb-layouts.md) for the full process.
