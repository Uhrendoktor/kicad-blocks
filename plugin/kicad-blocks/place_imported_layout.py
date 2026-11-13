"""PCB Editor action for moving a previously imported block layout at the cursor."""

from __future__ import annotations

from pathlib import Path

from kicad_blocks.json_types import JsonObject

from kicad_blocks.pcb_placement import (
    move_placement_at_cursor,
    read_pcb_placement_manifest,
)


def _active_board_path(board) -> Path:
    document = board.document
    filename = str(getattr(document, "board_filename", "") or getattr(board, "name", "")).strip()
    if not filename:
        raise RuntimeError("KiCad did not report a file name for the active PCB board.")
    candidate = Path(filename).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    project = getattr(document, "project", None)
    project_path = str(getattr(project, "path", "") or "").strip()
    if not project_path:
        raise RuntimeError(
            "KiCad did not report the project directory for the active PCB board. "
            "Save the board in a KiCad project before using interactive block placement."
        )
    return (Path(project_path).expanduser() / candidate).resolve()


def _choose_placement(placements: list[JsonObject]) -> JsonObject | None:
    if len(placements) == 1:
        return placements[0]

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Place Imported Block Layout")
    root.resizable(False, False)
    root.geometry("520x150")
    selected_placement: JsonObject | None = None
    labels: list[str] = []
    for item in placements:
        item_uuids = item.get("item_uuids")
        object_count = len(item_uuids) if isinstance(item_uuids, list) else 0
        labels.append(
            f"{item.get('instance_reference', '')} — {item.get('block', 'Block')} / "
            f"{item.get('layout_name', 'Layout')} ({object_count} board objects)"
        )

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
        nonlocal selected_placement
        try:
            selected_placement = placements[labels.index(choice.get())]
        except ValueError:
            selected_placement = None
        root.destroy()

    def cancel() -> None:
        root.destroy()

    ttk.Button(buttons, text="Move at cursor", command=accept).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", cancel)
    combo.bind("<Return>", lambda _event: accept())
    root.bind("<Escape>", lambda _event: cancel())
    root.mainloop()
    return selected_placement


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
        manifest = read_pcb_placement_manifest(board_path)
        raw_placements = manifest.get("placements")
        if not isinstance(raw_placements, list):
            raise TypeError("PCB placement manifest contains no placement list.")
        placements: list[JsonObject] = []
        for raw_placement in raw_placements:
            if not isinstance(raw_placement, dict):
                raise TypeError("PCB placement manifest contains a malformed placement.")
            placements.append(raw_placement)
        placement = _choose_placement(placements)
        if placement is None:
            return

        print(
            "KiCad Blocks: moving imported block "
            f"{placement['instance_reference']} using native PCB cursor placement."
        )
        # This is the final IPC request: KiCad keeps its server busy until the
        # user commits or cancels the interactive move with its normal UI.
        move_placement_at_cursor(board, placement)
    except Exception as exc:
        _show_error(exc)


if __name__ == "__main__":
    main()
