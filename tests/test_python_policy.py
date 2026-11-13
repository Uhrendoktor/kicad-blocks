"""Regression tests for the repository's syntax-aware Python policy gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "scripts" / "check_python_policy.py"
SPEC = importlib.util.spec_from_file_location("kicad_blocks_python_policy", POLICY_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load scripts/check_python_policy.py.")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def _write_source(root: Path, relative: str, source: str) -> Path:
    """Create one policy fixture and return its path."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_rejects_inline_suppressions_and_dynamic_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    path = _write_source(
        tmp_path,
        "plugin/example.py",
        '"""Policy fixture."""\n'
        "import os\n"
        "value = eval('1 + 1')\n"
        "os.system('echo unsafe')\n"
        "result = 1  # noqa: F401\n",
    )

    errors = policy._check_file(path)

    assert any("inline diagnostic suppression" in error for error in errors)
    assert any("dynamic eval()" in error for error in errors)
    assert any("os.system()" in error for error in errors)


def test_rejects_explicit_any_outside_reviewed_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    path = _write_source(
        tmp_path,
        "plugin/example.py",
        '"""Policy fixture."""\n'
        "from typing import Any as DynamicValue\n"
        "import typing as typing_module\n"
        "def process(value: DynamicValue) -> typing_module.Any:\n"
        "    return value\n"
        "items: list[typing_module.Any] = []\n",
    )

    errors = policy._check_file(path)

    assert any("explicit Any" in error for error in errors)
    assert sum("explicit Any" in error for error in errors) >= 3


def test_allows_the_one_documented_untrusted_object_input(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    path = _write_source(
        tmp_path,
        "plugin/kicad-blocks/kicad_blocks/model.py",
        '"""Model policy fixture."""\n'
        "def validate_parameter_value(value: object) -> str:\n"
        "    return str(value)\n",
    )

    assert policy._check_file(path) == []


def test_requires_documentation_for_production_python_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    path = _write_source(tmp_path, "scripts/undocumented.py", "value = 1\n")

    errors = policy._check_file(path)

    assert any("missing module docstring" in error for error in errors)


def test_pyright_inventory_requires_every_core_module_or_a_host_exclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    core = tmp_path / "plugin" / "kicad-blocks" / "kicad_blocks"
    core.mkdir(parents=True)
    (core / "model.py").write_text('"""Model."""\n', encoding="utf-8")
    (core / "plugin.py").write_text('"""Host adapter."""\n', encoding="utf-8")
    config = tmp_path / "pyrightconfig.json"
    config.write_text(
        '{"include":["plugin/kicad-blocks/kicad_blocks"],'
        '"exclude":["plugin/kicad-blocks/kicad_blocks/plugin.py"]}',
        encoding="utf-8",
    )

    assert policy._check_pyright_inventory() == []

    config.write_text('{"include":["scripts"] ,"exclude":[]}', encoding="utf-8")
    errors = policy._check_pyright_inventory()
    assert any("model.py" in error for error in errors)
    assert any("plugin.py" in error for error in errors)
