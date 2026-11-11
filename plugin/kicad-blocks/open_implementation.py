"""Standalone KiCad schematic action: open the selected block implementation."""


def main():
    from kicad_blocks.plugin import open_selected_implementation_action

    open_selected_implementation_action()


if __name__ == "__main__":
    main()
