# Python quality and testing contract

The quality gates are deliberately explicit. They are intended to catch correctness defects and prevent new maintenance debt without applying a blanket style policy to KiCad's dynamic host adapter.

## Blocking checks

| Gate | Scope | What it protects |
| --- | --- | --- |
| Pytest strict mode | All pytest-collected tests under `tests/` | Rejects unknown configuration/markers and non-strict expected failures; the complete suite runs on every CI PR. |
| Ruff lint | All Python under `examples/`, `plugin/`, `scripts/`, and `tests/` | Applies the reviewed correctness, import, bugbear, comprehension, datetime, return, simplification, and modernization rules in `pyproject.toml`. |
| Ruff format | Same complete Python inventory | Ensures the repository-wide formatter contract without rewriting files in CI. |
| Python policy | All tracked Python source roots | AST/tokenizer checks reject every explicit `Any`, unreviewed `object` annotations (including nested generics, returns, and variables), inline diagnostic suppressions, dynamic `eval`/`exec`, and `os.system`. Production modules must have module documentation. |
| Pyright and annotation ratchet | Reviewed core package modules, the KIID narrowing boundary, entrypoint bootstrap, and release/tooling scripts | Standard-mode type checking; `pyrightconfig.json` is the maintained include/exclude inventory. Changed files in this reviewed scope may not increase missing function annotations or generic `object` annotations versus the merge base. The dynamic KiCad/Qt host adapter remains explicitly excluded until its native boundary has usable stubs. |
| Vulture | Pure deterministic core modules | Reports only high-confidence (100%) unused code; dynamic GUI registration and host callbacks are intentionally outside this pass. |
| Test inventory | Every pytest test function/method plus host acceptance scripts | CI fails when `docs/testing-inventory.md` no longer matches test source. |

The rule selection is curated rather than `ALL`/preview. Three central exceptions are deliberate: `E402` is required by KiCad script entrypoints that extend `sys.path` before host imports; `E501` is handled by the formatter; and `SIM105` is a stylistic preference that would obscure best-effort filesystem cleanup and Tk teardown. These are centrally documented exceptions, never per-line suppressions. The type-checking boundary is explicit: new modules in `kicad_blocks/` must be added to the Pyright inventory or have a narrow, documented host exclusion. There is no explicit `Any` exception. JSON manifests use recursive `JsonValue`/`JsonObject` aliases, PCB syntax uses recursive `SExpr`, and KiCad KIID handling uses a narrow Protocol plus a TypeGuard. Two exact `object` inputs are allowed: the untrusted parameter value converted and validated at the model boundary, and the dynamic KIID object narrowed by the TypeGuard.

The import-sort rule remains enabled repository-wide. Eight path-scoped `I001` exceptions cover three standalone IPC entrypoints and five executable KiCad/fixture harnesses whose imports intentionally follow runtime `sys.path` injection; their locations and rationale are centralized in `pyproject.toml`. No inline `noqa`, type-ignore, or pyright-ignore comments are accepted. Fix findings in code or update the central policy only when the exception is architectural, exact, and documented. CI checks are read-only; use local formatting/lint commands to apply changes.

## Local commands

```sh
python -m pytest -q
python -m ruff check examples plugin scripts tests
python -m ruff format --check examples plugin scripts tests
python scripts/check_python_policy.py --base origin/main
python scripts/generate_test_inventory.py --check
python -m pyright --project pyrightconfig.json
python scripts/check_dead_code.py
python -m compileall -q examples plugin scripts tests
```

Run `python scripts/generate_test_inventory.py --write` after changing pytest tests. The inventory includes standalone KiCad GUI/IPC tests separately; they require the same system dependencies and display/native KiCad setup as CI and cannot be validated by pytest alone.

## Repository audit decisions

The test suite deliberately combines distinct validation layers. In particular, `test_pcb_layout_unit.py` validates deterministic transformations without KiCad, while `test_pcb_layout.py` validates the real KiCad/IPC integration. Likewise, schematic/editor unit contracts complement real GUI and IPC acceptance. Release dispatch, wheel publication, immutable asset checks, and release packaging exercise different failure boundaries; the small HTTP response doubles are local to those different protocols and were not merged merely to reduce line count.

The audit removed only two unreferenced compatibility wrappers: `plugin/kicad-blocks/actions.py` and `plugin/kicad-blocks/browse_library.py`. The latter delegated to the already-registered `manage-library` action; neither file is named by `plugin.json` or current user documentation. The three action scripts now share `entrypoints/_bootstrap.py`, replacing repeated `sys.path` mutation and plugin-module loading. The installer test verifies that the shared helper is included in installed packages.

No behavioral test, geometry assertion, KiCad compatibility check, collision/placement constraint, release integrity check, or visual evidence gate was removed or weakened.
