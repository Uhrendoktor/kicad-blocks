#!/usr/bin/env python3
"""Build a deterministic, installable KiCad Blocks release ZIP and SHA-256 file."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_RELATIVE = Path("plugin") / "kicad-blocks"
TOP_LEVEL_FILES = ("README.md", "CHANGELOG.md", "VERSION", "LICENSE")
EXTRA_FILES = (Path("scripts") / "install_plugin.py",)


def _load_installer(root: Path):
    installer_path = root / "scripts" / "install_plugin.py"
    spec = importlib.util.spec_from_file_location("kicad_blocks_release_installer", installer_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load bundled installer: {installer_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_commit(root: Path, explicit: str | None) -> str:
    value = explicit or os.environ.get("GITHUB_SHA")
    if value:
        return value
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _iter_package_files(root: Path) -> list[Path]:
    candidates = [root / item for item in TOP_LEVEL_FILES + EXTRA_FILES]
    candidates.extend(sorted((root / PACKAGE_RELATIVE).rglob("*")))
    selected: list[Path] = []
    for path in candidates:
        if path.is_dir():
            continue
        if path.is_symlink():
            raise ValueError(f"Release package does not allow symlinks: {path}")
        if not path.is_file():
            raise FileNotFoundError(f"Required release file is missing: {path}")
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.name == ".DS_Store":
            continue
        selected.append(path)
    return sorted(set(selected), key=lambda p: p.relative_to(root).as_posix())


def build_release_package(
    source_root: Path,
    output_dir: Path,
    *,
    source_commit: str | None = None,
) -> tuple[Path, Path]:
    root = Path(source_root).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    version_text = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version_text):
        raise ValueError(f"VERSION must be plain MAJOR.MINOR.PATCH, got {version_text!r}.")
    lock = (root / PACKAGE_RELATIVE / "kicad-python.lock").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", lock):
        raise ValueError("kicad-python.lock must contain one 40-character lowercase commit SHA.")

    installer = _load_installer(root)
    plugin_root = root / PACKAGE_RELATIVE
    installer.validate_plugin_package(plugin_root)
    expected_tag = f"kicad-python-{lock[:8]}"
    requirements = (plugin_root / "requirements.txt").read_text(encoding="utf-8")
    expected_wheels = [
        (
            f"https://github.com/Uhrendoktor/kicad-blocks/releases/download/{expected_tag}/"
            f"kicad_python-0.9.0.dev0-cp{minor}-cp{minor}-linux_x86_64.whl"
        )
        for minor in (312, 313, 314)
    ]
    if not all(wheel in requirements for wheel in expected_wheels):
        raise ValueError(
            "Plugin requirements do not point at all CPython 3.12, 3.13 and 3.14 "
            "wheels locked by kicad-python.lock."
        )

    commit = _source_commit(root, source_commit)
    archive_root = f"kicad-blocks-{version_text}-linux-x86_64"
    metadata = {
        "version": version_text,
        "source_commit": commit,
        "kicad_python_commit": lock,
        "dependency_release_tag": expected_tag,
        "supported_runtime": {
            "kicad": "10.99.0 development nightly",
            "os": "Linux x86_64",
            "python": "CPython 3.12, 3.13 and 3.14",
        },
    }

    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f"{archive_root}.zip"
    checksum_path = output / f"{archive_root}.zip.sha256"
    files = _iter_package_files(root)
    release_info = json.dumps(metadata, indent=2, sort_keys=True) + "\n"

    with tempfile.NamedTemporaryFile(
        prefix=".kicad-blocks-release-", suffix=".zip", dir=output, delete=False
    ) as temp:
        temp_path = Path(temp.name)
    try:
        with ZipFile(
            temp_path, "w", compression=ZIP_DEFLATED, compresslevel=9, strict_timestamps=True
        ) as archive:
            for path in files:
                relative = path.relative_to(root).as_posix()
                info = ZipInfo(
                    f"{archive_root}/{relative}",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.flag_bits |= 0x800
                archive.writestr(
                    info, path.read_bytes(),
                    compress_type=ZIP_DEFLATED, compresslevel=9
                )
            info = ZipInfo(
                f"{archive_root}/RELEASE_INFO.json",
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.flag_bits |= 0x800
            archive.writestr(
                info, release_info.encode("utf-8"),
                compress_type=ZIP_DEFLATED, compresslevel=9
            )
        temp_path.replace(archive_path)
    finally:
        temp_path.unlink(missing_ok=True)

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path.write_text(
        f"{digest}  {archive_path.name}\n", encoding="ascii"
    )
    print(f"Built {archive_path} ({archive_path.stat().st_size} bytes)")
    print(f"SHA-256 {digest}")
    return archive_path, checksum_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--commit")
    args = parser.parse_args()
    build_release_package(args.source, args.output_dir, source_commit=args.commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
