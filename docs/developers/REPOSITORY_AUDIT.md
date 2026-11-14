# Repository quality and redundancy audit

**Reference standard:** Uhrendoktor/freecad-cloth PR 2751, specifically its staged Python quality contract. This audit does not apply FreeCAD-specific GUI, geometry, or solver checks to KiCad Blocks.

## Scope reviewed

The audit covered the complete tracked repository tree at the starting revision, including the plugin package, action entry points, release/install scripts, examples, documentation, both CI jobs, and every file under tests/. Test functions were enumerated individually for the generated test inventory. Searches also covered explicit Any imports, generic object annotations, and the production type boundaries.

The audit was subsequently rebased onto current main commit `17f2fd4422c38d6bcdd2ccdac3ec81508bbde42c`, retaining the newer Create Block from Sheet and automatic child-dependency work. The refreshed inventory covers **224 pytest cases across 23 modules**, seven standalone KiCad acceptance runners, and three additional CI validation runners. The new Create Block IPC integration runner is catalogued separately because it is not named `test_*.py`.

## Confirmed findings and changes

1. **Test collection hid acceptance runners.** Seven files named test_*.py are executable main()-based acceptance programs and intentionally contain no pytest test functions. CI invokes them explicitly for live Eeschema, schematic IPC, PCB IPC/layout, the plugin-managed environment, or Tk/X11 behavior. They are now explicitly registered in scripts/test_inventory.py, listed separately in TEST_INVENTORY.md, and exempted by the pytest collection guard only when they retain their main() entry point. All other test_*.py modules must collect at least one test.
2. **No test deletion was justified.** The apparent overlaps are different layers or environments: test_pcb_layout_unit.py checks deterministic transforms and net mapping while test_pcb_layout.py runs a KiCad-backed acceptance path; test_editor_workflow.py checks action and transaction contracts while test_instance_configuration_gui.py drives the real Tk dialog; test_library_browser.py checks UI logic while test_library_picker_gui.py exercises real X11 selection and native KiCad rendering; test_publish_ipc_wheel.py, test_publish_pinned_ipc_wheel.py, and test_publish_release.py cover distinct API and immutable-asset contracts. The automated inventory keeps these differences visible.
3. **Intentional cross-environment repetition remains.** The managed-environment plugin registration runner invokes the picker and instance-configuration smoke programs after installation, while visual capture drives equivalent workflows to produce screenshot evidence. Those runs validate different execution environments and evidence outputs; removing either would reduce coverage.
4. **Weak annotation escape hatches existed in production code.** Any/dict[str, Any]/list[Any] annotations were present in composition, operations, PCB placement/layout, and the PCB action entry point. Generic object annotations appeared in parameter handling, PCB placement results, and the library browser's record indexes. They were replaced with recursive JSON value types, a recursive S-expression type, typed block records, and a narrow board/KIID Protocol.
5. **Type and quality policy was not repository-wide.** CI installed Ruff but only linted plugin/ and tests/. A reviewed Pyright production inventory, explicit Ruff rule selection, Ruff format check, alias-aware AST policy, public API documentation contract, changed-file annotation ratchet, and high-confidence dead-code scan are being added as independent gates.

## Redundancy/dead-code decisions

No test module was removed. No helper or source file was deleted solely because its name or direct import count suggested it was unused: KiCad's plugin manifest, entry-point discovery, command registration, Tk callbacks, IPC objects, and CLI runners contain dynamic references that make naive reference counts unsafe.

The high-confidence dead-code check is intentionally restricted to production/plugin and script surfaces and runs at Vulture's 100% confidence threshold. Any lower-confidence candidate should be reviewed against the action manifest, dynamic imports, subprocess entry points, and the integration runners before removal.

## Ongoing rules

- Every pytest test function is listed in the generated inventory with a purpose derived from its descriptive test name.
- Every test_*.py module must either collect pytest tests or be registered as a main()-based acceptance runner.
- New Python changes may not increase missing parameter/return annotations per rule and file relative to the merge base. New Python files therefore start with fully annotated functions.
- Explicit typing.Any and generic object annotations are prohibited in tracked Python sources, including quoted forward references and type comments.
- The pure model and PCB placement boundary are part of the reviewed static-type inventory; their collaborators use structured types and narrow Protocols rather than broad escape hatches.
- Repository changes preserve test assertions, KiCad validation, production runtime behavior, and release/install contracts. The audit does not authorize reducing thresholds or replacing native acceptance checks with mocks.
