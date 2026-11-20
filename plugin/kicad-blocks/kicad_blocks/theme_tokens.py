"""Pure palette tokens with a deterministic fallback for unavailable KiCad themes."""

from __future__ import annotations

import os

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


def resolve_theme(theme: str | bool | None = None) -> str:
    """Resolve an explicit palette or use the stable light fallback.

    KiCad's active palette is resolved by the plugin through its IPC settings
    directory before this helper is called. A standalone/test host can choose
    a palette through KICAD_BLOCKS_THEME; this pure module intentionally does
    not probe operating-system or desktop-environment APIs.
    """
    requested = (
        str(theme).strip().casefold()
        if theme is not None
        else os.environ.get(THEME_ENVIRONMENT_VARIABLE, "system").strip().casefold()
    )
    if requested in _DARK_VALUES:
        return "dark"
    if requested in _LIGHT_VALUES:
        return "light"
    return "light"


def palette_for_theme(theme: str | bool | None = None) -> dict[str, str]:
    """Return a mutable copy of a complete semantic palette."""
    return dict(_PALETTES[resolve_theme(theme)])
