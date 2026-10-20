"""Replacing the stock home screen (the main source of ads and of RAM use on these TVs).

This is the only place allowed to disable a launcher or the setup wizard, and only
under its own checks: the new launcher must already answer HOME and be the default
before anything stock is touched, and every step is verified and rolled back on failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .adb import AdbRunner, validate_package
from .backup import Backup, enable_package, read_setting, write_setting
from .device import (
    COMPONENT_RE,
    DeviceInfo,
    component_package,
    is_real_home,
    list_home_activities,
    resolve_home,
)
from .packages import ENABLED, Package
from .safety import check_health

STOCK_LAUNCHERS = (
    "com.google.android.tvlauncher",
    "com.google.android.apps.tv.launcherx",
    "com.google.android.leanbacklauncher",
)
# Its RecoveryActivity claims HOME once the stock launcher is gone (seen on MT5867 sets).
SETUP_WIZARD = "com.google.android.tungsten.setupwraith"
NOT_A_LAUNCHER = {SETUP_WIZARD}

# The cleanyourtv launcher's optional accessibility service: sends the remote's Input
# button (KEYCODE_TV / KEYCODE_TV_INPUT) to the input switcher, which on Google TV only
# the stock launcher answers. It only filters keys; it cannot read the screen.
INPUT_KEY_LAUNCHER = "com.gathin.tvhome"
INPUT_KEY_SERVICE = f"{INPUT_KEY_LAUNCHER}/{INPUT_KEY_LAUNCHER}.InputKeyService"
A11Y_SETTINGS = [("secure", "enabled_accessibility_services"),
                 ("secure", "accessibility_enabled")]

KNOWN = {
    "com.gathin.tvhome": "cleanyourtv home",
    "me.efesser.flauncher": "FLauncher",
    "com.spocky.projengmenu": "Projectivy Launcher",
}


class LauncherError(Exception):
    pass


@dataclass
class HomeCandidate:
    component: str
    package: str
    label: str
    stock: bool


def candidates(adb: AdbRunner) -> list[HomeCandidate]:
    out = []
    for comp in list_home_activities(adb):
        pkg = component_package(comp)
        if not is_real_home(comp) or pkg in NOT_A_LAUNCHER:
            continue
        out.append(HomeCandidate(comp, pkg, KNOWN.get(pkg, pkg), pkg in STOCK_LAUNCHERS))
    return out


def alternatives(adb: AdbRunner) -> list[HomeCandidate]:
    return [c for c in candidates(adb) if not c.stock]


@dataclass
class SwapResult:
    ok: bool
    new_home: str
    disabled: list[str] = field(default_factory=list)
    rolled_back: bool = False
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def _has_input_key(services: list[str]) -> bool:
    return any(s.startswith(INPUT_KEY_LAUNCHER + "/") and s.endswith("InputKeyService")
               for s in services)


def enable_input_key(adb: AdbRunner, backup: Backup) -> bool:
    """Add InputKeyService to the enabled services, keeping any others the TV already has.

    The original values must already be in `backup` (A11Y_SETTINGS), so restore puts them back.
    """
    ns, key = A11Y_SETTINGS[0]
    services = [s for s in (read_setting(adb, ns, key) or "").split(":") if s]
    if not _has_input_key(services):
        services.append(INPUT_KEY_SERVICE)
        value = ":".join(services)
        if not write_setting(adb, ns, key, value):
            return False
        backup.record_setting(ns, key, value)
    if not write_setting(adb, *A11Y_SETTINGS[1], "1"):
        return False
    backup.record_setting(*A11Y_SETTINGS[1], "1")
    backup.save()
    return _has_input_key((read_setting(adb, ns, key) or "").split(":"))


def set_home(adb: AdbRunner, component: str) -> bool:
    if not COMPONENT_RE.match(component):
        raise ValueError(f"componente inválido: {component!r}")
    result = adb.shell(f"cmd package set-home-activity --user 0 {component}")
    return result.ok


def install_apk(adb: AdbRunner, path: str | Path) -> str:
    """Install a local APK (never downloads). Returns adb's answer; raises on failure."""
    apk = Path(path).expanduser()
    if apk.suffix.lower() != ".apk" or not apk.is_file():
        raise LauncherError(f"Arquivo APK não encontrado: {apk}")
    result = adb.install(str(apk.resolve()))
    if "Success" not in result.output:
        raise LauncherError(f"A TV não instalou o APK: {result.output or 'sem resposta'}")
    return result.output


def starts(adb: AdbRunner, component: str) -> bool:
    """Open the launcher once; HOME resolving to it does not prove the activity comes up."""
    if not COMPONENT_RE.match(component):
        raise ValueError(f"componente inválido: {component!r}")
    out = adb.shell(f"am start -W -n {component}", timeout=60).output
    return "Status: ok" in out and not any(
        line.startswith("Error") for line in out.splitlines())


