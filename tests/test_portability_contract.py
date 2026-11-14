"""Static guards that keep production feature paths free of OS-specific desktop APIs."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PACKAGE = ROOT / "plugin" / "kicad-blocks" / "kicad_blocks"


class ProductionPortabilityContractTests(unittest.TestCase):
    def test_runtime_package_does_not_call_os_specific_desktop_helpers(self):
        forbidden = (
            "xdotool",
            "xprop",
            "WM_TRANSIENT_FOR",
            "/proc/",
            "WAYLAND_DISPLAY",
            "XDG_SESSION_TYPE",
            "winreg",
            "gsettings",
            "kdeglobals",
            "AppleInterfaceStyle",
            '"defaults"',
            "wtype",
            "ydotool",
            "wmctrl",
            "sys.platform",
            "platform.system(",
            "os.name",
            "os.readlink(",
            "ctypes.windll",
            "os.startfile(",
            "xdg-open",
            "wmctrl",
            "wtype",
            "ydotool",
        )
        sources = {
            path.relative_to(PLUGIN_PACKAGE).as_posix(): path.read_text(encoding="utf-8")
            for path in PLUGIN_PACKAGE.rglob("*.py")
        }
        violations = [
            f"{path}: {token}"
            for path, source in sources.items()
            for token in forbidden
            if token in source
        ]
        self.assertEqual(violations, [], "Production code contains desktop-specific runtime calls")

    def test_library_settings_directory_uses_platformdirs_dependency(self):
        requirements = (ROOT / "plugin" / "kicad-blocks" / "requirements.txt").read_text(
            encoding="utf-8"
        )
        repository = (PLUGIN_PACKAGE / "repository.py").read_text(encoding="utf-8")
        self.assertIn("platformdirs>=4.3,<5", requirements)
        self.assertIn("user_config_path(\"kicad-blocks\", appauthor=False)", repository)
        self.assertNotIn('Path.home() / ".config"', repository)


if __name__ == "__main__":
    unittest.main()
