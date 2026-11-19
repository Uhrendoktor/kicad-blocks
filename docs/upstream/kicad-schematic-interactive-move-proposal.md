# KiCad upstream proposal: interactive schematic move over IPC

## Problem

The PCB IPC API exposes `Board.interactive_move(items)`, but the current schematic IPC API has no equivalent. Eeschema already implements the native move tool (`SCH_ACTIONS::move` / `SCH_MOVE_TOOL`), while clients can only create schematic symbols at explicit coordinates and select them. This prevents an IPC client from placing a library symbol and immediately handing it to the normal cursor-driven placement workflow.

The absence is confirmed in current source, not inferred from Python docs alone: `schematic_commands.proto` contains `PlaceSymbolFromLibrary` but no `InteractiveMoveItems`; `API_HANDLER_SCH` registers `PlaceSymbolFromLibrary`, `AddToSelection`, and `FocusOnItems`, but no move handler. The PCB command and handler provide a direct design precedent.

## Proposed API

Add `Schematic.interactive_move(items)`, mirroring the PCB API and its asynchronous editor semantics.

A schematic IPC request should be defined in `api/proto/schematic/schematic_commands.proto`, conceptually:

```proto
message InteractiveMoveItems
{
  kiapi.common.types.DocumentSpecifier schematic = 1;
  repeated kiapi.common.types.KIID items = 2;
}
```

The Eeschema handler should:

1. Reject headless calls and requests while Eeschema is busy.
2. Validate the schematic document and resolve each KIID through the active sheet path. Reject missing IDs and items outside that path; do not silently move a partial selection.
3. Build the move selection from the resolved schematic items, set its reference point to the first item's position, and make it the active editor selection.
4. Start `SCH_ACTIONS::move` through `TOOL_MANAGER::PostAPIAction`, transferring the active `SCH_COMMIT` returned by `getCurrentCommit(clientName)` to the move tool.
5. Return immediately while Eeschema owns the interactive operation. Confirm should push the transaction as one undo step; Escape/cancel should roll it back.

This mirrors the PCB handler's sequence: validate document, check busy state, resolve all item IDs, establish a selection reference point, clear/set selection, retrieve the active API commit, and call `PostAPIAction(PCB_ACTIONS::move, commit)`. The schematic version uses `SCH_SELECTION_TOOL` and `SCH_ACTIONS::move` instead.
## Likely implementation touchpoints

- `api/proto/schematic/schematic_commands.proto`: add the request shown above.
- `eeschema/api/api_handler_sch.h/.cpp`: include the generated request, declare/register `handleInteractiveMoveItems`, resolve path-aware `SCH_ITEM` IDs, validate the active sheet, update `SCH_SELECTION_TOOL`, and post the move with the active `SCH_COMMIT`.
- `eeschema/tools/sch_actions.h/.cpp` and `eeschema/tools/sch_move_tool.cpp`: reuse the existing `SCH_ACTIONS::move` and `SCH_MOVE_TOOL::Main`; avoid adding a second move implementation.
- `kicad-python`: generate the new protobuf type and mirror `Board.interactive_move()` in `Schematic`, serializing the schematic `DocumentSpecifier` and item KIIDs.
- IPC integration tests: cover valid single/multi-item selection, invalid/stale IDs, wrong-sheet IDs, busy/headless responses, cursor handoff, confirm, cancel, and single-step undo.
## Compatibility and semantics

Keep the API schematic-specific and use the existing Eeschema move action. Do not route through the unstable generic `RunAction` request or require desktop-specific keyboard/mouse injection. Match PCB's immediate-return/busy behavior so the IPC client does not block waiting for the user's cursor interaction.

## Client example

```python
commit = schematic.begin_commit()
symbol = schematic.place_symbol_from_library("KBlocks:MyBlock", temporary_position)
schematic.interactive_move(symbol.id)
# Eeschema owns the active commit; confirm commits, Escape cancels.
```

The method above is proposed API, not currently available in KiCad. Until it exists, clients can only place at a fixed coordinate and select the new item.

## Current plugin integration

The plugin now capability-detects `Schematic.interactive_move`. If the method is present and the connected Eeschema server accepts the request, the plugin creates the symbol inside an API commit and returns control to the native move tool. If the binding or server does not yet expose the method, the same commit is pushed and the existing page-center/focus/select path completes normally. This makes the plugin ready for the upstream method without making current installations lose a working Place action.

The current official schematic API reference lists `place_symbol_from_library` but not `interactive_move`; the Board reference documents the latter for PCB only. The required server-side request is therefore still an upstream KiCad change, not something a client can emulate safely over the current IPC surface.


## Related upstream work checked

KiCad merge request [!2332](https://gitlab.com/kicad/code/kicad/-/merge_requests/2332) is closed, but it is not an implementation of this proposal. Its `InteractivePlaceSymbol` request loads a symbol resource and starts the symbol-placement pipeline. This proposal instead operates on existing schematic item KIIDs and hands an already-created symbol to the native move tool. The two operations have different payloads, lifecycle semantics, and commit ownership; !2332 should not be treated as satisfying `Schematic.interactive_move(items)`.

The PCB reference implementation is in `API_HANDLER_BOARD::handleInteractiveMoveItems` (current Doxygen source, lines 920–962). It rejects headless/busy contexts, validates the document, resolves item IDs, sets the selection reference point, clears and sets selection, then transfers the API commit to `PCB_ACTIONS::move` using `PostAPIAction`. The schematic port must additionally account for hierarchical sheet paths: resolve each KIID to an item and its `SCH_SHEET_PATH`, and reject a mixed-sheet or stale selection rather than silently moving only the subset that resolved. The active-sheet rule should be explicit in the request contract and tests.

A minimal acceptance test should create a symbol in an explicit API commit, invoke the interactive move, verify Eeschema enters its native move state, then test both confirm (one undoable commit) and Escape (rollback of the created symbol). Also cover empty IDs, duplicate IDs, stale IDs, items from another sheet, headless mode, and a second request while the editor is busy.
