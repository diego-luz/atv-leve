"""Plan building (pure, no I/O) and plan execution in small, health-checked batches."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .adb import AdbRunner, validate_package
from .backup import Backup, enable_package, reinstall_package
from .packages import DISABLED, ENABLED, Category, Classified
from .profiles import RuleSet
from .safety import Health, assert_safe_targets, check_health
from .tune import SettingChange, apply_settings

DISABLE = "disable"
UNINSTALL = "uninstall"
DEFAULT_BATCH = 5


@dataclass
class PlanItem:
    name: str
    category: Category
    reason: str
    action: str


@dataclass
class Plan:
    items: list[PlanItem] = field(default_factory=list)
    pending_confirm: list[Classified] = field(default_factory=list)
    settings: list[SettingChange] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    already_done: list[str] = field(default_factory=list)

    @property
    def targets(self) -> list[str]:
        return [i.name for i in self.items]

    @property
    def settings_needed(self) -> list[SettingChange]:
        return [s for s in self.settings if s.needed]

    @property
    def empty(self) -> bool:
        return not self.items and not self.settings_needed


def build_plan(
    classified: list[Classified],
    rules: RuleSet,
    home_pkgs: set[str],
    current_home: str | None,
    include: Iterable[str] = (),
    confirmed: Iterable[str] = (),
    uninstall: bool = False,
    settings: Iterable[SettingChange] = (),
) -> Plan:
    include = set(include)
    confirmed = set(confirmed)
    action = UNINSTALL if uninstall else DISABLE
    plan = Plan(settings=list(settings))
    by_name = {c.name: c for c in classified}

    for name in sorted(include - set(by_name)):
        plan.refused.append((name, "não está instalado nesta TV"))

    for c in classified:
        actionable = c.package.state == ENABLED or (uninstall and c.package.state == DISABLED)
        if c.category is Category.PROTECTED:
            if c.name in include:
                plan.refused.append((c.name, f"protegido ({c.reason})"))
            continue
        if not actionable:
            if c.category is Category.SAFE or c.name in include:
                plan.already_done.append(c.name)
            continue
        if c.category is Category.SAFE:
            plan.items.append(PlanItem(c.name, c.category, c.reason, action))
        elif c.category is Category.CONFIRM:
            if c.name in confirmed or c.name in include:
                plan.items.append(PlanItem(c.name, c.category, c.reason, action))
            else:
                plan.pending_confirm.append(c)
        elif c.name in include:  # UNKNOWN: only with explicit manual selection
            plan.items.append(PlanItem(c.name, c.category, "selecionado manualmente", action))

    assert_safe_targets(plan.targets, rules, home_pkgs, current_home)
    return plan


# -- execution -----------------------------------------------------------------------


@dataclass
class ApplyResult:
    done: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)
    aborted: str = ""
    settings_failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.aborted and not self.failed and not self.settings_failed


def run_action(adb: AdbRunner, item: PlanItem) -> tuple[bool, str]:
    validate_package(item.name)
    if item.action == UNINSTALL:
        result = adb.shell(f"pm uninstall -k --user 0 {item.name}")
        return result.ok and "Success" in result.output, result.output
    result = adb.shell(f"pm disable-user --user 0 {item.name}")
    return result.ok and "disabled" in result.output, result.output


def undo_action(adb: AdbRunner, name: str, action: str, was_state: str = ENABLED) -> bool:
    if action == UNINSTALL:
        ok, _ = reinstall_package(adb, name)
        if ok and was_state == DISABLED:
            adb.shell(f"pm disable-user --user 0 {name}")
        return ok
    ok, _ = enable_package(adb, name)
    return ok


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def apply_plan(
    adb: AdbRunner,
    plan: Plan,
    backup: Backup,
    batch_size: int = DEFAULT_BATCH,
    log=print,
    health: Callable[[AdbRunner], Health] = check_health,
) -> ApplyResult:
    """Execute the plan. The backup must already be saved on disk."""
    if not backup.path.exists():
        raise RuntimeError("backup não gravado; nada foi feito")
    result = ApplyResult()
    original = backup.data["packages"]

    before = health(adb)
    if not before.ok:
        result.aborted = "a TV já não estava saudável antes de começar: " + "; ".join(
            before.problems
        )
        backup.set_status("aborted")
        return result

    backup.set_status("in_progress")
    total = len(plan.items)
    for n, batch in enumerate(_chunks(plan.items, max(1, batch_size)), start=1):
        log(f"[bold]Lote {n}[/bold] ({min(n * batch_size, total)}/{total})")
        changed: list[PlanItem] = []
        for item in batch:
            ok, out = run_action(adb, item)
            backup.record_change(item.name, item.action, ok, out)
            if ok:
                changed.append(item)
                result.done.append(item.name)
                log(f"  [green]✓[/green] {item.name}")
            else:
                result.failed.append((item.name, out))
                log(f"  [yellow]![/yellow] {item.name}: {out or 'falhou'}")
        backup.save()

        h = health(adb)
        if not h.ok:
            log("[bold red]A TV deu sinal de problema depois deste lote:[/bold red] "
                + "; ".join(h.problems))
            log("Revertendo o lote automaticamente...")
            for item in changed:
                was = original.get(item.name, {}).get("state", ENABLED)
                if undo_action(adb, item.name, item.action, was):
                    result.rolled_back.append(item.name)
                    result.done.remove(item.name)
                    backup.record_change(item.name, "rollback", True)
                    log(f"  [green]↺[/green] {item.name}")
                else:
                    backup.record_change(item.name, "rollback", False)
                    log(f"  [red]✗[/red] não consegui reverter {item.name}")
            after = health(adb)
            result.aborted = (
                "lote revertido; a TV voltou ao normal"
                if after.ok
                else "lote revertido, mas a TV ainda não parece saudável: "
                + "; ".join(after.problems)
            )
            backup.set_status("rolled_back")
            return result

    if plan.settings_needed:
        log("[bold]Ajustes de desempenho[/bold]")
        result.settings_failed = apply_settings(adb, plan.settings_needed, backup, log=log)

    backup.set_status("completed")
    return result
