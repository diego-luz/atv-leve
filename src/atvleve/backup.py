"""Backups (written before any change) and restore."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__
from .adb import AdbRunner, validate_package, validate_setting
from .device import COMPONENT_RE, DeviceInfo, is_real_home
from .packages import DISABLED, ENABLED, UNINSTALLED, Package, list_packages
from .profiles import project_root

FORMAT_VERSION = 1


class BackupError(Exception):
    pass


def data_dir() -> Path:
    env = os.environ.get("ATVLEVE_HOME")
    if env:
        return Path(env)
    root = project_root()
    if root:
        return root
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "atvleve"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "atvleve"


def backups_root() -> Path:
    return data_dir() / "backups"


# -- settings helpers ---------------------------------------------------------------


def read_setting(adb: AdbRunner, namespace: str, key: str) -> str | None:
    """Return the current value, or None when the setting does not exist."""
    validate_setting(namespace, key)
    value = adb.shell_out(f"settings get {namespace} {key}").strip()
    return None if value in ("", "null") else value


def write_setting(adb: AdbRunner, namespace: str, key: str, value: str | None) -> bool:
    validate_setting(namespace, key)
    if value is None:
        result = adb.shell(f"settings delete {namespace} {key}")
    else:
        # ':' and '/' for component lists such as enabled_accessibility_services.
        if not all(c.isalnum() or c in "._-/:" for c in value):
            raise ValueError(f"valor de settings inválido: {value!r}")
        result = adb.shell(f"settings put {namespace} {key} {value}")
    return result.ok


# -- backup file ---------------------------------------------------------------------


@dataclass
class Backup:
    path: Path
    data: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        adb: AdbRunner,
        info: DeviceInfo,
        packages: dict[str, Package],
        settings_keys: list[tuple[str, str]],
        profile_name: str = "",
        root: Path | None = None,
        kind: str = "manual",
    ) -> Backup:
        now = datetime.now()
        folder = (root or backups_root()) / info.backup_id
        path = folder / f"{now:%Y-%m-%d_%H%M%S}.json"
        n = 1
        while path.exists():
            path = folder / f"{now:%Y-%m-%d_%H%M%S}-{n}.json"
            n += 1
        data = {
            "format": FORMAT_VERSION,
            "tool": f"atvleve {__version__}",
            "created": now.isoformat(timespec="seconds"),
            "kind": kind,
            "status": "created",
            "device": info.to_dict(),
            "profile": profile_name,
            "packages": {
                p.name: {"system": p.system, "state": p.state} for p in packages.values()
            },
            "settings": [
                {"namespace": ns, "key": key, "value": read_setting(adb, ns, key)}
                for ns, key in settings_keys
            ],
            "changes": [],
            "settings_changed": [],
        }
        backup = cls(path, data)
        backup.save()
        return backup

    @classmethod
    def load(cls, path: Path) -> Backup:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError(f"Não foi possível ler o backup {path}: {exc}") from exc
        if data.get("format") != FORMAT_VERSION or "packages" not in data:
            raise BackupError(f"{path} não parece um backup do ATV Leve")
        return cls(path, data)

    def save(self) -> None:
        """Atomic write: never leaves a half-written backup behind."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            raise BackupError(f"Não foi possível gravar o backup em {self.path}: {exc}") from exc

    def record_change(self, package: str, action: str, ok: bool, detail: str = "") -> None:
        self.data["changes"].append(
            {
                "package": package,
                "action": action,
                "ok": ok,
                "detail": detail,
                "time": datetime.now().isoformat(timespec="seconds"),
            }
        )

    def record_setting(self, namespace: str, key: str, value: str) -> None:
        self.data["settings_changed"].append({"namespace": namespace, "key": key, "value": value})

    def set_status(self, status: str) -> None:
        self.data["status"] = status
        self.save()

    @property
    def device_id(self) -> str:
        return self.path.parent.name


def list_backups(device_id: str | None = None, root: Path | None = None) -> list[Path]:
    root = root or backups_root()
    if not root.exists():
        return []
    pattern = f"{device_id}/*.json" if device_id else "*/*.json"
    return sorted(root.glob(pattern), key=_backup_sort_key)


