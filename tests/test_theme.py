"""Unit tests for the shared desktop theme tokens and palette selection."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))

from kicad_blocks.theme import (  # noqa: E402
    AutoHideScrollbar,
    THEME_ENVIRONMENT_VARIABLE,
    UI_FONT_SIZES,
    UI_SPACING,
    palette_for_theme,
    resolve_theme,
)


def test_shared_tokens_match_the_reference_design_system():
    assert UI_SPACING == {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
    assert UI_FONT_SIZES == {
        "caption": 8,
        "body": 9,
        "section": 9,
        "metric": 10,
        "card_title": 11,
        "title": 16,
    }
    assert AutoHideScrollbar.IDLE_WIDTH == 2
    assert AutoHideScrollbar.ACTIVE_WIDTH == 10
    assert AutoHideScrollbar.HIDE_DELAY_MS == 900


def test_explicit_theme_and_environment_override_take_precedence():
    assert resolve_theme("dark") == "dark"
    assert resolve_theme("light") == "light"
    with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "dark"}):
        assert resolve_theme() == "dark"
    with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "OFF"}):
        assert resolve_theme() == "light"


def test_system_theme_uses_desktop_detection_when_not_overridden():
    with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "system"}):
        with patch("kicad_blocks.theme._desktop_prefers_dark", return_value=True):
            assert resolve_theme() == "dark"
        with patch("kicad_blocks.theme._desktop_prefers_dark", return_value=False):
            assert resolve_theme() == "light"


def test_palette_returns_independent_semantic_color_maps():
    light = palette_for_theme("light")
    dark = palette_for_theme("dark")
    assert light["background"] != dark["background"]
    assert light["surface"] != dark["surface"]
    assert light["accent"] != dark["accent"]
    for palette in (light, dark):
        assert palette["accent_foreground"]
        assert palette["basic_foreground"]
        for severity in ("error", "warning", "info"):
            assert palette[severity] != palette["foreground"]
            assert palette[severity] != palette["muted"]
    light["background"] = "#000000"
    assert palette_for_theme("light")["background"] != "#000000"
