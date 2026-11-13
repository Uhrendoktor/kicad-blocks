"""KiCad IPC entrypoint for the functional block library browser."""

from __future__ import annotations

from _bootstrap import run_action


if __name__ == "__main__":
    run_action("manage-library")
