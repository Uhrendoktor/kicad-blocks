# STUSB4500 schematic style and electrical checklist

This is the review and acceptance checklist for the generated STUSB4500 child-block schematic. The machine-checkable rules are enforced by scripts/validate_schematic_style.py and invoked from tests/validate_kicad_cli.py. KiCad CLI netlist/ERC checks remain authoritative for electrical connectivity; SVG/Eeschema review remains authoritative for visual legibility.

## Page composition and logical flow

- [ ] Use an A3 landscape sheet; keep the formal KiCad title block in the lower-right corner.
- [ ] Put the sheet title and source subtitle in the reserved top margin. Keep both inside native KiCad text boxes.
- [ ] Group related components in thin, unfilled functional frames with an attached title tab.
- [ ] Keep tabs attached to their frames and put their titles inside the tab, not as floating text.
- [ ] Make each functional frame just large enough for its member components, their references/values, required labels, and intentional local notes, with a consistent minimum clearance.
- [ ] Do not let a wire, label, text box, symbol field, or unrelated symbol penetrate a functional-frame border. A border crossing requires a documented exception and a specific test.
- [ ] Functional frames must not overlap one another.
- [ ] Arrange the primary electrical stages left-to-right and keep their horizontal frame-to-frame gap uniform. The reference implementation uses a 2.54 mm gap.
- [ ] Keep secondary frames (such as address straps) close to the component group they describe without colliding with adjacent frames.
- [ ] Align components, rows, and shared rails to the KiCad grid. Keep related passives on common axes and minimize unnecessary whitespace inside each frame.

## Symbols, references, values, and component population

- [ ] Keep reference and value fields close to their symbols and outside symbol bodies unless a passive value is intentionally centered inside its body.
- [ ] Keep resistor values centered in the resistor body. Rotate reference/value fields on vertical resistors to follow the symbol; counter-rotate fields on symbol-rotated horizontal resistor banks so the text remains readable.
- [ ] Place each symbol's reference/value pair on the nearest clear side of that symbol. Do not place fields under a neighboring component, on a wire corridor, or inside another component's frame tab.
- [ ] Do not write NC, DNP, or NOT FITTED into a component's Value field when it is a real optional component. Give it the value it would have when populated and mark the instance with KiCad's DNP option.
- [ ] For this address strap, R51 and R52 are optional 100 kΩ pull-ups: retain Value=100k and set DNP=yes. R53/R54 remain the populated 100 kΩ pull-downs.
- [ ] Check that every functional group contains the intended components and that the frame's clearance is consistent; do not use one oversized enclosing frame to mask bad spacing.

## Labels and electrical topology

- [ ] Keep local labels near the pin or component they identify. Move label text away from pin-name text, symbol references/values, and wire corridors; use a short perpendicular label twig when required.
- [ ] Each local label name must occur at least twice, on two or more distinct physical wire networks. If a net is completely wired as one visible network, do not add duplicate labels merely to satisfy a count.
- [ ] A physical wire network may carry at most one instance of a given local label name. Never place IC — LABEL — LABEL — IC on one connected network.
- [ ] Use labels to connect separate wire islands, not to decorate every pin of a continuous routed network.
- [ ] Hierarchical labels are the single-use exception for real child-sheet interfaces. Put each one at the end of a short wire twig; do not put it inline on a wire between two components.
- [ ] A hierarchical port must not split a component-to-component wire. For a branching node, create a visible branch twig and put the hierarchical label at the twig's free end.
- [ ] Do not use global labels in this child block. Use local labels for internal disconnected networks and hierarchical labels only for parent interfaces.
- [ ] Prefer short, visible, orthogonal wires for local relationships. Use matching local labels for long or remote internal nets rather than long loops across the sheet.
- [ ] Do not route wires diagonally. Do not allow wires to cross one another. Intentional T-branches must terminate at an existing wire endpoint or be marked with an explicit junction; a four-way crossing is not an acceptable substitute for a branch.
- [ ] Keep wires outside symbol bodies except where they terminate on the intended pin. Never route a net across the opposite terminal of a polarized/diode symbol.
- [ ] Derive pin and rail coordinates from the loaded library symbol. Do not guess rail heights or pin offsets.

## Annotation boxes and UI/layout overlap

- [ ] Every explanatory/annotating text item must be a native KiCad text_box, not a free-standing text item.
- [ ] Use the shared _graphic_text / _note_box helper. It measures the text, sets the box dimensions, and applies equal physical padding on all four sides.
- [ ] Keep a note box visually adjacent to its subject circuit and inside the subject's functional frame when practical. A reader should identify the described part without tracing a distant caption.
- [ ] Use borderless native text boxes for titles/captions only when the attached functional-frame tab or another nearby frame supplies the visual grouping.
- [ ] No text box may overlap another text box, a visible symbol reference/value, a local or hierarchical label, a symbol/pin, a wire, a junction, or a frame border.
- [ ] No wire may pass through a text box. No symbol field, net label, title, or note may sit on top of another UI element.
- [ ] Check all label stubs as well as main wires. Label glyphs must not cover pin names, values, component references, nearby notes, or block tabs.
- [ ] Recheck the rendered KiCad SVG at readable zoom after every position, orientation, font-size, or frame change. Passing the S-expression parser is not a visual pass.

