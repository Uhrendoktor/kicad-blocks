# Edit, publish, and synchronize implementations

Use a shared implementation when a change should be available to every project that uses the library. Use a project-local copy when one project needs an independent implementation.

## Edit the default implementation or a variant

1. Open **Browse and Place Functional Blocks** and select the block, or select a placed facade and choose **Open Block Implementation**.
2. In the browser, choose **Implementations**.
3. Select the **Default** row or a named variant.
4. Choose **Edit default** or **Edit variant**.
5. Edit the schematic in Eeschema and save it.

The manager shows exactly one **Default** row for the active default schematic, followed by the named alternatives. It does not expose a second Base row. **Edit default** opens the schematic currently represented by the Default row.

To add a named implementation, choose **Add variant** in the Implementations dialog. Give it a unique variant value and select an existing schematic, create a blank schematic, or copy another library implementation. The accepted operation is saved immediately and the new row appears at once. There is no separate save step. A failed write is shown in the manager and Close retries it; the window stays open until the change is stored.

## Edit Facade availability

1. Open **Browse and Place Functional Blocks** and select the block.
2. Choose **Edit facade**.
3. The action validates the generated symbol in the project-local KBlocks catalog and reports that the current KiCad GUI IPC does not support opening Symbol Editor and selecting an exact library entry.

The action does not change pin geometry or write to the project catalog. It does not use unstable `RunAction`, global keyboard injection, or an OS-specific window-control fallback. Full native Symbol Editor access remains an upstream API requirement.

## Publish a shared implementation and facade

1. Open the block's implementation from the browser, or select the **Default** or named implementation in **Implementations**.
2. Edit the schematic in Eeschema and save it.
3. Return to **Functional Block Library**. The block remains selected; click **Publish**.
4. The browser reports the new revision, how many placed instances were refreshed, and whether any pin migrations are required.

Publish can import project-catalog facade changes that were made through a separately available Symbol Editor session. The Edit Facade button in KiCad Blocks does not currently open that editor; the action reports the upstream GUI IPC limitation. Publishing still updates the shared library revision, synchronizes facade pins from the implementation's root hierarchical labels, rebuilds child dependencies, and updates the project catalog.

When existing pin numbers, units, body styles, and connection coordinates remain unchanged, already-placed instances are refreshed in place. Pin names, electrical types, and artwork can update without changing connectivity. When pins are added, removed, renumbered, or moved, affected instances are left untouched and identified for explicit migration. The plugin does not silently detach or remap nets.

Publishing a shared library update does not overwrite a project's independent local implementation.

## Create a project-local copy

1. Select exactly one placed, library-linked facade.
2. Run **Make selected instance local** from the instance's KiCad Blocks action.
3. Open the implementation for that instance and edit the local schematic.

The local implementation is stored under the project's `.kicad_blocks/local/` directory. The facade, symbol KIID, reference, position, and wiring remain associated with the placed instance.

## Synchronize a local copy

1. Select the instance that is linked to a local copy.
2. Run **Synchronize local copy** from the selected instance's KiCad Blocks action.
3. Review the non-modal status line/log summary for what was published, pulled, or found to have converged.

Synchronization compares tracked assets with the common baseline:

| Change state | Result |
| --- | --- |
| Only the local copy changed | Publish those local changes to the shared library. |
| Only the shared library changed | Pull the library changes into the local copy. |
| Both sides made the same change | Converge without a conflict. |
| Both sides made different changes to the same file | Stop before overwriting either side; resolve the conflict deliberately. |
| External facade interface changed | Perform an explicit project migration. |

Do not treat synchronization as a three-way graphical schematic merge. A conflicting file must be reviewed and resolved by the designer.
