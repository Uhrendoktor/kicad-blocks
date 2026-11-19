"""Dialog-path contracts for native Tk message and text-entry prompts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))

from kicad_blocks import plugin as plugin_actions


# Native dialog call-site contracts patch the functions and do not need Tk installed.
def _unpatched_native_dialog(*_args: TkOption, **_kwargs: TkOption) -> None:
    raise AssertionError("A native dialog call was not patched by this test.")


if plugin_actions.simpledialog is None:
    plugin_actions.simpledialog = SimpleNamespace(
        askstring=_unpatched_native_dialog,
    )
if plugin_actions.messagebox is None:
    plugin_actions.messagebox = SimpleNamespace(
        askyesno=_unpatched_native_dialog,
        showerror=_unpatched_native_dialog,
        showwarning=_unpatched_native_dialog,
        showinfo=_unpatched_native_dialog,
    )
from kicad_blocks.manifest_types import BlockParameter, BlockVariantManifest
from kicad_blocks.model import parse_parameters
from kicad_blocks.pcb_types import ResolvedBlockLayout
from kicad_blocks.report_types import LibraryValidationReport
from kicad_blocks.repository import BlockRecord
from ui_contracts import Misc, MonkeyPatch, TkOption


def _profile_inputs() -> tuple[
    list[BlockParameter], list[BlockVariantManifest], list[ResolvedBlockLayout]
]:
    return (
        parse_parameters("OUTPUT_VOLTAGE:number=3.3"),
        [
            {"value": "3V3", "file": "3v3.kicad_sch"},
            {"value": "1V8", "file": "1v8.kicad_sch"},
        ],
        [
            {
                "id": "standard",
                "name": "Standard",
                "file": "standard.kicad_pcb",
                "project_file": None,
            },
            {
                "id": "compact",
                "name": "Compact",
                "file": "compact.kicad_pcb",
                "project_file": None,
            },
        ],
    )


def test_profile_wizard_accepts_name_description_variant_parameters_and_layout(
    monkeypatch: MonkeyPatch,
) -> None:
    parent = cast(Misc, object())
    parameters, variants, layouts = _profile_inputs()
    answers = iter(
        [
            "Low voltage",
            "Optimized for 1.8 V operation",
            "1V8",
            "OUTPUT_VOLTAGE=1.8",
            "Compact  [compact]",
        ]
    )
    simple_prompts = []
    confirmations = iter([True, False])

    def askstring(title: str, prompt: str, **kwargs: TkOption) -> str | None:
        simple_prompts.append((title, prompt, kwargs.get("parent")))
        return next(answers)

    confirmation_prompts = []

    def askyesno(title: str, message: str, **kwargs: TkOption) -> bool:
        confirmation_prompts.append((title, message, kwargs.get("parent")))
        return next(confirmations)

    monkeypatch.setattr(plugin_actions.simpledialog, "askstring", askstring)
    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", askyesno)

    profiles = plugin_actions.collect_block_profiles(parent, parameters, variants, layouts)

    assert profiles == [
        {
            "name": "Low voltage",
            "description": "Optimized for 1.8 V operation",
            "variant": "1V8",
            "parameters": {"OUTPUT_VOLTAGE": "1.8"},
            "layout": "compact",
        }
    ]
    assert [item[0] for item in simple_prompts] == [
        "New Configuration Profile",
        "Profile Description",
        "Profile Implementation Variant",
        "Profile Parameter Overrides",
        "Profile PCB Layout",
    ]
    assert all(item[2] is parent for item in simple_prompts)
    assert [item[0] for item in confirmation_prompts] == [
        "Configuration Profiles",
        "Configuration Profiles",
    ]
    assert all(item[2] is parent for item in confirmation_prompts)
    assert "Add another profile?" in confirmation_prompts[1][1]


def test_profile_wizard_decline_returns_empty_without_opening_text_prompts(
    monkeypatch: MonkeyPatch,
) -> None:
    prompts = []
    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        plugin_actions.simpledialog,
        "askstring",
        lambda *args, **_kwargs: prompts.append(args),
    )
    parameters, variants, layouts = _profile_inputs()
    assert plugin_actions.collect_block_profiles(object(), parameters, variants, layouts) == []
    assert prompts == []


def test_profile_wizard_cancel_discards_an_incomplete_profile(monkeypatch: MonkeyPatch) -> None:
    parent = cast(Misc, object())
    parameters, variants, layouts = _profile_inputs()
    prompts = iter(["Low voltage", None])
    simple_calls = []
    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", lambda *_args, **_kwargs: True)

    def askstring(title: str, prompt: str, **kwargs: TkOption) -> str | None:
        simple_calls.append((title, kwargs.get("parent")))
        return next(prompts)

    monkeypatch.setattr(plugin_actions.simpledialog, "askstring", askstring)
    assert plugin_actions.collect_block_profiles(parent, parameters, variants, layouts) is None
    assert simple_calls == [
        ("New Configuration Profile", parent),
        ("Profile Description", parent),
    ]


def test_profile_wizard_blank_name_warns_and_does_not_create_partial_profile(
    monkeypatch: MonkeyPatch,
) -> None:
    parent = object()
    parameters, variants, layouts = _profile_inputs()
    warnings = []
    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(plugin_actions.simpledialog, "askstring", lambda *_args, **_kwargs: "   ")
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showwarning",
        lambda title, message, **kwargs: warnings.append((title, message, kwargs.get("parent"))),
    )
    assert plugin_actions.collect_block_profiles(parent, parameters, variants, layouts) is None
    assert len(warnings) == 1
    assert warnings[0][0] == "Configuration Profile"
    assert "profile name" in warnings[0][1].casefold()
    assert warnings[0][2] is parent


def test_profile_wizard_rejects_unknown_variant_before_requesting_overrides(
    monkeypatch: MonkeyPatch,
) -> None:
    parent = object()
    parameters, variants, layouts = _profile_inputs()
    prompts = iter(["Bad profile", "Description", "9V9"])
    calls: list[tuple[tuple[Path, ...], dict[str, TkOption]]] = []
    monkeypatch.setattr(plugin_actions.messagebox, "askyesno", lambda *_args, **_kwargs: True)

    def askstring(title: str, prompt: str, **kwargs: TkOption) -> str | None:
        calls.append(title)
        return next(prompts)

    monkeypatch.setattr(plugin_actions.simpledialog, "askstring", askstring)
    with pytest.raises(ValueError, match="Unknown profile variant"):
        plugin_actions.collect_block_profiles(parent, parameters, variants, layouts)
    assert calls == [
        "New Configuration Profile",
        "Profile Description",
        "Profile Implementation Variant",
    ]


def _layout_block() -> BlockRecord:
    return cast(
        BlockRecord,
        SimpleNamespace(
            name="Power block",
            manifest={
                "default_layout": "standard",
                "default_variant": "",
                "variants": [],
                "layouts": [
                    {"id": "standard", "name": "Standard"},
                    {"id": "compact", "name": "Compact"},
                ],
            },
        ),
    )


def test_layout_selector_returns_the_user_selected_layout(monkeypatch: MonkeyPatch) -> None:
    parent = object()
    calls = []
    monkeypatch.setattr(
        plugin_actions.simpledialog,
        "askstring",
        lambda title, prompt, **kwargs: (
            calls.append((title, prompt, kwargs.get("parent"))) or "Compact"
        ),
    )
    selected = plugin_actions.choose_block_layout(
        _layout_block(), parent, "Select PCB layout", None
    )
    assert selected == {"id": "compact", "name": "Compact"}
    assert calls[0][0] == "Select PCB layout"
    assert "Standard [standard]" in calls[0][1]
    assert "Compact [compact]" in calls[0][1]
    assert calls[0][2] is parent


def test_layout_selector_cancel_and_invalid_choice_are_distinct(monkeypatch: MonkeyPatch) -> None:
    parent = object()
    monkeypatch.setattr(plugin_actions.simpledialog, "askstring", lambda *_args, **_kwargs: None)
    assert (
        plugin_actions.choose_block_layout(_layout_block(), parent, "Select PCB layout", None)
        is None
    )
    monkeypatch.setattr(
        plugin_actions.simpledialog, "askstring", lambda *_args, **_kwargs: "Missing"
    )
    with pytest.raises(ValueError, match="No PCB layout"):
        plugin_actions.choose_block_layout(_layout_block(), parent, "Select PCB layout", None)


def _install_validation_fakes(
    monkeypatch: MonkeyPatch, tmp_path: Path, report: LibraryValidationReport
) -> tuple[Path, Path, Misc, list[tuple[tuple[Path, ...], dict[str, TkOption]]]]:
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "reports"
    output.mkdir()
    parent = object()
    calls = []
    monkeypatch.setattr(plugin_actions, "_pick_directory", lambda _parent, **_kwargs: output)
    monkeypatch.setattr(plugin_actions, "_library_roots_for", lambda _root: [library])
    monkeypatch.setattr(plugin_actions, "list_blocks", lambda _root: [])
    monkeypatch.setattr(plugin_actions, "find_kicad_cli", lambda: "kicad-cli")
    monkeypatch.setattr(
        plugin_actions,
        "validate_block_library",
        lambda *args, **kwargs: calls.append((args, kwargs)) or report,
    )
    return library, output, parent, calls


@pytest.mark.parametrize(
    ("report", "expected_title"),
    [
        (
            {
                "blocks": [
                    {
                        "name": "Broken block",
                        "status": "failed",
                        "errors": ["manifest is invalid"],
                        "warnings": [],
                    }
                ],
                "passed_count": 0,
                "warning_count": 0,
                "failed_count": 1,
            },
            "Block Library Validation — problems found",
        ),
        (
            {
                "blocks": [
                    {
                        "name": "ERC warning block",
                        "status": "warnings",
                        "errors": [],
                        "warnings": ["unconnected output"],
                    }
                ],
                "passed_count": 0,
                "warning_count": 1,
                "failed_count": 0,
            },
            "Block Library Validation — ERC warnings",
        ),
    ],
)
def test_library_validation_uses_the_correct_warning_dialog(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    report: LibraryValidationReport,
    expected_title: str,
) -> None:
    _library, output, parent, calls = _install_validation_fakes(monkeypatch, tmp_path, report)
    warnings = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showwarning",
        lambda title, message, **kwargs: warnings.append((title, message, kwargs.get("parent"))),
    )
    plugin_actions.validate_shared_library(tmp_path, parent)
    assert len(calls) == 1
    assert calls[0][1]["run_erc"] is True
    assert len(warnings) == 1
    assert warnings[0][0] == expected_title
    assert str(output) in warnings[0][1]
    assert warnings[0][2] is parent


def test_successful_library_validation_does_not_show_a_warning_dialog(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    report = {
        "blocks": [{"name": "Valid block", "status": "passed", "errors": [], "warnings": []}],
        "passed_count": 1,
        "warning_count": 0,
        "failed_count": 0,
    }
    _install_validation_fakes(monkeypatch, tmp_path, report)
    warnings = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showwarning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    plugin_actions.validate_shared_library(tmp_path, object())
    assert warnings == []


def test_validation_report_dialog_cancel_skips_all_library_validation(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    validations = []
    monkeypatch.setattr(plugin_actions, "_pick_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        plugin_actions,
        "_library_roots_for",
        lambda *_args, **_kwargs: validations.append("roots") or [],
    )
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showwarning",
        lambda *_args, **_kwargs: pytest.fail("No report dialog should be shown"),
    )
    plugin_actions.validate_shared_library(tmp_path, object())
    assert validations == []
