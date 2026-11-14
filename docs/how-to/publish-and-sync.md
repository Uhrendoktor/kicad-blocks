# Edit, publish, and synchronize implementations

Use a shared implementation when a change should be available to every project that uses the library. Use a project-local copy when one project needs an independent implementation.

## Edit the default implementation or a variant

1. Open **Browse and Place Functional Blocks** and select the block, or select a placed facade and choose **Open Block Implementation**.
2. In the browser, choose **Implementations**.
3. Select the **Default** row or a named variant.
4. Choose **Edit default** or **Edit variant**.
5. Edit the schematic in Eeschema and save it.

The **Default** row is an editable implementation. **Edit default** opens the default schematic; it is not a read-only label.

To add a named implementation, choose **Add variant** in the Implementations dialog. Give it a unique variant value and select an existing schematic, create a blank schematic, or copy another library implementation. Each accepted Add, Edit, Remove, or Set as default operation is saved to the block immediately. **Save & close** retries a failed write and then closes the manager.

## Edit the facade symbol

The facade is the symbol that projects place in their schematics. The current tested KiCad IPC build does not expose a supported cross-platform operation to open Symbol Editor and load the exact selected library symbol from Eeschema. The plugin validates the generated catalog entry and reports this capability gap; it does not call an unstable action, inject desktop input, or instruct the user to finish the core workflow manually.

This edit workflow remains unavailable until KiCad exposes the required API. The [compatibility guide](../explanation/compatibility.md#kicad-editor-action-api-boundary) records the API limitations.

## Publish a shared implementation

1. Edit and save the desired implementation in Eeschema.
2. Choose **KiCad Blocks Tasks → Edit & maintain → Publish implementation**.
3. Select the shared library block when prompted.
4. Review any interface or validation warnings before distributing the updated library.

Publishing updates the shared library revision and synchronizes the facade's external pins with root-sheet hierarchical labels. Existing pins whose names remain unchanged retain their number and graphics; new labels receive generated initial positions. Renaming or removing an external pin can require migration in projects that already use the block.

Publishing a shared library update does not silently overwrite a project's independent local implementation.

## Create a project-local copy

1. Select exactly one placed, library-linked facade.
2. Choose **KiCad Blocks Tasks → Edit & maintain → Make selected instance local**.
3. Open the implementation for that instance and edit the local schematic.

The local implementation is stored under the project's `.kicad_blocks/local/` directory. The facade, symbol KIID, reference, position, and wiring remain associated with the placed instance.

## Synchronize a local copy

1. Select the instance that is linked to a local copy.
2. Choose **KiCad Blocks Tasks → Edit & maintain → Synchronize local copy**.
3. Review the result message to see what was published, pulled, or found to have converged.

Synchronization compares tracked assets with the common baseline:

| Change state | Result |
| --- | --- |
| Only the local copy changed | Publish those local changes to the shared library. |
| Only the shared library changed | Pull the library changes into the local copy. |
| Both sides made the same change | Converge without a conflict. |
| Both sides made different changes to the same file | Stop before overwriting either side; resolve the conflict deliberately. |
| External facade interface changed | Perform an explicit project migration. |

Do not treat synchronization as a three-way graphical schematic merge. A conflicting file must be reviewed and resolved by the designer.
