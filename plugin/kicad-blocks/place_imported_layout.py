"""PCB Editor action for moving a previously imported block layout at the cursor."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kicad_blocks.pcb_placement import (
    Board,
    move_placement_at_cursor,
    read_pcb_placement_manifest,
)
from kicad_blocks.pcb_types import PCBPlacementEntry, PCBPlacementManifest


class _Project(Protocol):
    """KiCad project metadata needed to resolve a relative board path."""

    path: str


class _ProjectDocument(Protocol):
    """Document metadata exposed by the active KiCad PCB board."""

    board_filename: str
    project: _Project | None


class _ActiveBoard(Board, Protocol):
    """Active PCB board with the document metadata exposed by KiCad IPC."""

    document: _ProjectDocument
    name: str


def _active_board_path(board: _ActiveBoard) -> Path:
    document = board.document
    filename = str(document.board_filename or board.name).strip()
    if not filename:
        raise RuntimeError("KiCad did not report a file name for the active PCB board.")
    candidate = Path(filename).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    project = document.project
    project_path = str(project.path if project is not None else "").strip()
    if not project_path:
        raise RuntimeError(
            "KiCad did not report the project directory for the active PCB board. "
            "Save the board in a KiCad project before using interactive block placement."
        )
    return (Path(project_path).expanduser() / candidate).resolve()


def _choose_placement(placements: list[PCBPlacementEntry]) -> PCBPlacementEntry | None:
    if len(placements) == 1:
        return placements[0]

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Place Imported Block Layout")
    root.resizable(False, False)
    root.geometry("520x150")
    selected: dict[str, PCBPlacementEntry | None] = {"placement": None}
    labels = [
        f"{item['instance_reference']} — {item.get('block', 'Block')} / {item.get('layout_name', 'Layout')} "
        f"({len(item['item_uuids'])} board objects)"
        for item in placements
    ]

    ttk.Label(
        root,
        text="Select the imported block group to move with KiCad's cursor tool:",
        wraplength=480,
    ).pack(padx=16, pady=(16, 8), anchor="w")
    choice = tk.StringVar(value=labels[0])
    combo = ttk.Combobox(root, textvariable=choice, values=labels, state="readonly", width=68)
    combo.pack(padx=16, fill="x")
    combo.current(0)

    buttons = ttk.Frame(root)
    buttons.pack(padx=16, pady=16, anchor="e")

    def accept() -> None:
        try:
            selected["placement"] = placements[labels.index(choice.get())]
        except ValueError:
            selected["placement"] = None
        root.destroy()

    def cancel() -> None:
        root.destroy()

    ttk.Button(buttons, text="Move at cursor", command=accept).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", cancel)
    combo.bind("<Return>", lambda _event: accept())
    root.bind("<Escape>", lambda _event: cancel())
    root.mainloop()
    return selected["placement"]


def _show_error(error: Exception) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("KiCad Blocks — PCB placement", str(error), parent=root)
        root.destroy()
    except (ImportError, RuntimeError):
        print(f"KiCad Blocks PCB placement failed: {error}")


def main() -> None:
    try:
        from kipy import KiCad

        kicad = KiCad()
        board = kicad.get_board()
        if board is None:
            raise RuntimeError("No PCB board is open in PCB Editor.")

        board_path = _active_board_path(board)
        manifest: PCBPlacementManifest = read_pcb_placement_manifest(board_path)
        placement = _choose_placement(manifest["placements"])
        if placement is None:
            return

        print(
            "KiCad Blocks: moving imported block "
            f"{placement['instance_reference']} using native PCB cursor placement."
        )
        # This is the final IPC request: KiCad keeps its server busy until the
        # user commits or cancels the interactive move with its normal UI.
        move_placement_at_cursor(board, placement)
    except Exception as exc:  # noqa: BLE001 - present IPC and data errors to the KiCad user
        _show_error(exc)


if __name__ == "__main__":
    main()
