"""Read-only analysis shared by the CLI and the web panel."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .adb import AdbDeviceError, AdbRunner, normalize_host
from .backup import Backup, RestoreReport, latest_backup, restore
from .device import DeviceInfo, ProcMem, get_info, home_packages, top_memory
from .launcher import (
    STOCK_LAUNCHERS,
    HomeCandidate,
    SwapResult,
    alternatives,
    candidates,
    swap,
)
from .packages import Category, Classified, Package, classify, list_packages
from .planner import DEFAULT_BATCH, ApplyResult, Plan, apply_plan, build_plan
from .profiles import (
    Profile,
    RuleSet,
    build_rules,
    find_profile,
    load_generic,
    protected_conflicts,
    select_profile,
)
from .safety import Health, check_health
from .theme import LAUNCHER as THEME_LAUNCHER
from .theme import PRESETS, current_color, set_background, too_light
from .tune import DEFAULT_SCALE, animation_changes, trim_caches

HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


def validate_host(ip: str, port: int | str | None = None) -> str:
    """Return 'host:port' or raise ValueError (texts in pt-BR)."""
    ip = (ip or "").strip()
    if ":" in ip and port in (None, ""):
        ip, port = ip.rsplit(":", 1)
    if not HOST_RE.match(ip):
        raise ValueError("IP inválido. Exemplo: 192.168.0.20")
    try:
        port_n = int(port) if port not in (None, "") else 5555
    except ValueError as exc:
        raise ValueError("Porta inválida. Use um número, por exemplo 5555.") from exc
    if not 1 <= port_n <= 65535:
        raise ValueError("Porta inválida. Use um número entre 1 e 65535.")
    return f"{ip}:{port_n}"


def connect_host(adb: AdbRunner, host: str) -> AdbRunner:
    """`adb connect` + authorization check. Raises AdbDeviceError with a pt-BR message."""
    host = normalize_host(host)
    result = adb.connect(host)
    if "connected to" not in result.output.lower():
        raise AdbDeviceError(
            f"Não consegui conectar em {host}: {result.output or 'sem resposta'}. "
            "Confira se a TV está ligada, na mesma rede e com a depuração ativada."
        )
    adb.serial = host
    state = adb.get_state()
    if state != "device":
        raise AdbDeviceError(
            f"A TV {host} está com estado '{state or 'desconhecido'}'. Aceite o pedido "
            "\"Permitir depuração\" na tela da TV e tente de novo."
        )
    return adb


@dataclass
class Context:
    adb: AdbRunner
    info: DeviceInfo
    profile: Profile | None
    rules: RuleSet
    packages: dict[str, Package]
    home_pkgs: set[str]
    classified: list[Classified]
    warnings: list[str] = field(default_factory=list)


def gather(adb: AdbRunner, profile_name: str | None = None) -> Context:
    info = get_info(adb)
    generic = load_generic()
    profile = find_profile(profile_name) if profile_name else select_profile(adb.getprop)
    rules = build_rules(generic, profile)
    warnings = list(generic.warnings) + (list(profile.warnings) if profile else [])
    for name, pattern in protected_conflicts(rules):
        warnings.append(f"{name} está no perfil, mas é protegido por '{pattern}' e será ignorado.")
    packages = list_packages(adb)
    if not packages:
        raise ValueError("Não consegui listar os pacotes da TV (`pm list packages` veio vazio).")
    homes = home_packages(adb)
    return Context(adb, info, profile, rules, packages, homes,
                   classify(packages, rules, homes), warnings)


def diagnose(info: DeviceInfo) -> list[str]:
    tips = []
    if info.mem_total_kb and info.mem_total_kb < 1.6 * 1024**2:
        tips.append("RAM baixa (menos de 1,6 GB): desativar apps em segundo plano ajuda bastante.")
    if info.mem_available_kb is not None and info.mem_total_kb:
        if info.mem_available_kb / info.mem_total_kb < 0.25:
            tips.append("Menos de 25% da RAM livre agora: veja `atvleve top`.")
    if info.data_free_kb is not None and info.data_free_kb < 1024**2:
        tips.append("Menos de 1 GB livre no armazenamento: a TV fica lenta e falha em "
                    "atualizações. Limpe o cache de apps grandes ou desinstale apps de usuário.")
    return tips


# -- full report (web panel) ---------------------------------------------------------


@dataclass
class Recommendation:
    key: str
    title: str
    text: str
    impact: str  # "alto" / "médio" / "baixo"
    command: str = ""
    items: list[str] = field(default_factory=list)


@dataclass
class HeavyProc:
    name: str
    kb: int
    category: Category
    share: float  # 0..1 of the biggest process


@dataclass
class Report:
    host: str
    ctx: Context
    health: Health
    plan: Plan
    metric: str
    heavy: list[HeavyProc]
    counts: dict[Category, int]
    recommendations: list[Recommendation]
    tips: list[str]

    @property
    def info(self) -> DeviceInfo:
        return self.ctx.info

    @property
    def mem_free_ratio(self) -> float | None:
        i = self.info
        if i.mem_total_kb and i.mem_available_kb is not None:
            return i.mem_available_kb / i.mem_total_kb
        return None

    def by_category(self, cat: Category) -> list[Classified]:
        return [c for c in self.ctx.classified if c.category is cat]


def _mb(kb: int) -> str:
    return f"{kb / 1024:.0f} MB"


def build_report(adb: AdbRunner, host: str, top_n: int = 12) -> Report:
    """Collect everything the panel shows. Only reads from the TV."""
    ctx = gather(adb)
    health = check_health(adb)
    settings = animation_changes(adb, DEFAULT_SCALE)
    plan = build_plan(ctx.classified, ctx.rules, ctx.home_pkgs, ctx.info.home, settings=settings)
    metric, procs = top_memory(adb)

    cat_of = {c.name: c.category for c in ctx.classified}
    top = procs[:top_n]
    biggest = top[0].kb if top else 1
    heavy = []
    for p in top:
        base = p.name.split(":", 1)[0]
        cat = cat_of.get(base, Category.PROTECTED)  # 'system' etc. are not packages
        if ctx.rules.protected_match(base) or base in ctx.home_pkgs:
            cat = Category.PROTECTED
        heavy.append(HeavyProc(p.name, p.kb, cat, p.kb / biggest))

    counts = {c: 0 for c in Category}
    for c in ctx.classified:
        counts[c.category] += 1

    recs = _recommendations(ctx, plan, procs, host)
    return Report(host, ctx, health, plan, metric, heavy, counts, recs, diagnose(ctx.info))


def _recommendations(
    ctx: Context, plan: Plan, procs: list[ProcMem], host: str
) -> list[Recommendation]:
    ip = f"--ip {host}"
    recs: list[Recommendation] = []
    safe = [i.name for i in plan.items]
    if safe:
        recs.append(Recommendation(
            "safe", f"Desativar {len(safe)} app(s) já testados neste modelo",
            "Estes apps foram validados em uma TV igual à sua. Desativar libera memória e "
            "processamento, e dá para desfazer com um comando.",
            "alto", f"atvleve apply {ip}", safe))
    if plan.pending_confirm:
        recs.append(Recommendation(
            "confirm", f"Revisar {len(plan.pending_confirm)} app(s) sugeridos",
            "Provavelmente podem sair, mas dependem do seu uso (ex.: assistente de voz). "
            "O apply pergunta um por um.",
            "médio", f"atvleve apply {ip}", [c.name for c in plan.pending_confirm]))
    anim = plan.settings_needed
    if anim:
        recs.append(Recommendation(
            "tune", "Acelerar as animações",
            "As animações do sistema estão no padrão. Em 0.5 os menus respondem mais rápido "
            "e o processador trabalha menos.",
            "médio", f"atvleve tune {ip}",
            [f"{s.key}: {s.current or 'padrão'} → {s.value}" for s in anim]))

    home_pkg = (ctx.info.home or "").split("/", 1)[0]
    if home_pkg == THEME_LAUNCHER:
        recs.append(Recommendation(
            "theme", "Escolher a cor de fundo da tela inicial",
            "Sua tela inicial é o launcher leve do cleanyourtv. Dá para trocar a cor de fundo; "
            "o tema anterior vai para o backup.",
            "baixo", f"atvleve theme {ip} --color '#170A2B'"))
    if home_pkg in STOCK_LAUNCHERS:
        used = sum(p.kb for p in procs if p.name.split(":", 1)[0] == home_pkg)
        heavy_txt = f" Agora ele ocupa {_mb(used)} de memória." if used else ""
        recs.append(Recommendation(
            "launcher", "Trocar a tela inicial por uma leve",
            "A tela inicial original é o app que mais pesa nestas TVs e é de onde vêm os "
            f"anúncios.{heavy_txt} Instale o FLauncher pela Play Store da TV; o ATV Leve define "
            "ele como padrão e só então desativa a original (dá para desfazer).",
            "alto", f"atvleve launcher {ip}"))

    cat_of = {c.name: c.category for c in ctx.classified}
    unknown_heavy = []
    for p in procs:
        base = p.name.split(":", 1)[0]
        if cat_of.get(base) is Category.UNKNOWN and p.kb >= 20 * 1024:
            unknown_heavy.append(f"{p.name} — {_mb(p.kb)}")
    if unknown_heavy:
        recs.append(Recommendation(
            "heavy", "Apps pesados que ninguém validou ainda",
            "Estão consumindo memória agora e não estão em nenhum perfil. Se você não usa, "
            "teste desativar um de cada vez e reinicie a TV.",
            "médio", f"atvleve apply {ip} --include NOME_DO_PACOTE", unknown_heavy[:8]))

    if ctx.info.data_free_kb is not None and ctx.info.data_free_kb < 1024**2:
        recs.append(Recommendation(
            "storage", "Armazenamento quase cheio",
            "Com menos de 1 GB livre a TV fica lenta e as atualizações falham. Limpe o cache "
            "dos apps maiores em Configurações → Apps, ou desinstale apps que não usa.",
            "alto"))

    if not ctx.profile:
        recs.append(Recommendation(
            "profile", "Ainda não há perfil para este modelo",
            "Sem perfil, o ATV Leve não desativa nada sozinho. Gere um perfil-modelo e, depois "
            "de testar, contribua com o projeto: as próximas pessoas com esta TV agradecem.",
            "baixo", f"atvleve profile-export {ip}"))
    return recs


# -- apply / restore from the web panel --------------------------------------------------


@dataclass
class Choice:
    name: str
    category: Category
    reason: str
    description: str
    checked: bool


def apply_choices(ctx: Context, plan: Plan) -> list[Choice]:
    """What the panel offers: 'seguro' pre-checked, 'confirmar' unchecked (opt-in one by one)."""
    desc = ctx.rules.descriptions
    out = [Choice(i.name, i.category, i.reason, desc.get(i.name, ""), True) for i in plan.items]
    out += [Choice(c.name, c.category, c.reason, desc.get(c.name, ""), False)
            for c in plan.pending_confirm]
    return out


def review(adb: AdbRunner) -> tuple[Context, Plan, list[Choice]]:
    ctx = gather(adb)
    settings = animation_changes(adb, DEFAULT_SCALE)
    plan = build_plan(ctx.classified, ctx.rules, ctx.home_pkgs, ctx.info.home, settings=settings)
    return ctx, plan, apply_choices(ctx, plan)


@dataclass
class ApplyOutcome:
    backup: Backup
    plan: Plan
    result: ApplyResult
    log: list[str]
    cache_freed_kb: int | None = None


def run_apply(
    adb: AdbRunner, selected: set[str], tune: bool, trim: bool = False,
    batch_size: int = DEFAULT_BATCH,
) -> ApplyOutcome:
    """Apply only what the user ticked, recomputing everything from the TV.

    The browser only sends names; they are intersected with a fresh plan, so a
    protected or unknown package can never get in through the form.
    """
    ctx = gather(adb)
    settings = animation_changes(adb, DEFAULT_SCALE) if tune else []
    plan = build_plan(ctx.classified, ctx.rules, ctx.home_pkgs, ctx.info.home,
                      confirmed=selected, settings=settings)
    plan.items = [i for i in plan.items if i.name in selected]
    plan.pending_confirm = []
    if plan.empty and not trim:
        raise ValueError("Nada selecionado para aplicar (ou já estava aplicado).")
    backup = Backup.create(adb, ctx.info, ctx.packages,
                           [(s.namespace, s.key) for s in plan.settings],
                           profile_name=ctx.profile.name if ctx.profile else "", kind="apply-web")
    log: list[str] = []
    result = apply_plan(adb, plan, backup, batch_size=batch_size, log=log.append)
    freed = None
    if trim and not result.aborted:
        before, after = trim_caches(adb)
        if before is not None and after is not None:
            freed = max(0, after - before)
    return ApplyOutcome(backup, plan, result, log, freed)


@dataclass
class RestoreOutcome:
    backup: Backup
    report: RestoreReport
    health: Health
    log: list[str]


def run_restore(adb: AdbRunner) -> RestoreOutcome:
    info = get_info(adb)
    backup = Backup.load(latest_backup(info.backup_id, with_changes=True))
    log: list[str] = []
    report = restore(adb, backup, log=log.append)
    return RestoreOutcome(backup, report, check_health(adb), log)


@dataclass
class LauncherOptions:
    current: str | None
    installed: list[HomeCandidate]
    alternatives: list[HomeCandidate]


def launcher_options(adb: AdbRunner) -> LauncherOptions:
    current = get_info(adb).home
    return LauncherOptions(current, candidates(adb), alternatives(adb))


@dataclass
class LauncherOutcome:
    backup: Backup
    result: SwapResult
    log: list[str]
    mem_before_kb: int | None
    mem_after_kb: int | None
    health: Health


def run_launcher_swap(adb: AdbRunner, package: str, settle: float = 5.0,
                      log=None, input_key: bool = False) -> LauncherOutcome:
    """Swap the home screen, measuring free RAM before and after."""
    info = get_info(adb)
    lines: list[str] = []

    def emit(msg):
        lines.append(msg)
        if log:
            log(msg)

    backup, result = swap(adb, package, info, list_packages(adb), log=emit, input_key=input_key)
    if result.ok and settle:
        time.sleep(settle)  # let the killed launcher's memory come back
    after = get_info(adb).mem_available_kb
    return LauncherOutcome(backup, result, lines, info.mem_available_kb, after,
                           check_health(adb))


@dataclass
class ThemeOptions:
    available: bool  # the cleanyourtv launcher is the home screen
    current: str
    presets: dict[str, str]


def theme_options(adb: AdbRunner) -> ThemeOptions:
    home = (get_info(adb).home or "").split("/", 1)[0]
    available = home == THEME_LAUNCHER
    return ThemeOptions(available, current_color(adb) if available else "", PRESETS)


@dataclass
class ThemeOutcome:
    backup: Backup
    color: str
    previous: str
    warning: str = ""


def run_theme(adb: AdbRunner, color: str, settle: float = 1.0) -> ThemeOutcome:
    opts = theme_options(adb)
    if not opts.available:
        raise ValueError("A cor de fundo só pode ser escolhida com o launcher "
                         f"{THEME_LAUNCHER} como tela inicial.")
    info = get_info(adb)
    backup = Backup.create(adb, info, list_packages(adb), [], kind="theme")
    applied = set_background(adb, color, backup, settle=settle)
    backup.set_status("completed")
    warning = ("Cor clara: o texto do launcher é branco e pode ficar difícil de ler."
               if too_light(applied) else "")
    return ThemeOutcome(backup, applied, opts.current, warning)
