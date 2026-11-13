"""KiCad IPC entrypoint for opening the selected block implementation."""

from __future__ import annotations

from _bootstrap import run_action


if __name__ == "__main__":
    run_action("open-implementation")
