# KiCad upstream proposal: interactive schematic move over IPC

## Problem

The PCB IPC API exposes `Board.interactive_move(items)`, but the schematic IPC API has no equivalent. Eeschema already implements the native move tool (`SCH_ACTIONS::move` / `SCH_MOVE_TOOL`), while clients can only create schematic symbols at explicit coordinates and select them. This prevents an IPC client from placing a library symbol and immediately handing it to the normal cursor-driven placement workflow.

## Proposed API

Add `Schematic.interactive_move(items)`, mirroring the PCB API and its asynchronous editor semantics.

A schematic IPC request should:

1. Validate the active schematic document and resolve the supplied KIIDs.
2. Reject missing items, items outside the active sheet, or unsupported item types.
3. Build an `SCH_SELECTION`, set its reference point to the first item's position, and make it the active selection.
4. Start `SCH_ACTIONS::move` through `PostAPIAction`, transferring the active API commit to the move tool.
5. Return immediately while Eeschema owns the interactive operation. Confirm should push the commit as one undo step; cancel should roll it back.

## Likely implementation touchpoints

- `api/proto/schematic/schematic_commands.proto`: add an `InteractiveMoveItems` request containing repeated item KIIDs.
- `eeschema/api/api_handler_sch.h/.cpp`: declare, register, and implement the request handler, following `API_HANDLER_BOARD::handleInteractiveMoveItems`.
- `eeschema/tools/sch_actions.h/.cpp` and `sch_move_tool.h/.cpp`: reuse the existing move action/tool; avoid creating a second placement implementation.
- Generated protobuf bindings and `kicad-python`: expose `Schematic.interactive_move(items)`.
- IPC integration tests: cover valid selection, invalid/stale IDs, active-sheet validation, cursor handoff, confirm, cancel, and single-step undo.

## Compatibility and semantics

Keep the API schematic-specific and use the existing Eeschema move action. Do not route through the unstable generic `RunAction` request or require desktop-specific keyboard/mouse injection. Match PCB's immediate-return/busy behavior so the IPC client does not block waiting for the user's cursor interaction.

## Client example

```python
commit = schematic.begin_commit()
symbol = schematic.place_symbol_from_library("KBlocks:MyBlock", temporary_position)
schematic.interactive_move(symbol)
# Eeschema owns the operation; confirm commits, Escape cancels.
```

The method above is proposed API, not currently available in KiCad. Until it exists, clients can only place at a fixed coordinate and select the new item.
