"""Standalone KiCad schematic action: browse and place reusable blocks."""


def main():
    from kicad_blocks.plugin import run_library_browser

    run_library_browser()


if __name__ == "__main__":
    main()
