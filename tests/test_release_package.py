"""Deterministic V1.1 packaging and install-from-archive tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_release.py"
SPEC = importlib.util.spec_from_file_location("kicad_blocks_build_release", BUILD_SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the release builder.")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

INSTALLER_SCRIPT = ROOT / "scripts" / "install_plugin.py"
INSTALLER_SPEC = importlib.util.spec_from_file_location("kicad_blocks_installer", INSTALLER_SCRIPT)
if INSTALLER_SPEC is None or INSTALLER_SPEC.loader is None:
    raise RuntimeError("Could not load the plugin installer.")
installer = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(installer)


class ReleasePackageTests(unittest.TestCase):
    def test_default_linux_user_data_root_matches_xdg_data_home(self):
        with patch.dict(
            os.environ,
            {"XDG_DATA_HOME": "/home/test-user/.local/share"},
            clear=True,
        ):
            self.assertEqual(
                installer.default_documents_home(),
                Path("/home/test-user/.local/share"),
            )

    def test_archive_is_deterministic_and_contains_only_installable_release_inputs(self):
        with tempfile.TemporaryDirectory(prefix="kicad-blocks-release-") as temp:
            first_dir = Path(temp) / "one"
            second_dir = Path(temp) / "two"
            first_zip, first_sum = builder.build_release_package(
                ROOT, first_dir, source_commit="1" * 40
            )
            second_zip, second_sum = builder.build_release_package(
                ROOT, second_dir, source_commit="1" * 40
            )
            self.assertEqual(first_zip.read_bytes(), second_zip.read_bytes())
            self.assertEqual(first_sum.read_bytes(), second_sum.read_bytes())

            digest = hashlib.sha256(first_zip.read_bytes()).hexdigest()
            self.assertEqual(
                first_sum.read_text(encoding="ascii"),
                f"{digest}  {first_zip.name}\n",
            )
            with ZipFile(first_zip) as archive:
                names = archive.namelist()
                prefix = "kicad-blocks-1.1.0-linux-x86_64-cp312/"
                self.assertIn(prefix + "README.md", names)
                self.assertIn(prefix + "LICENSE", names)
                self.assertIn(prefix + "VERSION", names)
                self.assertIn(prefix + "CHANGELOG.md", names)
                self.assertIn(prefix + "scripts/install_plugin.py", names)
                self.assertIn(prefix + "plugin/kicad-blocks/plugin.json", names)
                self.assertIn(prefix + "plugin/kicad-blocks/requirements.txt", names)
                self.assertIn(prefix + "RELEASE_INFO.json", names)
                self.assertFalse(any(name.startswith(prefix + "tests/") for name in names))
                self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
                info = json.loads(archive.read(prefix + "RELEASE_INFO.json"))
                self.assertEqual(info["version"], "1.1.0")
                self.assertEqual(info["source_commit"], "1" * 40)
                self.assertEqual(
                    info["kicad_python_commit"],
                    "76b47d25a826a56a301660f5f572dda968c84d59",
                )
                for entry in archive.infolist():
                    self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))

    def test_extracted_release_archive_installs_using_its_bundled_installer(self):
        with tempfile.TemporaryDirectory(prefix="kicad-blocks-release-install-") as temp:
            temp_root = Path(temp)
            archive_path, checksum_path = builder.build_release_package(
                ROOT, temp_root / "dist", source_commit="2" * 40
            )
            extract_root = temp_root / "extracted"
            extract_root.mkdir()
            with ZipFile(archive_path) as archive:
                archive.extractall(extract_root)

            bundled_root = extract_root / "kicad-blocks-1.1.0-linux-x86_64-cp312"
            documents_home = temp_root / "user-data"
            result = subprocess.run(
                [
                    sys.executable,
                    str(bundled_root / "scripts" / "install_plugin.py"),
                    "--version",
                    "10.99",
                    "--documents-home",
                    str(documents_home),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertIn("KiCad Blocks installed at:", result.stdout)

            installed = (
                documents_home
                / "kicad"
                / "10.99"
                / "plugins"
                / "org.uhrendoktor.kicadblocks"
            )
            self.assertIn(str(installed), result.stdout)
            manifest = json.loads((installed / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["identifier"], "org.uhrendoktor.kicadblocks")
            for action in manifest["actions"]:
                self.assertTrue((installed / action["entrypoint"]).is_file())
            requirements = (installed / "requirements.txt").read_text(encoding="utf-8")
            self.assertIn("releases/download/kicad-python-76b47d25/", requirements)
            self.assertTrue((installed / "kicad-python.lock").is_file())
            self.assertTrue(checksum_path.is_file())

    def test_release_builder_rejects_mismatched_binding_dependency_url(self):
        import shutil

        with tempfile.TemporaryDirectory(prefix="kicad-blocks-release-invalid-") as temp:
            copied = Path(temp) / "source"
            shutil.copytree(
                ROOT,
                copied,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "dist"),
            )
            requirements = copied / "plugin" / "kicad-blocks" / "requirements.txt"
            requirements.write_text(
                "kicad-python @ https://example.invalid/wheel.whl\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "do not point at the wheel locked"):
                builder.build_release_package(copied, Path(temp) / "dist", source_commit="3" * 40)


if __name__ == "__main__":
    unittest.main()
