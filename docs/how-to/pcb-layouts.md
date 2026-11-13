# Manage and apply PCB layouts

A block can store multiple named pre-routed PCB layouts. A layout can be associated with the default implementation or a named variant. Applying it creates a new board copy; it does not modify an already open project board.

## Add a stored layout

1. Open **Browse and Place Functional Blocks** and select the block.
2. Choose **Manage PCB layouts**.
3. Choose **Add layout**.
4. Enter a layout name.
5. Choose **Create a new empty board** or **Choose an existing PCB file**.
6. When using an existing file, choose the `.kicad_pcb` source file.
7. Set **Use for implementation** to **Default** or a named variant.
8. Choose **Add layout**, then save the layout manager.

In blank-board mode, the PCB file entry and **Choose file** button are hidden. They appear only when you choose an existing PCB file.

An optional matching `.kicad_pro` project-settings sidecar can be imported with the layout. The plugin copies the selected PCB assets into the block library; it does not use the source board in place.

## Link a layout to an implementation

The PCB layout manager shows each stored board, its linked implementation, and its PCB filename.

1. Select a layout row.
2. Set **Assign selected layout to** to **Default**, a named variant, or **No variant link**.
3. Choose **Link selected**.
4. Use **Set default** when the selected layout should be the fallback for the block.
5. Choose **Save layouts**.

**Default** is used when no `KB_VARIANT` is set. A named variant can use its own linked layout. **No variant link** clears that layout's variant-specific links; it does not clear the block's default-layout selection.

The browser's **Open PCB layout** action opens a stored board for inspection. **Manage PCB layouts** changes the library's stored files and implementation links. These are separate operations.

## Apply a layout to a project

Before applying a layout, enable the IPC API in PCB Editor and confirm that the destination board contains every layer used by the stored layout.

1. In Eeschema, select one eligible block instance and choose **Apply selected PCB layout**. To apply more than one instance in one operation, select the eligible instances and choose **Apply multiple PCB layouts**.
2. Choose the layout or implementation option requested by the dialog.
3. KiCad Blocks creates a separate copy of the project board and merges the selected block layout data into that copy.
4. Open the generated board copy in PCB Editor.
5. Choose **Place Imported Block Layout**.
6. Select the imported instance group and move it with KiCad's native placement tool.
7. Click to commit the placement. Press **Esc** to cancel the active placement.

The original project board is not overwritten and is not edited in place. Review the generated board and project settings before adopting them.

## Merge behavior and limitations

The merge maps facade pins to layout nets, namespaces internal nets and footprint references, transforms geometry around the declared anchor, and copies supported footprints, tracks, vias, zones, and graphics. An optional project-settings sidecar can contribute named netclasses and safe net assignments. Unmatched or unsupported rules are reported rather than broadly copied.

The target board must already contain the required layers. Unrelated global board setup is not imported. The full mouse-driven commit/cancel sequence is not yet covered end to end by CI. See [PCB layout descriptors and merge behavior](../reference.md#pcb-layout-descriptors-and-merge-behavior).
