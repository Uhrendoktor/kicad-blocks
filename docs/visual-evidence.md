# Visual documentation and generated previews

KiCad Blocks treats screenshots and animations as generated evidence. The preview images are produced by the real application UI and KiCad's own renderers, not hand-drawn illustrations.

## Current previews

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-workflow.gif" alt="The real Place Functional Block chooser as it filters a library and applies a configuration profile" width="900"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg" alt="KiCad-native rendering of the USB-C Power functional block facade" width="520"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg" alt="KiCad-native rendering of the STUSB4500 reference implementation schematic" width="1000"></p>

[Open the editable KiCad schematic](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.kicad_sch).

The implementation is no longer a synthetic USB-C fixture. It uses the real STUSB4500QTR symbol, a USB Type-C receptacle, the datasheet-listed protection devices, power-path MOSFETs, capacitors/resistors and the optional MCU interface. Its layout follows ST DS12499 Rev. 8 Figure 10; the chooser profiles reflect the 5 V / 2.4 A, 9 V / 1.33 A and 12 V / 1.0 A PDO examples in Table 19. The external block ports are native KiCad hierarchical labels with standard electrical shapes.

The GIF contains three captures from the actual Tk placement chooser: the full library, a filtered search result, and the selected 5 V / 2.4 A PDO1 profile. The vector preview and editable schematic are exported from the generated KiCad source by KiCad CLI.

## Generation and validation

The visual acceptance fixture runs in CI under Xvfb. It creates a library with multiple blocks, PDO implementation variants and named profiles; opens the production Place Functional Block dialog; captures the actual window; and verifies that the PDO1 profile selects the 5 V implementation and 2.4 A typed parameter. The STUSB4500 schematic generator loads real symbols from the installed KiCad libraries and emits native hierarchical labels for its external ports. KiCad CLI exports the facade symbol and implementation schematic to SVG. Pillow builds the GIF from the captured application states.

The generated bundle contains the editable .kicad_sch, SVG previews and a SHA-256 manifest tied to the checked-out source commit and ST datasheet reference. CI uploads the bundle on pull requests so reviewers can inspect both the source schematic and its rendered output. On successful pushes to main, the publisher validates the manifest and publishes the assets to the separate docs/previews branch. The publisher verifies the remote branch head after pushing; provenance.json records the source commit, workflow run and asset hashes.

To reproduce the capture locally:

    python -m pip install cairosvg pillow
    xvfb-run -a -s "-screen 0 1280x800x24" \
      python tests/capture_visual_docs.py --output-dir artifacts/visual-docs

The environment must provide the KiCad CLI, Tk, Xvfb and xdotool. Generated files are disposable outputs; change the fixture or capture test rather than editing published screenshots by hand.