def _backup_sort_key(path: Path) -> tuple[str, int]:
    """Chronological order from the file name ('2026-10-20_140716' or '..._140716-2')."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}_\d{6})(?:-(\d+))?$", path.stem)
    if not m:
        return (path.stem, 0)
    return (m.group(1), int(m.group(2) or 0))


def latest_backup(device_id: str, root: Path | None = None, with_changes: bool = False) -> Path:
    paths = list_backups(device_id, root)
    if with_changes:
        changed = []
        for p in paths:
            try:
                data = Backup.load(p).data
            except BackupError:
                continue
            if data.get("changes") or data.get("settings_changed"):
                changed.append(p)
        paths = changed or paths
    if not paths:
        raise BackupError(f"Nenhum backup encontrado para esta TV ({device_id}).")
    return paths[-1]


# -- restore -------------------------------------------------------------------------


@dataclass
class RestoreReport:
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    unchanged: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


def enable_package(adb: AdbRunner, name: str) -> tuple[bool, str]:
    validate_package(name)
    result = adb.shell(f"pm enable --user 0 {name}")
    return result.ok, result.output


def reinstall_package(adb: AdbRunner, name: str) -> tuple[bool, str]:
    validate_package(name)
    result = adb.shell(f"cmd package install-existing --user 0 {name}")
    if not result.ok:
        result = adb.shell(f"pm install-existing --user 0 {name}")
    return result.ok, result.output


def restore_packages(
    adb: AdbRunner, original: dict[str, dict], only: set[str] | None = None, log=print
) -> RestoreReport:
    """Bring packages back to the states recorded in `original`.

    Restore only re-enables or reinstalls. It never disables something that is
    enabled now, except a package that was disabled originally and had to be
    reinstalled.
    """
    report = RestoreReport()
    current = list_packages(adb)
    for name, entry in sorted(original.items()):
        if only is not None and name not in only:
            continue
        want = entry.get("state")
        cur = current.get(name)
        if cur is None:
            if want in (ENABLED, DISABLED):
                report.failed.append(f"{name}: não existe mais na TV")
            continue
        if cur.state == want or want == UNINSTALLED:
            report.unchanged += 1
            continue
        if cur.state == UNINSTALLED:
            ok, out = reinstall_package(adb, name)
            if ok and want == DISABLED:
                adb.shell(f"pm disable-user --user 0 {name}")
            label = "reinstalado"
        elif cur.state == DISABLED and want == ENABLED:
            ok, out = enable_package(adb, name)
            label = "reativado"
        else:
            # Enabled now but disabled in the backup: leave it (restore never disables).
            report.unchanged += 1
            continue
        if ok:
            report.done.append(f"{name}: {label}")
            log(f"  [green]✓[/green] {name} {label}")
        else:
            report.failed.append(f"{name}: {out}")
            log(f"  [red]✗[/red] {name}: {out}")
    return report


def restore_settings(adb: AdbRunner, saved: list[dict], log=print) -> RestoreReport:
    report = RestoreReport()
    for entry in saved:
        ns, key, value = entry["namespace"], entry["key"], entry.get("value")
        if read_setting(adb, ns, key) == value:
            report.unchanged += 1
            continue
        if write_setting(adb, ns, key, value):
            shown = value if value is not None else "(padrão do sistema)"
            report.done.append(f"{ns}/{key} = {shown}")
            log(f"  [green]✓[/green] {ns}/{key} = {shown}")
        else:
            report.failed.append(f"{ns}/{key}")
            log(f"  [red]✗[/red] {ns}/{key}")
    return report


def restore_home(adb: AdbRunner, component: str | None, log=print) -> RestoreReport:
    """Give the default-home role back to the launcher recorded before a swap."""
    report = RestoreReport()
    if not component or not COMPONENT_RE.match(component) or not is_real_home(component):
        return report
    result = adb.shell(f"cmd package set-home-activity --user 0 {component}")
    if result.ok:
        report.done.append(f"tela inicial: {component.split('/', 1)[0]}")
        log(f"  [green]✓[/green] tela inicial de volta para {component}")
    else:
        report.failed.append(f"tela inicial: {result.output}")
    return report


def restore_theme(adb: AdbRunner, data: dict, log=print) -> RestoreReport:
    """Put back the launcher theme file saved before a theme change (None = no file)."""
    from .theme import ThemeError, restart_launcher, write_theme_raw  # theme imports backup

    report = RestoreReport()
    if "theme_before" not in data:
        return report
    try:
        write_theme_raw(adb, data["theme_before"])
        restart_launcher(adb)
    except ThemeError as exc:
        report.failed.append(f"tema do launcher: {exc}")
        return report
    report.done.append("tema do launcher")
    log("  [green]✓[/green] tema do launcher de volta ao que era")
    return report


def restore(adb: AdbRunner, backup: Backup, log=print) -> RestoreReport:
    pkgs = restore_packages(adb, backup.data["packages"], log=log)
    sets = restore_settings(adb, backup.data.get("settings", []), log=log)
    home = restore_home(adb, backup.data.get("home_before"), log=log)
    theme = restore_theme(adb, backup.data, log=log)
    report = RestoreReport(
        done=pkgs.done + sets.done + home.done + theme.done,
        failed=pkgs.failed + sets.failed + home.failed + theme.failed,
        unchanged=pkgs.unchanged + sets.unchanged,
    )
    backup.data.setdefault("restores", []).append(
        {
            "time": datetime.now().isoformat(timespec="seconds"),
            "done": report.done,
            "failed": report.failed,
        }
    )
    backup.save()
    return report
