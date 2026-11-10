#!/usr/bin/env python3
"""Install KiCad Blocks into the current user's KiCad IPC plugin directory."""

from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid


PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadblocks"
PLUGIN_DIRECTORY_NAME = "kicad-blocks"
DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "plugin" / PLUGIN_DIRECTORY_NAME

SUPPORTED_NIGHTLY_DIRECTORY = (10, 99)
FIRST_STABLE_SCHEMATIC_IPC_MAJOR = 11


def validate_install_target(
    version: str,
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> None:
    """Reject KiCad/platform targets that cannot load this plugin and its wheel.

    KiCad 9 and 10 expose IPC add-ons only in PCB Editor. Schematic Editor
    plugins are introduced in KiCad 11; the 10.99 development nightly is the
    current pre-release target. The published kicad-python wheel is currently
    CPython 3.12 / Linux x86_64 only.
    """
    if not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("KiCad version must use MAJOR.MINOR format, for example 10.99.")
    major, minor = (int(part) for part in version.split("."))
    if not (
        (major, minor) == SUPPORTED_NIGHTLY_DIRECTORY
        or major >= FIRST_STABLE_SCHEMATIC_IPC_MAJOR
    ):
        raise ValueError(
            "KiCad Blocks requires the KiCad 11 Schematic Editor IPC API. "
            "KiCad 9/10 (including stable 10.0.x) only support IPC add-ons in "
            "PCB Editor. Use the 10.99 development nightly directory, or a "
            "KiCad 11.0+ build."
        )

    selected_platform = platform_name or sys.platform
    selected_machine = (machine or platform.machine()).casefold()
    normalized_machine = {
        "amd64": "x86_64",
        "x64": "x86_64",
    }.get(selected_machine, selected_machine)
    if selected_platform != "linux" or normalized_machine != "x86_64":
        raise ValueError(
            "The currently published KiCad Python IPC wheel supports Linux "
            "x86_64 with CPython 3.12 only. This installer cannot install a "
            f"working plugin on {selected_platform}/{normalized_machine}. "
            "Use the supported Linux x86_64 target or provide a matching "
            "kicad-python development wheel for this platform."
        )



def default_documents_home() -> Path:
    """Return KiCad's platform-specific user-data root."""
    configured = os.environ.get("KICAD_DOCUMENTS_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "win32":
        return (Path.home() / "Documents" / "KiCad").resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Documents" / "KiCad").resolve()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (data_home / "KiCad").expanduser().resolve()


def validate_plugin_package(source: Path) -> dict:
    """Fail before installation if the package manifest or entrypoint is invalid."""
    source = source.expanduser().resolve()
    manifest_path = source / "plugin.json"
    if not source.is_dir() or not manifest_path.is_file():
        raise FileNotFoundError(f"KiCad plugin package is incomplete: {source}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identifier = manifest.get("identifier")
    if not isinstance(identifier, str) or not re.fullmatch(
        r"[\w\d]{2,}\.[\w\d]+\.[\w\d]+",
        identifier,
        flags=re.ASCII,
    ):
        raise ValueError(
            "KiCad IPC plugin identifiers must use reverse-DNS form with at least "
            "three components, for example 'org.example.myplugin'."
        )
    if identifier != PLUGIN_IDENTIFIER:
        raise ValueError(
            f"Expected plugin identifier {PLUGIN_IDENTIFIER!r}, got {identifier!r}."
        )
    runtime = manifest.get("runtime", {})
    if runtime.get("type") != "python":
        raise ValueError("KiCad Blocks must use the Python IPC plugin runtime.")

    actions = manifest.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("plugin.json must declare at least one action.")
    for action in actions:
        entrypoint = action.get("entrypoint")
        if not entrypoint:
            raise ValueError("Every plugin action must declare an entrypoint.")
        entrypoint_path = (source / entrypoint).resolve()
        if not entrypoint_path.is_relative_to(source) or not entrypoint_path.is_file():
            raise FileNotFoundError(
                f"Plugin action entrypoint is missing or outside the package: {entrypoint!r}"
            )
    requirements = source / "requirements.txt"
    if not requirements.is_file():
        raise FileNotFoundError("KiCad Blocks package is missing requirements.txt.")
    return manifest


def install_plugin(
    source: Path,
    documents_home: Path,
    version: str,
    *,
    dry_run: bool = False,
) -> Path:
    """Atomically replace the installed plugin package for one KiCad version."""
    validate_install_target(version)
    source = Path(source).expanduser().resolve()
    documents_home = Path(documents_home).expanduser().resolve()
    manifest = validate_plugin_package(source)
    destination = documents_home / "kicad" / version / "plugins" / manifest["identifier"]

    if dry_run:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix=".kicad-blocks-install-", dir=destination.parent)
    )
    staged_plugin = staging_root / manifest["identifier"]
    backup = destination.with_name(
        f"{destination.name}.backup-{uuid.uuid4().hex[:8]}"
    )
    moved_existing = False
    try:
        shutil.copytree(
            source,
            staged_plugin,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # Verify the staged copy before replacing an existing installation.
        validate_plugin_package(staged_plugin)
        if destination.exists():
            destination.rename(backup)
            moved_existing = True
        try:
            staged_plugin.rename(destination)
        except Exception:
            if moved_existing and backup.exists():
                backup.rename(destination)
                moved_existing = False
            raise
        if moved_existing:
            shutil.rmtree(backup)
            moved_existing = False
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        if moved_existing and backup.exists() and not destination.exists():
            backup.rename(destination)
    legacy_destination = destination.parent / PLUGIN_DIRECTORY_NAME
    if legacy_destination != destination and legacy_destination.is_dir():
        legacy_manifest_path = legacy_destination / "plugin.json"
        try:
            legacy_manifest = json.loads(
                legacy_manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            legacy_manifest = {}
        if legacy_manifest.get("identifier") == "kicad-blocks":
            shutil.rmtree(legacy_destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        required=True,
        help="KiCad MAJOR.MINOR version directory, e.g. 11.0 or 10.99.",
    )
    parser.add_argument(
        "--documents-home",
        type=Path,
        default=default_documents_home(),
        help="Override KiCad's user-data root (defaults to the platform location).",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    destination = install_plugin(
        args.source,
        args.documents_home,
        args.version,
        dry_run=args.dry_run,
    )
    print(f"KiCad Blocks {'would be installed' if args.dry_run else 'installed'} at: {destination}")
    print("Enable the KiCad API in Preferences > Plugins, then restart KiCad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