## Clippy rules and declared group model

The reference generator declares each circuit group in `SCHEMATIC_GROUPS`. A group owns a stable ID, title, description, member references, lane/order or alignment constraint, and optional evidence metadata. The shared source-evidence record points to ST DS12499 Rev. 8, page 26, Figure 10; each group can override that reference and may supply a reviewed screenshot asset path.

The layout pass derives each primary group's pin extents, frame, title-tab width and description area from the loaded KiCad library symbols. It then packs the primary groups from left to right with the same 2.54 mm frame gap. The address-strap group is aligned to the controller group in a secondary lane. R1/R4 and C2/C3 adjacency, and R55's placement relative to U1.6, are explicit component constraints.

Component positions are now derived from each group's declared anchor and relative constraints (offsets, a midpoint relation for the FET network, and a pin-relative relation for the RESET pull-down). The symbol specification no longer contains sheet coordinates. The layout model still expresses design intent through explicit relationships; it does not yet infer a complete layout from the electrical net graph. Each group retains one vertical anchor baseline, while all other component origins are derived. A topology-aware placer can replace or augment the relationship list behind the same group metadata and linter interface.

The machine-enforced rules now include:

- [ ] `SCH003`: a label's visible glyph rectangle must not be penetrated by a wire. The label's own connection at its anchor is allowed.
- [ ] `SCH009`: every member pin, visible Reference/Value field, generated label glyph, wire segment and text box must remain inside its owning frame. A wire crossing a frame boundary is an error; each label must belong to exactly one frame and remain inside that frame.
- [ ] `SCH005`: declared ground pins face down where symbol orientation permits it. D1.3 is currently an explicit down-facing ground-pin rule.
- [ ] `SCH004`: U1.21-C1.1, U1.23-C2.1 and U1.23-C3.1 must belong to physical wire networks. A label cannot satisfy this rule; those supply pins must remain label-free.
- [ ] `SCH006`: C2/C3 decouplers are placed as a contiguous pair. Their common pin network is a physical branch, not a pair of pin labels.
- [ ] `SCH006`: R1/R4 are aligned with no more than one resistor-body width of clear gap.
- [ ] `SCH004`: T4.3, T1.2 and R10.1 form one continuous physical common-source network. The FET_MID label is forbidden.
- [ ] `SCH006`: resistor references are within 3.0 mm of their symbol origin; capacitor references are within 4.0 mm and capacitor values within 5.5 mm.
- [ ] `SCH010`: visible Reference/Value fields do not straddle symbol bodies, overlap pins/wires/labels/other fields, or sit more than 8.0 mm from their symbol body. Values may be centered wholly inside passive bodies.
- [ ] `SCH011`: block titles contain no line breaks; every block description/comment panel has its own visible outline.
- [ ] `SCH012`: labels and descriptive comment panels keep at least 2.54 mm clear of functional-frame borders.
- [ ] `SCH013`: a wire may end at its intended pin but may not cross a symbol body or an intermediate pin.
- [ ] `SCH014`: configured paired pins share the declared logical net; C2.2 and C3.2 are both GND.
- [ ] `SCH015`: U1.3, the unused NC pin immediately below RESET, always has an explicit no-connect marker.
- [ ] `SCH016`: wire compactness is scored per group as total internal wire length divided by its configured budget. The FET switch budget is 105 mm (score <= 1.0).
- [ ] `SCH017`: non-trivial groups identify their member circuits and provide datasheet/page/figure or a reviewed screenshot asset.
- [ ] `SCH018`: visible label glyphs do not overlap other label glyphs, symbol fields or annotation boxes.
- [ ] `SCH007` / `SCH009`: every declared group emits both a title and a description/source panel. Frames are derived from pin extents and have a fixed 2.54 mm horizontal gap along the primary flow.

## Automated and visual validation gate

- [ ] Run unit tests, Ruff, and compileall, including the schematic style checker.
- [ ] Run `scripts/validate_schematic_style.py --list-rules` to inspect rule IDs/severities and scope, then run `scripts/validate_schematic_style.py PATH_TO_SCHEMATIC`. The stable catalogue contains SCH001–SCH018. Run `--generic-only PATH_TO_SCHEMATIC` to use the reusable geometry/presentation subset without the STUSB4500 profile contracts.
- [ ] Export the schematic netlist with KiCad CLI and verify all critical pin-to-net assignments against the datasheet-derived map.
- [ ] Run KiCad ERC with the standalone child-block validation profile. Suppress only expected root-sheet hierarchical ports, externally driven input/power pins, isolated hierarchical port labels, and absent project library tables. Internal shorts, pin conflicts, dangling wires/local labels, and all other ERC rules remain enabled; require zero unignored violations.
- [ ] Export a fresh SVG from the exact tested commit and inspect the full page and dense regions. Confirm the boxes fit the text and remain close to the circuits they describe.
- [ ] Open/save the exact-head schematic in Eeschema when the GUI runner is available.
- [ ] Re-export the netlist and rerun all geometry checks after any wire, label, frame, component, or field movement.
