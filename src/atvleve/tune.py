"""Performance tweaks (settings). Every original value is saved in the backup first."""

from __future__ import annotations

from dataclasses import dataclass

from .adb import AdbRunner
from .backup import Backup, read_setting, write_setting
from .device import parse_df

ANIMATION_KEYS = (
    ("global", "window_animation_scale"),
    ("global", "transition_animation_scale"),
    ("global", "animator_duration_scale"),
)
ALLOWED_SCALES = ("0", "0.5", "1")
DEFAULT_SCALE = "0.5"

# Experimental tweaks are off by default and must document their risk here before being added.
EXPERIMENTAL: dict[str, str] = {}


@dataclass
class SettingChange:
    namespace: str
    key: str
    value: str
    current: str | None = None

    @property
    def needed(self) -> bool:
        return self.current != self.value


def normalize_scale(scale: str | float) -> str:
    text = str(scale).strip().replace(",", ".")
    if text in ("1.0",):
        text = "1"
    if text in ("0.0",):
        text = "0"
    if text not in ALLOWED_SCALES:
        raise ValueError(f"escala de animação inválida: {scale!r} (use 0, 0.5 ou 1)")
    return text


def animation_changes(adb: AdbRunner, scale: str) -> list[SettingChange]:
    scale = normalize_scale(scale)
    return [
        SettingChange(ns, key, scale, read_setting(adb, ns, key)) for ns, key in ANIMATION_KEYS
    ]


def apply_settings(
    adb: AdbRunner, changes: list[SettingChange], backup: Backup, log=print
) -> list[str]:
    """Apply settings changes; the backup must already hold the original values."""
    saved = {(s["namespace"], s["key"]) for s in backup.data.get("settings", [])}
    failed = []
    for ch in changes:
        if (ch.namespace, ch.key) not in saved:
            raise RuntimeError(f"valor original de {ch.key} não está no backup; abortando")
        if not ch.needed:
            continue
        if write_setting(adb, ch.namespace, ch.key, ch.value):
            backup.record_setting(ch.namespace, ch.key, ch.value)
            log(f"  [green]✓[/green] {ch.namespace}/{ch.key} = {ch.value}")
        else:
            failed.append(ch.key)
            log(f"  [red]✗[/red] {ch.namespace}/{ch.key}")
        backup.save()
    return failed


def data_free_kb(adb: AdbRunner) -> int | None:
    for cmd in ("df -k /data", "df /data"):
        _, free = parse_df(adb.shell_out(cmd))
        if free is not None:
            return free
    return None


def trim_caches(adb: AdbRunner) -> tuple[int | None, int | None]:
    """Ask Android to drop every app cache (`pm trim-caches`). Returns free kB before/after.

    Not reversible, and it does not need to be: caches are rebuilt as apps are used.
    """
    before = data_free_kb(adb)
    adb.shell("pm trim-caches 999G", timeout=120)
    return before, data_free_kb(adb)
