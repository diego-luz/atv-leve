"""Theme of the cleanyourtv launcher (com.gathin.tvhome): for now, the background colour.

The launcher overlays `config.json` from its external files folder on its built-in
config. Only the keys atvleve sets are changed; the rest of an existing theme is kept,
and the previous file goes into the backup so restore can put it back.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path

from .adb import AdbRunner
from .backup import read_setting, write_setting

LAUNCHER = "com.gathin.tvhome"
THEME_DIR = f"/sdcard/Android/data/{LAUNCHER}/files"
THEME_FILE = f"{THEME_DIR}/config.json"
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
DEFAULT_COLOR = "#000000"

# The launcher draws white text and icons over this colour, so the presets stay dark.
PRESETS = {
    "#000000": "Preto",
    "#1A1A1E": "Grafite",
    "#070A14": "Azul-noite",
    "#0A1A33": "Azul-marinho",
    "#170A2B": "Roxo",
    "#0B1F17": "Verde-escuro",
    "#2A0A14": "Vinho",
    "#2B1A0A": "Café",
}


class ThemeError(Exception):
    pass


def normalize_color(value: str) -> str:
    value = (value or "").strip()
    if not value.startswith("#"):
        value = "#" + value
    if not COLOR_RE.match(value):
        raise ValueError(f"Cor inválida: {value!r}. Use o formato #RRGGBB, ex.: #170A2B.")
    return value.upper()


def luminance(color: str) -> float:
    """Relative luminance (WCAG), 0 = black, 1 = white."""
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def too_light(color: str) -> bool:
    # Below 4.5:1 against white text: (1 + 0.05) / (L + 0.05) < 4.5  <=>  L > 0.183
    return luminance(color) > 0.183


def read_theme_raw(adb: AdbRunner) -> str | None:
    result = adb.shell(f"cat {THEME_FILE}")
    if result.returncode != 0 or "No such file" in result.output:
        return None
    return result.stdout


def current_color(adb: AdbRunner) -> str:
    raw = read_theme_raw(adb)
    try:
        value = json.loads(raw).get("background_color") if raw else None
        return normalize_color(value) if value else DEFAULT_COLOR
    except (ValueError, AttributeError):
        return DEFAULT_COLOR


def write_theme_raw(adb: AdbRunner, raw: str | None) -> None:
    """Put `raw` as config.json on the TV, or remove the file when None."""
    if raw is None:
        adb.shell(f"rm -f {THEME_FILE}")
        return
    adb.shell(f"mkdir -p {THEME_DIR}")
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "config.json"
        local.write_text(raw, encoding="utf-8")
        result = adb.push(str(local), THEME_FILE)
    if not result.ok or read_theme_raw(adb) != raw:
        raise ThemeError(f"Não consegui gravar o tema na TV: {result.output or 'sem resposta'}")


def restart_launcher(adb: AdbRunner, settle: float = 1.0) -> None:
    """Restart the launcher so it rereads the theme.

    A force-stop drops the app's accessibility services (the optional Input key helper),
    so they are put back as the cleanyourtv theme script does.
    """
    services = read_setting(adb, "secure", "enabled_accessibility_services")
    adb.shell(f"am force-stop {LAUNCHER}")
    adb.shell(f"am start -n {LAUNCHER}/.MainActivity")
    if services and LAUNCHER in services:
        if settle:
            time.sleep(settle)
        write_setting(adb, "secure", "enabled_accessibility_services", None)
        write_setting(adb, "secure", "enabled_accessibility_services", services)
        write_setting(adb, "secure", "accessibility_enabled", "1")


def set_background(adb: AdbRunner, color: str, backup, settle: float = 1.0) -> str:
    """Change only background_color, keeping the rest of the theme. Returns the colour."""
    color = normalize_color(color)
    before = read_theme_raw(adb)
    try:
        theme = json.loads(before) if before else {}
        if not isinstance(theme, dict):
            theme = {}
    except ValueError:
        theme = {}
    backup.data["theme_before"] = before
    backup.save()
    theme["background_color"] = color
    write_theme_raw(adb, json.dumps(theme, indent=2, ensure_ascii=False) + "\n")
    backup.record_change(LAUNCHER, "theme", True, f"background_color={color}")
    backup.save()
    restart_launcher(adb, settle)
    return color
