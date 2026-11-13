# Create, find, and place blocks

Use this guide to turn a hierarchical sheet into a library block or place an existing block in a project. For the complete first-use sequence, see the [first-block tutorial](../tutorials/first-block.md).

## Create a block from a sheet

1. Open the parent schematic in Eeschema and select the hierarchical sheet symbol to package. Alternatively, open the target sheet itself and run the command with no selection.
2. Choose **Create Block from Sheet**.
3. Enter the name and optional description.
4. Choose the shared library destination.

KiCad Blocks copies the selected sheet and its child schematic files into a new `.kicad_block` directory. It leaves the source project unchanged. The root sheet's hierarchical labels become the facade pins; child-sheet labels remain internal.

Open the generated block in the browser and review the pin list and implementation before sharing it. See [facade pin rules](../reference.md#facade-pins).

## Register an existing library

Open **Browse and Place Functional Blocks**, then choose **Manage Libraries**.

- Choose **Add library** to register an existing folder.
- Choose **Create library** to create an empty folder.
- Choose **Change folder** to point a registration at another location.
- Choose **Set default** to select the destination for newly created blocks.
- Choose **Unregister** to remove a folder from the list without deleting its files.

The browser searches all registered libraries. A folder that contains no valid `.kicad_block` assets will not show usable entries.

## Find and place a block

1. Open a saved project in Eeschema.
2. Choose **Browse and Place Functional Blocks**.
3. Type in the search field. Search covers the block name, description, pin names, declared parameters, variants, and layout names.
4. Select a result to inspect its description, facade, pins, configurable settings, implementations, profiles, and PCB layout options.
5. Choose an implementation variant or named profile when the block provides one.
6. Choose **Place block** or double-click the selected row.
7. Confirm the facade in KiCad's native **Choose Symbol** dialog and place it with the normal KiCad tool.

The browser minimizes before placement so the Eeschema canvas remains visible. Restore it from the taskbar after placement or cancellation.

The project receives a combined `KBlocks.kicad_sym` catalog and `.kicad_blocks/` metadata. The shared library stays in its original folder; implementation schematics are not copied into the parent project.

## Open or manage a stored PCB layout

The browser has two distinct actions:

- **Open PCB layout** opens a stored board asset in PCB Editor for inspection.
- **Manage PCB layouts** opens the block's layout manager, where you can add, remove, set defaults, and link layouts to implementations.

To apply a stored board layout to a project, use the **Apply selected PCB layout** task. Follow [manage and apply PCB layouts](pcb-layouts.md) for the full process.
