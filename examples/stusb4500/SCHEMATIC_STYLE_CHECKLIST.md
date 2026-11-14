# STUSB4500 schematic style and electrical checklist

This checklist consolidates the visual conventions, electrical-routing lessons, review observations, and explicit requirements gathered while refining PR #20. It applies to the STUSB4500 reference implementation and to similar functional-block schematics.

## Page composition and functional blocks

- [ ] Use an A3 landscape sheet; keep the formal KiCad title block in the lower-right corner.
- [ ] Put a restrained title and source subtitle in the reserved top margin. Do not place a large decorative title over the circuit field.
- [ ] Group functions in thin, unfilled blue rectangular frames with a small attached tab/header on the top edge.
- [ ] Use small, purposeful functional blocks: USB-C connector/CC protection, VBUS bulk protection, VBUS monitor/filter, controller/decoupling, address straps, PMOS switch, and protected output.
- [ ] Keep tabs attached to frames, titles inside the tab, and notes inside the relevant block. Avoid floating captions.
- [ ] Keep whitespace between unrelated blocks. Frames should not touch, overlap, or cut through components, labels, wires, or note boxes.
- [ ] Align component rows and shared rails to the KiCad grid. Keep related passives on common axes.
- [ ] Use sparse notes that explain a specific design choice or unresolved part selection; size note boxes from their actual text.

## Symbols, references, values, and labels

- [ ] Keep reference and value fields close to their symbols. Keep resistor values centered in the resistor body.
- [ ] Rotate reference/value fields on vertical resistors by 90 degrees so they follow the symbol orientation; counter-rotate fields on symbol-rotated horizontal resistor banks so the text stays readable.
- [ ] Place net labels close to the component/pin they name, but offset them perpendicular to pin names and wire corridors when necessary.
- [ ] Reuse local labels at least twice. A one-off local label is usually a hidden wire; replace it with a short wire or give the net a second intentional local-label use.
- [ ] Hierarchical labels are the exception: they are real block interfaces and may be used once at the child boundary.
- [ ] Do not use global labels in this block. Use local labels for internal nets and hierarchical labels only for true parent interfaces.
- [ ] Do not solve visual clutter by deleting all labels. Balance short physical wires and reused labels to minimize both wire length and label count.

## Electrical topology and routing

- [ ] Keep the USB-C receptacle and its CC ESD array together; CC1 and CC2 must route independently to the correct receptacle and controller pins.
- [ ] Place the VBUS bulk capacitor and TVS physically beside the USB-C receptacle and connect them to its VBUS/GND entry.
- [ ] Orient D4 so incoming VBUS enters from the connector side and the filtered output faces the downstream filter/sense circuit.
- [ ] Physically connect D4, C4, R1, and R4 as one compact VBUS filter/sense circuit. Avoid traces through diode bodies or across the opposite diode terminal.
- [ ] Connect C1, C2, and C3 directly to the U1 regulator pins they decouple. Use short orthogonal routes and keep the cap-to-pin association visually obvious.
- [ ] Keep U1 address straps close to the controller and close the pull-up / address / pull-down chain gaps. Keep the two address nets separate.
- [ ] Connect R55.1 directly to U1 RESET with a short wire; retain RESET as the hierarchical parent interface at U1.
- [ ] Keep the two PMOS devices facing one another. Their common-source node must be one physical wire with a branch to the bias resistor; route the second gate to the bias bus.
- [ ] Keep MCU connectors, MCU I2C pull-ups, and other parent-level application circuitry outside this controller block.
- [ ] Keep U1.22 on its own VSYS hierarchical input. Do not tie VSYS to GND.
- [ ] Use hierarchical GND ports for all ground endpoints in the child implementation.
- [ ] Preserve the datasheet-derived signal assignment for CC1/CC2, VBUS, VBUS_VS_DISCH, DISCH, FET_MID, VSNK_GATE, VSNK, VREG_1V2, VREG_2V7, ADDR0, ADDR1, and RESET.

## Routing and geometry safeguards

- [ ] Prefer short, visible, orthogonal wires for local relationships. Use matching local labels for long or remote internal nets instead of long loops across blocks.
- [ ] Keep wires outside symbol bodies except where they terminate on the intended pin. Never route a net across the opposite terminal of a polarized/diode symbol.
- [ ] Derive pin and rail coordinates from the loaded library symbol. Do not guess a rail height: a prior VBUS placement overlapped the embedded PMOS FET_MID pin.
- [ ] Keep CC lanes, VBUS rails, PMOS common-source wiring, and block borders on separate corridors.
- [ ] Check every label stub as well as every main wire: labels must not cover pin names, values, component references, or block tabs.
- [ ] Check for overlap between text, symbols, wires, junctions, notes, tabs, and frame lines in the rendered KiCad SVG. A passing parser is not a visual pass.
- [ ] Keep address and similar repeated networks compact; reduce spacing when the circuit remains readable and electrical nodes stay distinct.

## Validation gate

- [ ] Run the Python/unit tests and Ruff.
- [ ] Export the schematic netlist with KiCad CLI and verify all critical pin-to-net assignments against the datasheet-derived map.
- [ ] Run KiCad ERC with the standalone child-block validation profile. Suppress only expected root-sheet hierarchical ports, externally driven input/power pins, isolated hierarchical port labels, and absent project library tables; all internal pin conflicts, dangling wire/label checks, and all other ERC rules remain enabled. Require zero unignored violations and inspect the report.
- [ ] Export a fresh SVG from the exact tested commit and visually inspect the whole page and dense regions at readable zoom.
- [ ] Open/save the exact-head schematic in Eeschema when the GUI runner is available.
- [ ] Verify the rendered frame geometry and all wiring after any coordinate or orientation change.
