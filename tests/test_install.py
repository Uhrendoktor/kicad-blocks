"""Tests for the cross-platform KiCad IPC plugin installer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_plugin.py"
SOURCE = ROOT / "plugin" / "kicad-blocks"


class PluginInstallerTests(unittest.TestCase):
    def test_install_and_reinstall_replace_the_complete_plugin_folder(self):
        with tempfile.TemporaryDirectory(prefix="kicad-blocks-install-test-") as temp:
            user_data = Path(temp) / "KiCad"
            command = [
                sys.executable,
                str(INSTALLER),
                "--version",
                "11.0",
                "--documents-home",
                str(user_data),
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            destination = user_data / "11.0" / "plugins" / "kicad-blocks"
            self.assertTrue((destination / "plugin.json").is_file())
            self.assertTrue((destination / "main.py").is_file())
            self.assertTrue((destination / "requirements.txt").is_file())
            self.assertTrue((destination / "kicad_blocks" / "plugin.py").is_file())
            self.assertEqual(
                json.loads((destination / "plugin.json").read_text(encoding="utf-8"))[
                    "identifier"
                ],
                "kicad-blocks",
            )

            stale_file = destination / "must_be_removed_on_reinstall.txt"
            stale_file.write_text("stale", encoding="utf-8")
            subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertFalse(stale_file.exists())

    def test_dry_run_does_not_create_installation_directory(self):
        with tempfile.TemporaryDirectory(prefix="kicad-blocks-install-dry-run-") as temp:
            user_data = Path(temp) / "KiCad"
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
            self.assertIn(str(user_data / "11.0" / "plugins" / "kicad-blocks"), result.stdout)
            self.assertFalse(user_data.exists())

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
