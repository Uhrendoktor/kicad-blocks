"""Pure palette tokens and desktop appearance resolution for KiCad Blocks."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

THEME_ENVIRONMENT_VARIABLE = "KICAD_BLOCKS_THEME"
UI_SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
UI_FONT_SIZES = {
    "caption": 8,
    "body": 9,
    "section": 9,
    "metric": 10,
    "card_title": 11,
    "title": 16,
}

_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "background": "#171c21",
        "surface": "#222a31",
        "surface_alt": "#2b343d",
        "field": "#171c21",
        "hover": "#33404a",
        "selected": "#2d4352",
        "foreground": "#e8edf2",
        "muted": "#a7b2bd",
        "border": "#414c56",
        "accent": "#79b8e4",
        "accent_foreground": "#17232e",
        "basic_foreground": "#9bd8b7",
        "error": "#ff7770",
        "warning": "#f5c56b",
        "info": "#84caff",
        "scroll_thumb": "#505d69",
        "scroll_trough": "#1d242a",
    },
    "light": {
        "background": "#f1f4f7",
        "surface": "#ffffff",
        "surface_alt": "#edf1f5",
        "field": "#ffffff",
        "hover": "#e7eff6",
        "selected": "#e2edf6",
        "foreground": "#202934",
        "muted": "#647180",
        "border": "#d6dee6",
        "accent": "#2f6f9f",
        "accent_foreground": "#ffffff",
        "basic_foreground": "#24684b",
        "error": "#b42318",
        "warning": "#9a6700",
        "info": "#175cd3",
        "scroll_thumb": "#c1cbd5",
        "scroll_trough": "#e8edf2",
    },
}
_DARK_VALUES = {"dark", "true", "1", "yes", "on"}
_LIGHT_VALUES = {"light", "false", "0", "no", "off"}


def _desktop_prefers_dark() -> bool:
    """Read the desktop's appearance preference without requiring a dependency."""
    if sys.platform == "win32":
        try:
            import winreg

            key_path = chr(92).join((
                "Software",
                "Microsoft",
                "Windows",
                "CurrentVersion",
                "Themes",
                "Personalize",
            ))
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        except (ImportError, OSError, ValueError):
            return False

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            return result.returncode == 0 and result.stdout.strip().casefold() == "dark"
        except (OSError, subprocess.TimeoutExpired):
            return False

    if sys.platform.startswith("linux"):
        gtk_theme = os.environ.get("GTK_THEME", "")
        if gtk_theme:
            return "dark" in gtk_theme.casefold()

        for schema, key in (
            ("org.gnome.desktop.interface", "color-scheme"),
            ("org.gnome.desktop.interface", "gtk-theme"),
        ):
            try:
                result = subprocess.run(
                    ["gsettings", "get", schema, key],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode != 0:
                continue
            value = result.stdout.strip().strip("'").strip('"').casefold()
            if key == "color-scheme" and value in {"prefer-dark", "dark"}:
                return True
            if key == "color-scheme" and value in {"prefer-light", "light"}:
                return False
            if key == "gtk-theme" and "dark" in value:
                return True

        kdeglobals = Path.home() / ".config" / "kdeglobals"
        try:
            in_general = False
            for line in kdeglobals.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("["):
                    in_general = line.casefold() == "[general]"
                elif in_general and line.casefold().startswith("colorscheme="):
                    return "dark" in line.split("=", 1)[1].casefold()
        except OSError:
            pass

    return False


def resolve_theme(theme: str | bool | None = None) -> str:
    """Resolve an explicit palette, the environment override, or desktop mode."""
    requested = (
        str(theme).strip().casefold()
        if theme is not None
        else os.environ.get(THEME_ENVIRONMENT_VARIABLE, "system").strip().casefold()
    )
    if requested in _DARK_VALUES:
        return "dark"
    if requested in _LIGHT_VALUES:
        return "light"
    return "dark" if _desktop_prefers_dark() else "light"


def palette_for_theme(theme: str | bool | None = None) -> dict[str, str]:
    """Return a mutable copy of a complete semantic palette."""
    return dict(_PALETTES[resolve_theme(theme)])
