"""KiCad IPC entrypoint for creating a block from the selected sheet."""

from _bootstrap import run_action


if __name__ == "__main__":
    run_action("create-block")
