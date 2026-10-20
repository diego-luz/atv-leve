"""Safety checks: what must never be touched and whether the TV is still healthy."""

from __future__ import annotations

from dataclasses import dataclass, field

from .adb import AdbRunner
from .device import component_package, is_real_home, list_home_activities, resolve_home
from .profiles import RuleSet


class SafetyError(Exception):
    pass


@dataclass
class Health:
    ok: bool
    home: str | None = None
    problems: list[str] = field(default_factory=list)


def check_health(adb: AdbRunner) -> Health:
    """The TV is healthy when adb answers, the package manager works and a real HOME exists."""
    problems: list[str] = []
    state = adb.get_state()
    if state != "device":
        return Health(False, None, [f"a TV não responde ao adb (estado: {state or 'sem conexão'})"])

    pm = adb.shell("pm path android")
    if "package:" not in pm.stdout:
        problems.append("o gerenciador de pacotes não respondeu")

    home = resolve_home(adb)
    if is_real_home(home):
        pass
    elif home and home.endswith("ResolverActivity"):
        # Several launchers and no default: fine as long as a real one exists.
        if not any(is_real_home(c) for c in list_home_activities(adb)):
            problems.append("nenhuma tela inicial (launcher) disponível")
    else:
        problems.append(
            f"nenhuma tela inicial válida (HOME resolve para: {home or 'nada'})"
        )
    return Health(not problems, home, problems)


def assert_safe_targets(
    targets: list[str], rules: RuleSet, home_pkgs: set[str], current_home: str | None
) -> None:
    """Last line of defense before touching anything. Raises on any protected target."""
    bad = []
    current_pkg = component_package(current_home) if is_real_home(current_home) else None
    for name in targets:
        pattern = rules.protected_match(name)
        if pattern:
            bad.append(f"{name} (protegido por {pattern})")
        elif name in home_pkgs or name == current_pkg:
            bad.append(f"{name} (é uma tela inicial)")
    if bad:
        raise SafetyError("Recusado: o plano contém pacotes protegidos: " + ", ".join(bad))

    # Never leave the TV without a launcher: phase 1 never disables a launcher at all, but if
    # that ever changes, at least one real launcher must remain untouched.
    remaining = home_pkgs - set(targets)
    if home_pkgs and not remaining:
        raise SafetyError("Recusado: o plano removeria todas as telas iniciais da TV.")
