"""Package listing and classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .adb import PACKAGE_RE, AdbRunner
from .profiles import RuleSet

ENABLED = "enabled"
DISABLED = "disabled"
UNINSTALLED = "uninstalled"  # removed for user 0 with `pm uninstall -k --user 0`


class Category(str, Enum):
    PROTECTED = "protegido"
    SAFE = "seguro"
    CONFIRM = "confirmar"
    UNKNOWN = "desconhecido"


@dataclass
class Package:
    name: str
    system: bool
    state: str  # ENABLED / DISABLED / UNINSTALLED


@dataclass
class Classified:
    package: Package
    category: Category
    reason: str

    @property
    def name(self) -> str:
        return self.package.name


def parse_pm_list(text: str) -> set[str]:
    """Parse `pm list packages` output ('package:com.foo', optionally with -f/-U extras)."""
    out: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        tokens = line[len("package:"):].split()
        if not tokens:
            continue
        name, extras = tokens[0], tokens[1:]
        # Extras from -U / -i look like "uid:10123" or "installer=com.android.vending".
        if any(":" not in t and "=" not in t for t in extras):
            continue
        if "=" in name:  # `-f` format: package:/path/base.apk=com.foo
            name = name.rsplit("=", 1)[1]
        if PACKAGE_RE.match(name):
            out.add(name)
    return out


def list_packages(adb: AdbRunner) -> dict[str, Package]:
    installed = parse_pm_list(adb.shell_out("pm list packages"))
    system = parse_pm_list(adb.shell_out("pm list packages -s"))
    disabled = parse_pm_list(adb.shell_out("pm list packages -d"))
    # -u also lists packages uninstalled for the user but kept on the system image.
    with_uninstalled = parse_pm_list(adb.shell_out("pm list packages -u"))

    packages: dict[str, Package] = {}
    for name in sorted(installed | with_uninstalled):
        if name not in installed:
            state = UNINSTALLED
        elif name in disabled:
            state = DISABLED
        else:
            state = ENABLED
        # An uninstalled-for-user app is always a system app (otherwise it would be gone).
        is_system = name in system or state == UNINSTALLED
        packages[name] = Package(name, is_system, state)
    return packages


def classify_one(name: str, rules: RuleSet, home_pkgs: set[str]) -> tuple[Category, str]:
    """Decision order from the spec (7.1). Protection always wins."""
    pattern = rules.protected_match(name)
    if pattern:
        return Category.PROTECTED, f"regra de proteção: {pattern}"
    if name in home_pkgs:
        return Category.PROTECTED, "tela inicial (launcher)"
    if name in rules.safe:
        return Category.SAFE, "validado no perfil"
    if name in rules.confirm:
        return Category.CONFIRM, "candidato do perfil (pedir confirmação)"
    return Category.UNKNOWN, "não está em nenhum perfil"


def classify(
    packages: dict[str, Package], rules: RuleSet, home_pkgs: set[str]
) -> list[Classified]:
    out = []
    for pkg in packages.values():
        cat, reason = classify_one(pkg.name, rules, home_pkgs)
        out.append(Classified(pkg, cat, reason))
    order = list(Category)
    out.sort(key=lambda c: (order.index(c.category), c.name))
    return out
