"""Tests for the cross-platform KiCad IPC plugin installer."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_plugin.py"
SOURCE = ROOT / "plugin" / "kicad-blocks"
PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadblocks"


class PluginInstallerTests(unittest.TestCase):
    def test_installer_accepts_supported_cpython_minors(self):
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location("kicad_blocks_installer", INSTALLER)
        installer = module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(installer)

        for minor in (12, 13, 14):
            with self.subTest(minor=minor):
                installer.validate_python_runtime(
                    implementation="cpython",
                    version_info=(3, minor, 4),
                )
        for version in ((3, 11, 9), (3, 15, 0)):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(ValueError, "CPython 3.12, 3.13 and 3.14"),
            ):
                installer.validate_python_runtime(
                    implementation="cpython",
                    version_info=version,
                )
        with self.assertRaisesRegex(ValueError, "CPython 3.12, 3.13 and 3.14"):
            installer.validate_python_runtime(
                implementation="pypy",
                version_info=(3, 14, 0),
            )

    def test_install_and_reinstall_replace_the_complete_plugin_folder(self):
        with tempfile.TemporaryDirectory(prefix="kicad-blocks-install-test-") as temp:
            user_data = pathlib.Path(temp) / "KiCad"
            command = [
                sys.executable,
                str(INSTALLER),
                "--version",
                "11.0",
                "--documents-home",
                str(user_data),
            ]
            legacy_destination = user_data / "kicad" / "11.0" / "plugins" / "kicad-blocks"
            legacy_destination.mkdir(parents=True)
            (legacy_destination / "plugin.json").write_text(
                json.dumps({"identifier": "kicad-blocks"}), encoding="utf-8"
            )
            subprocess.run(command, check=True, capture_output=True, text=True)
            destination = (
                user_data / "kicad" / "11.0" / "plugins" / PLUGIN_IDENTIFIER
            )
            self.assertFalse(legacy_destination.exists())
            self.assertTrue((destination / "plugin.json").is_file())
            self.assertTrue((destination / "main.py").is_file())
            self.assertTrue((destination / "requirements.txt").is_file())
            self.assertTrue((destination / "kicad_blocks" / "plugin.py").is_file())
            self.assertEqual(
                json.loads((destination / "plugin.json").read_text(encoding="utf-8"))[
                    "identifier"
                ],
                PLUGIN_IDENTIFIER,
            )

            stale_file = destination / "must_be_removed_on_reinstall.txt"
            stale_file.write_text("stale", encoding="utf-8")
            subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertFalse(stale_file.exists())

    def test_dry_run_does_not_create_installation_directory(self):
        with tempfile.TemporaryDirectory(prefix="kicad-blocks-install-dry-run-") as temp:
            user_data = pathlib.Path(temp) / "KiCad"
            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALLER),
                    "--version",
                    "11.0",
                    "--documents-home",
                    str(user_data),
                    "--dry-run",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn(str(user_data / "kicad" / "11.0" / "plugins" / PLUGIN_IDENTIFIER), result.stdout)
            self.assertFalse(user_data.exists())

    def test_plugin_runtime_minimum_matches_published_wheel_python_abi(self):
        manifest = json.loads((SOURCE / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["runtime"]["min_version"], "3.12")

    def test_plugin_requirements_use_a_published_binary_wheel(self):
        requirements = (SOURCE / "requirements.txt").read_text(encoding="utf-8")
        for abi in ("cp312", "cp313", "cp314"):
            self.assertIn(
                "https://github.com/Uhrendoktor/kicad-blocks/releases/download/"
                f"kicad-python-76b47d25/kicad_python-0.9.0.dev0-{abi}-{abi}-linux_x86_64.whl",
                requirements,
            )
        self.assertNotIn("git+", requirements)
        self.assertNotIn("@main", requirements)

    def test_installer_rejects_non_reverse_dns_plugin_identifier(self):
        with tempfile.TemporaryDirectory(prefix="kicad-blocks-invalid-id-") as temp:
            source = pathlib.Path(temp) / "plugin"
            shutil.copytree(SOURCE, source)
            manifest_path = source / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["identifier"] = "kicad-blocks"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALLER),
                    "--version",
                    "11.0",
                    "--source",
                    str(source),
                    "--documents-home",
                    str(pathlib.Path(temp) / "KiCad"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reverse-DNS", result.stderr)

    def test_installer_rejects_kicad_10_stable_because_it_has_no_schematic_ipc_addons(self):
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location("kicad_blocks_installer", INSTALLER)
        installer = module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(installer)
        with self.assertRaisesRegex(ValueError, "PCB Editor"):
            installer.validate_install_target("10.0", platform_name="linux", machine="x86_64")
        with self.assertRaisesRegex(ValueError, "PCB Editor"):
            installer.validate_install_target("10.8", platform_name="linux", machine="x86_64")

    def test_installer_allows_kiCad_10_99_nightly_and_11_or_newer(self):
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location("kicad_blocks_installer", INSTALLER)
        installer = module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(installer)
        for version in ("10.99", "11.0", "11.1", "12.0"):
            with self.subTest(version=version):
                installer.validate_install_target(version, platform_name="linux", machine="x86_64")

    def test_installer_rejects_unsupported_platform_and_architecture(self):
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location("kicad_blocks_installer", INSTALLER)
        installer = module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(installer)
        with self.assertRaisesRegex(ValueError, "Linux x86_64"):
            installer.validate_install_target("10.99", platform_name="win32", machine="AMD64")
        with self.assertRaisesRegex(ValueError, "Linux x86_64"):
            installer.validate_install_target("10.99", platform_name="linux", machine="aarch64")
        with self.assertRaisesRegex(ValueError, "Linux x86_64"):
            installer.validate_install_target("11.0", platform_name="darwin", machine="arm64")

    def test_installer_rejects_invalid_kicad_version_format(self):
        with tempfile.TemporaryDirectory(prefix="kicad-blocks-install-version-") as temp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALLER),
                    "--version",
                    "11",
                    "--documents-home",
                    temp,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("MAJOR.MINOR", result.stderr)


if __name__ == "__main__":
    unittest.main()
