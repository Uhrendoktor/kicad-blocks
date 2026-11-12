# Visual documentation and generated previews

KiCad Blocks treats screenshots and animations as generated evidence. The preview images are produced by the real application UI and KiCad's own renderers, not hand-drawn illustrations.

## Current previews

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-workflow.gif" alt="The real Place Functional Block chooser as it filters a library and applies a configuration profile" width="900"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg" alt="KiCad-native rendering of the USB-C Power functional block facade" width="520"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg" alt="KiCad-native rendering of the functional block implementation schematic" width="900"></p>

The GIF contains three captures from the actual Tk placement chooser: the full library, a filtered search result, and the selected 5 V configuration profile. The vector previews are exported by KiCad CLI from a generated block fixture.

## Generation and validation

The visual acceptance fixture runs in CI under Xvfb. It creates a library with multiple blocks, implementation variants and a named profile; opens the production Place Functional Block dialog; captures the actual window; and verifies that the profile selects the matching implementation and typed parameter override. KiCad CLI independently exports the facade symbol and implementation schematic to SVG. Pillow builds the GIF from the captured application states.

The generated bundle contains a SHA-256 manifest tied to the checked-out source commit. CI uploads the bundle on pull requests so reviewers can inspect it. On successful pushes to main, the publisher validates the manifest and publishes the images to the separate docs/previews branch. The publisher verifies the remote branch head after pushing; provenance.json records the source commit, workflow run and asset hashes.

To reproduce the capture locally:

    python -m pip install cairosvg pillow
    xvfb-run -a -s "-screen 0 1280x800x24" \
      python tests/capture_visual_docs.py --output-dir artifacts/visual-docs

The environment must provide the KiCad CLI, Tk, Xvfb and xdotool. Generated files are disposable outputs; change the fixture or capture test rather than editing published screenshots by hand.