def _home_is(adb: AdbRunner, component: str) -> bool:
    return resolve_home(adb) == component and check_health(adb).ok


def swap(
    adb: AdbRunner,
    new_package: str,
    info: DeviceInfo,
    packages: dict[str, Package],
    root=None,
    log=print,
    input_key: bool = False,
) -> tuple[Backup, SwapResult]:
    """Make `new_package` the home screen, then disable the setup wizard and stock launchers.

    The wizard goes first: with it still enabled its RecoveryActivity takes HOME as
    soon as the stock launcher is disabled.
    """
    validate_package(new_package)
    if input_key and new_package != INPUT_KEY_LAUNCHER:
        raise LauncherError(f"O ajuste do botão Input só existe no launcher {INPUT_KEY_LAUNCHER}.")
    if not check_health(adb).ok:
        raise LauncherError("A TV não está saudável agora; nada foi feito.")
    alt = {c.package: c for c in alternatives(adb)}
    if new_package not in alt:
        raise LauncherError(
            f"{new_package} não aparece como tela inicial nesta TV. Instale e abra o app uma vez."
        )
    new = alt[new_package]
    previous = resolve_home(adb)
    if not is_real_home(previous) or component_package(previous) == new_package:
        # With two launchers and no default HOME shows the chooser; undo goes to the stock one.
        stock = [c.component for c in candidates(adb) if c.stock]
        previous = stock[0] if stock else None

    backup = Backup.create(adb, info, packages, A11Y_SETTINGS if input_key else [], root=root,
                           kind="launcher")
    backup.data["home_before"] = previous
    backup.data["home_after"] = new.component
    backup.set_status("in_progress")

    def rollback(reason: str, disabled: list[str]) -> SwapResult:
        log(f"[red]Revertendo:[/red] {reason}")
        for name in reversed(disabled):
            ok, out = enable_package(adb, name)
            backup.record_change(name, "rollback", ok, out)
        if previous:
            set_home(adb, previous)
        backup.set_status("rolled_back")
        return SwapResult(False, new.component, [], True, reason)

    if not starts(adb, new.component):
        return backup, rollback(f"o {new.label} não abriu na TV", [])
    log(f"  [green]✓[/green] {new.label} abre normalmente")

    # A stock launcher may declare a higher HOME priority (tvlauncher uses 2), and then
    # Android ignores the preference until it is disabled. So the preference is set now
    # and again after every step, and the Home may stay on the stock launcher until the
    # last one is gone, but never on anything else.
    if not set_home(adb, new.component):
        return backup, rollback("a TV recusou o comando de tela inicial padrão", [])
    if not check_health(adb).ok:
        return backup, rollback("a TV ficou sem tela inicial válida ao definir o padrão", [])
    if resolve_home(adb) == new.component:
        log(f"  [green]✓[/green] {new.label} definido como tela inicial")
    else:
        log("  · a tela original tem prioridade maior; o padrão vale depois de desativá-la")

    targets = [p for p in (SETUP_WIZARD, *STOCK_LAUNCHERS)
               if p in packages and packages[p].state == ENABLED]
    disabled: list[str] = []
    for i, name in enumerate(targets):
        result = adb.shell(f"pm disable-user --user 0 {name}")
        ok = result.ok and "disabled" in result.output
        backup.record_change(name, "disable", ok, result.output)
        backup.save()
        if not ok:
            return backup, rollback(f"a TV recusou desativar {name}", disabled)
        disabled.append(name)
        # Android drops the home preference whenever the set of HOME apps changes.
        set_home(adb, new.component)
        home = resolve_home(adb)
        stock_left = any(p in STOCK_LAUNCHERS for p in targets[i + 1:])
        if home == new.component and check_health(adb).ok:
            log(f"  [green]✓[/green] {name} desativado; Home no {new.label}")
        elif (stock_left and is_real_home(home) and component_package(home) in STOCK_LAUNCHERS
              and check_health(adb).ok):
            log(f"  [green]✓[/green] {name} desativado; Home ainda na tela original")
        else:
            return backup, rollback(
                f"depois de desativar {name} o Home foi para {home or 'nada'}", disabled)

    if not _home_is(adb, new.component):
        return backup, rollback(f"no fim o Home não ficou no {new.label}", disabled)
    backup.record_change(new.package, "set-home", True, new.component)
    backup.save()

    warnings = []
    if input_key:
        if enable_input_key(adb, backup):
            log("  [green]✓[/green] botão Input do controle ligado ao seletor de entradas")
        else:
            warnings.append("não consegui ativar o serviço do botão Input")
            log(f"  [yellow]![/yellow] {warnings[-1]}")
    backup.set_status("completed")
    return backup, SwapResult(True, new.component, disabled, warnings=warnings)
