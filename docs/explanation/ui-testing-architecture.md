# Automated UI interaction testing architecture

## Goals

The UI must be tested as an application, not just as a collection of functions. Every user-visible action should have a traceable route from a real control to an observable result. The test catalog should also become the source for the human-readable list of windows, workflows, and screenshots.

The architecture has five layers:

1. **Production Tk widgets.** Keep the existing UI implementation. Attach stable semantic IDs to controls as they are constructed. IDs are not translated button labels and therefore survive text changes.
2. **Reusable component contracts.** A contract performs an interaction, returns an observation, and asserts the expected result.
3. **Screen catalog.** A declarative registry describes windows, their major views, controls, action IDs, expected evidence, and current coverage status.
4. **Generated documentation.** Render the catalog into a nested window/functionality inventory and attach real light/dark screenshots from the existing capture pipeline.
5. **CI execution.** Run headless/unit checks on every build; run real Tk interactions under Xvfb; retain xdotool and real KiCad GUI/IPC integration for behaviors that require desktop focus or editor-side effects.

## Contract lifecycle

Every reusable component implements the same interface:

~~~python
observation = contract.execute(context)
contract.assert_result(context, observation)
~~~

The convenience method `contract.run(context)` performs both stages. The separation matters: execute is responsible for doing the thing a user does; assert_result is responsible for proving the thing that should happen.

The initial component types are:

| Contract | Real UI object | Required result assertions |
|---|---|---|
| `WindowContract` | `Tk` / `Toplevel` | Window identity, title, existence, visibility, usable dimensions |
| `ButtonContract` | `ttk.Button` / `Button` | Invoked command, exact action route, expected window/dialog lifecycle |
| `MenuItemContract` | `Menu` entry | Invoked menu command and resulting state |
| `ChoiceViewContract` | `ttk.Combobox` or equivalent choice | Selected value and dependent callback/state |
| `NotebookViewContract` | `ttk.Notebook` | Every declared view can be selected and its required controls become visible |
| `PaneGroupContract` | `ttk.Panedwindow` | Expected child panes are present, visible, correctly oriented, and have non-zero geometry |

A contract should assert outcomes rather than implementation details. For example, clicking a "Set Default" button should verify that the selected implementation becomes Default and that dependent metadata is preserved. It should not merely assert that a method named `make_default` was called.

## Stable control IDs and coverage discovery

A production control that participates in a contract receives a stable attribute such as:

~~~python
button._kicad_ui_contract_id = "task-hub.action.validate-libraries"
~~~

The test layer recursively discovers tagged controls. Each registered screen then compares discovered IDs with its declared coverage set. Untagged interactive widgets, missing expected controls, and duplicate IDs are failures. Controls that intentionally cannot be automated (for example, a foreign KiCad window) must be registered with a reason and their platform/integration limitation; they must not silently disappear from the coverage report.

This first milestone tags the Task Hub's action buttons, Close button, and notebook. Other screens remain explicitly partial or inventory-only until their controls receive contracts.

## What counts as proof?

There are different kinds of UI verification, and they must not be conflated:

- **Widget contract:** Invoke the actual Tk command and assert the resulting route or visible state. Use for button/menu/choice/view/pane behavior.
- **Workflow integration:** Use test fixtures around filesystem and KiCad IPC boundaries, then assert the generated files, persisted records, selected schematic item, or editor request. A mocked callback alone is not proof.
- **Desktop interaction:** Use Xvfb/xdotool for focus, key navigation, window stacking, native chooser interactions, and other behavior tied to the display server.
- **Visual evidence:** Capture the production widget tree at useful sizes in light and dark themes. Inspect geometry, clipping, contrast, and labels. A screenshot does not prove that a button works.

The first layer deliberately calls Tk's own `invoke()` method. Python documents `Button.invoke()` as executing the button command and returning its result, and `Menu.invoke(index)` as executing the indexed entry as though it had been clicked. This is more reliable for exhaustive callback coverage than driving every button by pixel coordinates. Keyboard, pointer, focus, and real desktop behavior still require additional tests.

