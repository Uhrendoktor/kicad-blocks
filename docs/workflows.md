# Workflow guides

Use these guides when you know what you want to do. Each section describes the task, the steps in KiCad and what changes as a result. Exact filenames, field names and implementation rules are in the [feature reference](reference.md).

## Where to find the commands

The three common Eeschema actions are available as toolbar buttons:

- **Browse and Place Functional Blocks**
- **Create Block from Selected Sheet**
- **Open Block Implementation**

Other commands are grouped in **Tools → External Plugins → KiCad Blocks Tasks**:

| Tab | Use it for |
| --- | --- |
| **Use & build** | Browse, place and create blocks. |
| **Edit & maintain** | Configure an instance, edit or publish a block, create a local copy, synchronize it or nest another block. |
| **Checks & PCB** | Validate libraries, export a recursive BOM, run recursive ERC and apply stored PCB layouts. |

PCB Editor has a separate **Place Imported Block Layout** action for moving a previously imported layout group.

## Create a block from a hierarchical sheet

Use this when you have a working circuit in a project and want to make it available from a shared library.

1. Open the parent schematic and select the hierarchical sheet symbol you want to package. Alternatively, open that sheet itself and run the command with nothing selected.
2. Choose **Create Block from Selected Sheet**.
3. Enter the block name and an optional description.
4. Choose the shared library folder.

KiCad Blocks copies the selected sheet and its child sheets into a new `.kicad_block` folder. The original project is not modified.

The root sheet's hierarchical labels define the block's external pins. Child-sheet labels stay inside the implementation. Open **Edit implementation** in the browser to continue editing the copied circuit with normal KiCad schematic tools.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/create-block-form-light.png" alt="Create Functional Block form for naming a new block and choosing its library destination" width="700">
</p>

## Find and place a block

1. Open a project in Eeschema and choose **Browse and Place Functional Blocks**.
2. Search by block name, description, pin name, parameter or variant.
3. Select a result. The details pane shows its interface, implementation choices, parameters and available PCB layouts.
4. Select the required variant or named profile.
5. Choose **Place at cursor** or double-click the result.
6. Confirm the selected facade in KiCad's native **Choose Symbol** dialog and place it.

The placed facade is a normal schematic symbol. KiCad Blocks creates or updates the project's combined `KBlocks` symbol catalog under `.kicad_blocks/`; the shared library stays in its registered folder.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-details-light.png" alt="Library browser showing the STUSB4500 block, its ten-pin facade and available implementation choices" width="900">
</p>

## Configure one placed instance

Use a configuration profile when you want a known set of values. Use custom values when this instance needs something different.

**Before placement:** choose the variant or profile in the placement chooser.

**After placement:** select one facade symbol and choose **Configure selected instance** in the task hub. Select a profile or edit its variant, parameters and PCB layout, then save.

The values belong to the selected symbol instance. Changing one placed block does not change the shared library or other instances in the project.

For exact field names and parameter behavior, see [Per-instance fields](reference.md#per-instance-fields), [Parameters](reference.md#parameters-and-token-expansion) and [Variants and profiles](reference.md#variants-and-profiles).

## Edit a shared block and publish the changes

Use this when the implementation should be updated for everyone who uses the shared library.

1. Open the block from the browser, or select a placed instance and choose **Open Block Implementation**.
2. Edit the implementation in Eeschema and save it.
3. Choose **KiCad Blocks Tasks → Edit & maintain → Publish implementation**.
4. Select the block when prompted.

Publishing updates the shared block revision and refreshes its facade from the root hierarchical labels. Existing pin artwork is retained for labels that keep the same names; new labels become new facade pins.

A changed external pin interface may require migration in projects that already use the block. Review the pin list before publishing an interface change.

## Customize a block in one project

Use a local copy when a project needs a different implementation but the shared library should stay unchanged.

1. Select one placed, library-linked block instance.
2. Choose **Make selected instance local**.
3. Open and edit that project's local implementation.
4. Choose **Synchronize local copy** when you want to compare it with the shared version.

The local implementation is stored under the project's `.kicad_blocks/local/` directory. The facade symbol, instance identity, position and wiring remain in place.

Synchronization can publish local-only changes or pull library-only changes. When both sides changed the same file in different ways, synchronization stops before overwriting either copy. Resolve that conflict deliberately. An external pin/interface change needs an explicit migration.

## Nest one block inside another

Use nesting to compose a larger subsystem from blocks that remain separate library assets.

1. Open **KiCad Blocks Tasks → Edit & maintain → Nest a block**.
2. Choose the parent block and the child block.
3. Map the child pins to nets in the parent implementation and set the child's placement and orientation.
4. Save the composition and validate the resulting library.

The child facade is composed into the parent implementation. The child remains a separate library dependency; it is not copied into a new child hierarchical sheet. Cyclic dependencies are rejected. See [nested block composition](reference.md#nested-block-composition) for the stored dependency data.

## Validate a library or inspect a block's contents

Choose **KiCad Blocks Tasks → Checks & PCB → Validate registered libraries** to scan the registered libraries. Validation checks manifests, required files, revision hashes, profiles, dependencies and recursive ERC. It writes an aggregate `library-validation.json` report and per-block ERC reports.

For one placed block instance, **Export recursive BOM** exports the implementation and its nested dependencies; **Run recursive ERC** checks them with KiCad CLI. See [recursive BOM and ERC](reference.md#recursive-bom-and-erc) for how parameters and nested dependencies are handled.

Native project BOM, netlist and ERC operate on the facade symbol and its external pins. They do not automatically include components in the separately stored implementation. Use the recursive actions when you need implementation-level results.

## Apply a stored PCB layout

A block can include one or more pre-routed `.kicad_pcb` layouts.

1. In Eeschema, select one block instance and choose **Apply selected PCB layout**. To apply several at once, select multiple eligible block instances and choose **Apply multiple PCB layouts**.
2. KiCad Blocks creates a **separate copy** of the project board and merges the selected layouts into it.
3. Open that generated board copy in PCB Editor.
4. Choose **Place Imported Block Layout** and select the imported instance group.
5. Move the group with KiCad's native tool and click to place it. Press **Esc** to cancel.

The merge does not modify a board already open in PCB Editor. The destination board must contain the layers used by the stored layout. Review the copied board and project settings before adopting them.

For merge mapping, net handling and supported PCB objects, see [PCB layout descriptors and merge behavior](reference.md#pcb-layout-descriptors-and-merge-behavior).