## Framework selection

### Selected: Tkinter + pytest + existing Xvfb/xdotool

This matches the codebase: the application is written in Tkinter, the existing tests already use pytest, and CI already installs a virtual X server and xdotool. pytest's built-in parametrization can generate one test per action or view without another test framework. The contract helpers remain ordinary Python code and need no runtime dependency in the installed plugin.

### Considered: Squish for Tk

Squish explicitly supports Tk applications and complex controls such as item views, menus, and tabs, with Tcl/Tk API access. It is the strongest commercial option to revisit if a paid cross-platform recorder/desktop-automation product is justified. It is not a requirement for the baseline CI design.

### Not selected: pytest-qt

pytest-qt is specifically for PyQt5, PyQt6, and PySide6. This repository uses Tkinter; adding Qt tooling would require a different widget stack and would not test the actual UI.

### Not selected as the cross-platform solution: pywinauto

pywinauto documents Windows Win32 and Microsoft UI Automation backends. Its documentation still describes AT-SPI on Linux and Apple Accessibility support as longer-term plans. It may help with Windows-specific desktop checks but does not currently provide the cross-platform abstraction required for this test architecture.

### Retained for desktop-specific tests: xdotool and Xvfb

They are already present in CI and can send real X11 keyboard input and capture visible windows. Keep these for interaction paths where the window manager matters. Do not use X11 automation as evidence of Windows, macOS, or Wayland behavior.

## Screen catalog and generated inventory

`tests/ui_contract_catalog.json` is the declarative register of screens. For each screen it contains:

- stable screen ID, title, purpose, and current coverage status;
- major functionality or nested notebook views and action labels;
- semantic control IDs when the screen is contract-tested;
- references to automated evidence;
- the current light/dark or state-specific screenshots.

Run:

~~~bash
python scripts/generate_ui_inventory.py
~~~

The generated `docs/ui-inventory.md` shows every registered screen, its major functions, evidence, screenshots, and one of three coverage states:

- **Contracted:** the reusable widget interaction and expected UI transition are asserted.
- **Partial:** useful targeted GUI, integration, model, or screenshot checks exist, but systematic coverage is incomplete.
- **Inventory only:** the screen is tracked so it cannot be forgotten, but systematic interaction contracts do not yet exist.

CI runs the generator's `--check` mode to reject a stale committed inventory.

## Incremental rollout

1. **Task Hub (initial milestone):** verify window creation, all three tabs, all thirteen operation routes, Close, and interactive-widget coverage.
2. **Functional Block Library:** cover search, result selection, preview tabs, implementation/sheet selectors, refresh, library management, and every toolbar action.
3. **Pickers and forms:** cover empty/single/multiple results, selection, confirm/cancel, validation, and source-mode choices.
4. **Managers:** cover add/edit/promote/remove, row selection, dependent associations, immediate persistence, failed writes, and recovery.
5. **External hand-offs:** use real KiCad IPC or editor processes to assert output documents and selections. Add per-platform desktop automation only where the relevant native API exists.

Do not claim that every button works merely because Task Hub dispatch is complete: it proves routing, while downstream operation success still has to be asserted. The generated inventory is designed to make that difference visible.

## References

- [Python Tkinter reference](https://docs.python.org/3/library/tkinter.html) — widget hierarchy, the Tcl/Tk command model, button invocation, and menu invocation.
- [pytest parametrizing tests](https://www.pytest.org/en/latest/example/parametrize.html) — generate independent test cases from each registered action or view.
- [Squish automated GUI testing for Tk](https://www.qt.io/quality-assurance/squish/platform-automated-tk-gui-testing) — commercial Tk support, complex controls, and Tcl/Tk API access.
- [pywinauto getting started](https://pywinauto.readthedocs.io/en/latest/getting_started.html) — Windows UI Automation and Win32 backends; documents the status of Linux/macOS accessibility backends.
- [pytest-qt introduction](https://pytest-qt.readthedocs.io/en/stable/intro.html) — confirms its Qt binding targets (PyQt5, PyQt6, PySide6).
