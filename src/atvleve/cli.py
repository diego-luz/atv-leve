"""Command line interface (user-facing texts in pt-BR)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from . import __version__
from .adb import AdbDeviceError, AdbError, AdbRunner
from .analysis import (
    Context,
    connect_host,
    diagnose,
    gather,
    launcher_options,
    run_launcher_swap,
    run_theme,
    theme_options,
)
from .backup import Backup, BackupError, latest_backup, list_backups, restore
from .device import DeviceInfo, fmt_kb, get_info, home_packages, top_memory
from .launcher import (
    A11Y_SETTINGS,
    INPUT_KEY_LAUNCHER,
    LauncherError,
    enable_input_key,
    install_apk,
)
from .packages import ENABLED, Category, list_packages
from .planner import DEFAULT_BATCH, Plan, apply_plan, build_plan
from .profiles import ProfileError, build_rules, load_generic
from .safety import SafetyError, check_health
from .tune import ANIMATION_KEYS, DEFAULT_SCALE, animation_changes, apply_settings, trim_caches

console = Console()

CATEGORY_STYLE = {
    Category.PROTECTED: "green",
    Category.SAFE: "cyan",
    Category.CONFIRM: "yellow",
    Category.UNKNOWN: "dim",
}

ENABLE_ADB_HELP = """\
[bold]Como ativar o ADB na TV[/bold]
 1. Configurações → Sistema (ou Preferências do dispositivo) → Sobre.
 2. Aperte OK 7 vezes seguidas em [bold]Build do Android TV OS[/bold] até aparecer
    "Você agora é um desenvolvedor".
 3. Volte e abra [bold]Opções do desenvolvedor[/bold].
 4. Ative [bold]Depuração USB[/bold] e, se existir, [bold]Depuração pela rede[/bold] /
    [bold]Depuração por ADB[/bold].
 5. Descubra o IP da TV em Configurações → Rede e Internet → (sua rede).
 6. Rode: [bold]atvleve doctor --ip IP_DA_TV[/bold] e aceite o pedido que aparece na TV
    (marque "Sempre permitir deste computador").

[bold]Se a TV só tiver "Depuração sem fio" (Wireless debugging)[/bold] — comum no Google TV 14:
 - Abra "Depuração sem fio" → "Parear dispositivo com código".
 - Rode: [bold]atvleve doctor --pair IP:PORTA_DE_PAREAMENTO --code CODIGO[/bold]
 - Depois conecte usando o IP:PORTA mostrado na tela principal de "Depuração sem fio":
   [bold]atvleve doctor --ip IP:PORTA[/bold]
"""


class UserError(Exception):
    pass


# -- connection & context --------------------------------------------------------------


def make_runner(args) -> AdbRunner:
    return AdbRunner(verbose=args.verbose, log=console.print)


def connect(args, adb: AdbRunner | None = None) -> AdbRunner:
    adb = adb or make_runner(args)
    adb.find_adb()
    ip = args.ip or os.environ.get("ATVLEVE_IP")
    if ip:
        return connect_host(adb, ip)
    elif args.serial:
        adb.serial = args.serial
    else:
        devices = adb.devices()
        ready = [s for s, state in devices if state == "device"]
        if len(ready) == 1:
            adb.serial = ready[0]
        elif not devices:
            raise AdbDeviceError(
                "Nenhuma TV conectada. Use --ip IP_DA_TV (ex.: --ip 192.168.0.20)."
            )
        elif not ready:
            states = ", ".join(f"{s} ({st})" for s, st in devices)
            raise AdbDeviceError(
                f"Aparelho encontrado, mas não está pronto: {states}. "
                "Se estiver 'unauthorized', aceite o pedido na tela da TV."
            )
        else:
            raise AdbDeviceError(
                "Há mais de uma TV/aparelho conectado. Escolha com --serial: " + ", ".join(ready)
            )
    state = adb.get_state()
    if state != "device":
        raise AdbDeviceError(
            f"A TV {adb.serial} está com estado '{state or 'desconhecido'}'. "
            "Aceite o pedido de depuração na tela da TV e tente de novo."
        )
    return adb


def load_context(args, adb: AdbRunner) -> Context:
    ctx = gather(adb, args.profile)
    for w in ctx.warnings:
        console.print(f"[yellow]Aviso:[/yellow] {w}")
    return ctx


def profile_label(ctx: Context) -> str:
    if ctx.profile:
        return f"{ctx.profile.name} ({ctx.profile.brand} {ctx.profile.model})"
    return "nenhum perfil específico (só regras genéricas)"


def restore_hint(args) -> str:
    parts = ["atvleve restore"]
    if args.ip:
        parts.append(f"--ip {args.ip}")
    elif args.serial:
        parts.append(f"--serial {args.serial}")
    return " ".join(parts)


# -- commands ---------------------------------------------------------------------------


def cmd_doctor(args) -> int:
    adb = make_runner(args)
    console.print("[bold]1. Programa adb[/bold]")
    try:
        path = adb.find_adb()
    except AdbError as exc:
        console.print(Panel(str(exc), title="adb não encontrado", border_style="red"))
        return 1
    console.print(f"  [green]✓[/green] {path} — {adb.version()}")

    if args.pair:
        if not args.code:
            raise UserError("Use --code com o código de 6 dígitos mostrado na TV.")
        console.print(f"[bold]Pareando com {args.pair}[/bold]")
        result = adb.pair(args.pair, args.code)
        if "success" not in result.output.lower():
            console.print(f"  [red]✗[/red] {result.output or 'falhou'}")
            console.print(ENABLE_ADB_HELP)
            return 1
        console.print(f"  [green]✓[/green] {result.output}")
        if not args.ip:
            console.print("Agora rode [bold]atvleve doctor --ip IP:PORTA[/bold] (a porta de "
                          "conexão aparece na tela de Depuração sem fio).")
            return 0

    console.print("[bold]2. Conexão com a TV[/bold]")
    try:
        adb = connect(args, adb)
    except AdbError as exc:
        console.print(f"  [red]✗[/red] {exc}")
        console.print(Panel(ENABLE_ADB_HELP, border_style="yellow"))
        return 1
    console.print(f"  [green]✓[/green] conectado em {adb.serial} e autorizado")

    console.print("[bold]3. Saúde da TV[/bold]")
    health = check_health(adb)
    if health.ok:
        console.print(f"  [green]✓[/green] tela inicial: {health.home}")
    else:
        for p in health.problems:
            console.print(f"  [red]✗[/red] {p}")
        return 1
    console.print("\nTudo certo. Próximos passos: [bold]atvleve info[/bold] e "
                  "[bold]atvleve scan[/bold].")
    return 0


def print_info(info: DeviceInfo) -> None:
    table = Table(show_header=False, box=None)
    table.add_column(style="bold")
    table.add_column()
    low_ram = {"true": "sim", "false": "não"}.get(info.low_ram, info.low_ram or "não informado")
    rows = [
        ("Marca", info.brand or "?"),
        ("Fabricante", info.manufacturer or "?"),
        ("Modelo", info.model or "?"),
        ("Aparelho", info.device or "?"),
        ("Android", f"{info.release or '?'} (SDK {info.sdk or '?'})"),
        ("Build", info.build_id or "?"),
        ("Modo pouca RAM", low_ram),
        ("RAM total", fmt_kb(info.mem_total_kb)),
        ("RAM disponível", fmt_kb(info.mem_available_kb)),
        ("Espaço livre /data", f"{fmt_kb(info.data_free_kb)} de {fmt_kb(info.data_total_kb)}"),
        ("Tela inicial", info.home or "não encontrada"),
        ("Serial", info.serialno or info.serial),
    ]
    for k, v in rows:
        table.add_row(k, v)
    console.print(Panel(table, title="Informações da TV"))


def cmd_info(args) -> int:
    adb = connect(args)
    info = get_info(adb)
    print_info(info)
    for tip in diagnose(info):
        console.print(f"[yellow]•[/yellow] {tip}")
    return 0


def cmd_scan(args) -> int:
    adb = connect(args)
    ctx = load_context(args, adb)
    console.print(f"Perfil: [bold]{profile_label(ctx)}[/bold]")
    table = Table(title=f"Pacotes ({len(ctx.classified)})")
    table.add_column("Pacote")
    table.add_column("Tipo")
    table.add_column("Estado")
    table.add_column("Classificação")
    table.add_column("Motivo", style="dim")
    state_pt = {"enabled": "ativo", "disabled": "desativado", "uninstalled": "removido"}
    counts: dict[Category, int] = {c: 0 for c in Category}
    for c in ctx.classified:
        counts[c.category] += 1
        if args.only and c.category.value != args.only:
            continue
        style = CATEGORY_STYLE[c.category]
        table.add_row(
            c.name,
            "sistema" if c.package.system else "usuário",
            state_pt[c.package.state],
            f"[{style}]{c.category.value}[/{style}]",
            c.reason,
        )
    console.print(table)
    console.print(
        "  ".join(f"[{CATEGORY_STYLE[c]}]{c.value}: {n}[/{CATEGORY_STYLE[c]}]"
                  for c, n in counts.items())
    )
    if counts[Category.UNKNOWN]:
        console.print(
            "\n[dim]'desconhecido' nunca é tocado automaticamente. Para mexer em um, use "
            "`atvleve apply --include NOME` (pede confirmação individual).[/dim]"
        )
    return 0


def cmd_top(args) -> int:
    adb = connect(args)
    info = get_info(adb)
    console.print("Lendo uso de memória (pode levar alguns segundos)...")
    metric, procs = top_memory(adb)
    if not procs:
        raise UserError("Não consegui ler `dumpsys meminfo` nesta TV.")
    homes = home_packages(adb)
    rules = build_rules(load_generic(), None)
    table = Table(title=f"Apps que mais usam memória ({metric})")
    table.add_column("#", justify="right")
    table.add_column("Processo")
    table.add_column("Memória", justify="right")
    table.add_column("Protegido?", style="dim")
    for i, p in enumerate(procs[: args.limit], start=1):
        base = p.name.split(":", 1)[0]
        prot = "sim" if rules.protected_match(base) or base in homes else ""
        table.add_row(str(i), p.name, fmt_kb(p.kb), prot)
    console.print(table)
    console.print(f"RAM total: {fmt_kb(info.mem_total_kb)} · disponível: "
                  f"{fmt_kb(info.mem_available_kb)}")
    return 0


def cmd_backup(args) -> int:
    adb = connect(args)
    info = get_info(adb)
    packages = list_packages(adb)
    backup = Backup.create(adb, info, packages, list(ANIMATION_KEYS), kind="manual")
    console.print(f"[green]Backup salvo:[/green] {backup.path}")
    return 0


def _settings_for(args, adb):
    if getattr(args, "no_tune", False):
        return []
    return animation_changes(adb, args.scale)


def print_plan(plan: Plan, uninstall: bool) -> None:
    verb = "Desinstalar (para o usuário 0)" if uninstall else "Desativar"
    if plan.items:
        table = Table(title=f"{verb}: {len(plan.items)} pacote(s)")
        table.add_column("Pacote")
        table.add_column("Classificação")
        table.add_column("Motivo", style="dim")
        for item in plan.items:
            style = CATEGORY_STYLE[item.category]
            table.add_row(item.name, f"[{style}]{item.category.value}[/{style}]", item.reason)
        console.print(table)
    else:
        console.print("Nenhum pacote para desativar.")
    if plan.pending_confirm:
        console.print(f"\n[yellow]Pedirão confirmação no apply ({len(plan.pending_confirm)}):"
                      "[/yellow]")
        for c in plan.pending_confirm:
            console.print(f"  ? {c.name}")
    if plan.settings_needed:
        console.print("\n[bold]Ajustes:[/bold]")
        for s in plan.settings_needed:
            console.print(f"  {s.namespace}/{s.key}: {s.current or '(padrão)'} → {s.value}")
    if plan.already_done:
        console.print(f"\n[dim]Já desativados: {', '.join(plan.already_done)}[/dim]")
    for name, why in plan.refused:
        console.print(f"[red]Recusado:[/red] {name} — {why}")


def _include_list(args) -> list[str]:
    out = []
    for chunk in args.include or []:
        out += [x.strip() for x in chunk.split(",") if x.strip()]
    return out


def cmd_plan(args) -> int:
    adb = connect(args)
    ctx = load_context(args, adb)
    console.print(f"Perfil: [bold]{profile_label(ctx)}[/bold]")
    plan = build_plan(
        ctx.classified, ctx.rules, ctx.home_pkgs, ctx.info.home,
        include=_include_list(args), uninstall=args.uninstall,
        settings=_settings_for(args, adb),
    )
    print_plan(plan, args.uninstall)
    console.print("\n[dim]Isto é só uma simulação: nada foi alterado na TV. "
                  "Para executar: atvleve apply[/dim]")
    return 0


def cmd_apply(args) -> int:
    adb = connect(args)
    ctx = load_context(args, adb)
    console.print(f"Perfil: [bold]{profile_label(ctx)}[/bold]")
    include = _include_list(args)
    settings = _settings_for(args, adb)
    plan = build_plan(ctx.classified, ctx.rules, ctx.home_pkgs, ctx.info.home,
                      include=include, uninstall=args.uninstall, settings=settings)

    confirmed: list[str] = []
    if plan.pending_confirm and not args.yes:
        console.print("\n[bold]Pacotes que o perfil sugere, mas pedem confirmação:[/bold]")
        for c in plan.pending_confirm:
            if Confirm.ask(f"  Desativar [yellow]{c.name}[/yellow]?", default=False):
                confirmed.append(c.name)
    unknown = [i.name for i in plan.items if i.category is Category.UNKNOWN]
    if unknown and not args.yes:
        console.print("\n[bold red]Pacotes desconhecidos selecionados manualmente:[/bold red]")
        for name in unknown:
            if not Confirm.ask(f"  Tem certeza que quer mexer em [red]{name}[/red]?",
                               default=False):
                include.remove(name)
    plan = build_plan(ctx.classified, ctx.rules, ctx.home_pkgs, ctx.info.home,
                      include=include, confirmed=confirmed, uninstall=args.uninstall,
                      settings=settings)
    plan.pending_confirm = []
    console.print()
    print_plan(plan, args.uninstall)
    if plan.empty:
        console.print("\nNada a fazer.")
        return 0
    if args.uninstall:
        console.print(Panel(
            "[bold]--uninstall[/bold] remove o app para o usuário. Ele pode voltar com "
            "`atvleve restore`, mas volta na [bold]versão de fábrica[/bold] (as atualizações "
            "da Play Store se perdem).", border_style="yellow"))
    if not args.yes and not Confirm.ask("\nAplicar este plano?", default=False):
        console.print("Cancelado. Nada foi alterado.")
        return 0

    backup = Backup.create(adb, ctx.info, ctx.packages,
                           [(s.namespace, s.key) for s in plan.settings],
                           profile_name=ctx.profile.name if ctx.profile else "", kind="apply")
    console.print(f"[green]Backup salvo:[/green] {backup.path}\n")

    result = apply_plan(adb, plan, backup, batch_size=args.batch_size, log=console.print)
    hint = restore_hint(args)
    if result.aborted:
        console.print(Panel(
            f"[bold red]Parado:[/bold red] {result.aborted}\n\n"
            f"Para desfazer tudo o que foi feito: [bold]{hint}[/bold]\n"
            "Último recurso: o reset de fábrica da TV restaura todos os apps.",
            border_style="red"))
        return 2
    summary = f"{len(result.done)} pacote(s) alterado(s)."
    if result.failed:
        summary += f" {len(result.failed)} não puderam ser alterados (a TV recusou)."
    console.print(Panel(
        f"[green]{summary}[/green]\n\n"
        "Agora [bold]reinicie a TV[/bold] e teste: tela inicial, controle remoto, entradas "
        "HDMI, Wi-Fi, apps que você usa.\n\n"
        f"Se algo der errado: [bold reverse] {hint} [/bold reverse]",
        title="Concluído", border_style="green"))
    return 0 if result.ok else 1


def cmd_tune(args) -> int:
    adb = connect(args)
    if args.trim_cache:
        before, after = trim_caches(adb)
        if before is not None and after is not None:
            console.print(f"Cache limpo: {fmt_kb(max(0, after - before))} liberados.")
    changes = animation_changes(adb, args.scale)
    needed = [c for c in changes if c.needed]
    if not needed:
        console.print(f"As animações já estão em {args.scale}. Nada a fazer.")
        return 0
    for c in needed:
        console.print(f"  {c.namespace}/{c.key}: {c.current or '(padrão)'} → {c.value}")
    if not args.yes and not Confirm.ask("Aplicar?", default=True):
        return 0
    info = get_info(adb)
    backup = Backup.create(adb, info, list_packages(adb), list(ANIMATION_KEYS), kind="tune")
    console.print(f"[green]Backup salvo:[/green] {backup.path}")
    failed = apply_settings(adb, changes, backup, log=console.print)
    backup.set_status("completed")
    console.print(f"Para voltar ao que era: [bold]{restore_hint(args)}[/bold]")
    return 1 if failed else 0


def cmd_restore(args) -> int:
    if args.list:
        paths = list_backups()
        if not paths:
            console.print("Nenhum backup encontrado.")
        for p in paths:
            try:
                data = Backup.load(p).data
            except BackupError:
                continue
            n = len([c for c in data.get("changes", []) if c.get("ok")])
            console.print(f"{p}  [dim]{data.get('kind')} · {data.get('status')} · "
                          f"{n} alteração(ões)[/dim]")
        return 0
    adb = connect(args)
    if args.file:
        paths = [Path(args.file)]
    else:
        device_id = get_info(adb).backup_id
        if args.all:
            # Newest first, so each step goes one change further back in time.
            paths = [p for p in reversed(list_backups(device_id))
                     if Backup.load(p).data.get("changes")
                     or Backup.load(p).data.get("settings_changed")]
            if not paths:
                raise BackupError(f"Nenhum backup com alterações para esta TV ({device_id}).")
        else:
            paths = [latest_backup(device_id, with_changes=True)]
    backups = [Backup.load(p) for p in paths]
    for b in backups:
        console.print(f"Restaurar a partir de [bold]{b.path}[/bold] "
                      f"({b.data.get('created')}, {b.data.get('kind')})")
    if not args.yes and not Confirm.ask("Continuar?", default=True):
        return 0
    failed: list[str] = []
    for b in backups:
        report = restore(adb, b, log=console.print)
        failed += report.failed
        console.print(f"{b.path.name}: {len(report.done)} item(ns) restaurado(s), "
                      f"{report.unchanged} já estavam como no backup.")
    if failed:
        console.print(f"[red]{len(failed)} falharam:[/red]")
        for f in failed:
            console.print(f"  {f}")
    health = check_health(adb)
    if not health.ok:
        console.print("[red]Atenção:[/red] " + "; ".join(health.problems))
    console.print("Reinicie a TV para concluir.")
    return 0 if not failed and health.ok else 1


def cmd_launcher(args) -> int:
    adb = connect(args)
    if args.install:
        console.print(f"Instalando [bold]{args.install}[/bold] na TV (só instala; a tela "
                      "inicial não muda)…")
        install_apk(adb, args.install)
        console.print("[green]Instalado.[/green]")
    opts = launcher_options(adb)
    console.print(f"Tela inicial atual: [bold]{opts.current or 'nenhuma'}[/bold]")
    for c in opts.installed:
        tag = "original" if c.stock else "alternativa"
        console.print(f"  {c.label}  [dim]{c.component} · {tag}[/dim]")
    if args.input_key and not args.set:
        if (opts.current or "").split("/", 1)[0] != INPUT_KEY_LAUNCHER:
            raise UserError(f"--input-key precisa do launcher {INPUT_KEY_LAUNCHER} como tela "
                            "inicial (ou use junto com --set).")
        backup = Backup.create(adb, get_info(adb), list_packages(adb), A11Y_SETTINGS,
                               kind="input-key")
        ok = enable_input_key(adb, backup)
        backup.set_status("completed")
        console.print(f"[green]Backup salvo:[/green] {backup.path}")
        console.print("Botão Input ligado ao seletor de entradas." if ok else
                      "[red]Não consegui ativar o serviço do botão Input.[/red]")
        return 0 if ok else 1
    if not args.set:
        if not opts.alternatives:
            console.print("Nenhum launcher alternativo instalado. Instale um pela Play Store "
                          "da TV (FLauncher ou Projectivy Launcher), abra uma vez e rode de "
                          "novo.")
        else:
            names = ", ".join(c.package for c in opts.alternatives)
            console.print(f"Para trocar: [bold]atvleve launcher --set PACOTE[/bold] ({names})")
        return 0
    console.print(
        f"Vou conferir que [bold]{args.set}[/bold] abre, defini-lo como tela inicial e "
        "desativar o assistente de configuração e o launcher original (nunca desinstalar). "
        "Cada passo é conferido; se o Home for para outro lugar, tudo volta sozinho."
    )
    if not args.yes and not Confirm.ask("Continuar?", default=False):
        return 0
    out = run_launcher_swap(adb, args.set, log=console.print, input_key=args.input_key)
    console.print(f"[green]Backup salvo:[/green] {out.backup.path}")
    if not out.result.ok:
        console.print(f"[red]Não troquei a tela inicial:[/red] {out.result.message}")
        return 1
    if out.mem_before_kb and out.mem_after_kb:
        diff = out.mem_after_kb - out.mem_before_kb
        console.print(f"RAM livre: {fmt_kb(out.mem_before_kb)} → {fmt_kb(out.mem_after_kb)} "
                      f"({'+' if diff >= 0 else '−'}{fmt_kb(abs(diff))})")
    if not out.health.ok:
        console.print("[red]Atenção:[/red] " + "; ".join(out.health.problems))
    console.print(f"Reinicie a TV e confira. Para voltar: [bold]{restore_hint(args)}[/bold]")
    return 0 if out.health.ok else 1


def cmd_theme(args) -> int:
    adb = connect(args)
    opts = theme_options(adb)
    if not opts.available:
        raise UserError("O tema só funciona com o launcher com.gathin.tvhome como tela inicial "
                        "(veja `atvleve launcher`).")
    if not args.color:
        console.print(f"Cor de fundo atual: [bold]{opts.current}[/bold]")
        for hex_, name in opts.presets.items():
            console.print(f"  [on {hex_}]    [/on {hex_}] {hex_}  {name}")
        console.print("Para trocar: [bold]atvleve theme --color '#170A2B'[/bold]")
        return 0
    out = run_theme(adb, args.color)
    console.print(f"Cor de fundo: {out.previous} → [bold]{out.color}[/bold]")
    if out.warning:
        console.print(f"[yellow]{out.warning}[/yellow]")
    console.print(f"[green]Backup salvo:[/green] {out.backup.path}")
    console.print(f"Para voltar: [bold]{restore_hint(args)}[/bold]")
    return 0


def build_export(ctx: Context) -> dict:
    info = ctx.info
    enabled = sorted(p.name for p in ctx.packages.values()
                     if p.system and p.state == ENABLED)
    by_cat: dict[str, list[str]] = {c.value: [] for c in Category}
    for c in ctx.classified:
        by_cat[c.category.value].append(c.name)
    return {
        "brand": info.brand,
        "model": info.model,
        "match": {"ro.product.model": [info.model] if info.model else []},
        "android_release": info.release,
        "protected": [],
        "safe_to_disable": [],
        "confirm_first": [],
        "notes": (
            "Perfil gerado por `atvleve profile-export`. Mova nomes de 'inventory' para "
            "safe_to_disable só depois de testar na TV real (desativar, reiniciar, usar)."
        ),
        "device_info": {
            "manufacturer": info.manufacturer,
            "device": info.device,
            "sdk": info.sdk,
            "build_id": info.build_id,
            "low_ram": info.low_ram,
            "mem_total": fmt_kb(info.mem_total_kb),
            "data_total": fmt_kb(info.data_total_kb),
            "home": info.home,
            "atvleve": __version__,
        },
        "inventory": {
            "system_enabled": enabled,
            "disabled": sorted(p.name for p in ctx.packages.values() if p.state != ENABLED),
            "user_installed": sorted(p.name for p in ctx.packages.values() if not p.system),
            "classification": by_cat,
        },
    }


def cmd_profile_export(args) -> int:
    adb = connect(args)
    ctx = load_context(args, adb)
    data = build_export(ctx)
    slug = "-".join(x for x in (ctx.info.brand, ctx.info.model) if x).lower() or "tv"
    slug = "".join(ch if ch.isalnum() else "-" for ch in slug).strip("-")
    out = Path(args.output or f"perfil-{slug}.yaml")
    if out.exists() and not args.force:
        raise UserError(f"{out} já existe. Use --force para sobrescrever ou --output.")
    text = (
        "# Perfil gerado pelo ATV Leve. Revise antes de contribuir: docs/adicionar-perfil.md\n"
        "# Este arquivo NÃO contém número de série nem dados pessoais.\n"
        + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    )
    out.write_text(text, encoding="utf-8")
    console.print(f"[green]Perfil-modelo salvo em[/green] {out}")
    return 0


def cmd_web(args) -> int:
    try:
        from .web.app import run
    except ImportError:
        raise UserError("O painel web precisa do Flask: pip install -e \".[web]\"") from None
    if args.host not in ("127.0.0.1", "localhost"):
        console.print("[bold red]Atenção:[/bold red] o painel ficará acessível por outros "
                      "aparelhos da rede, sem senha, e ele pode alterar a TV (botão Aplicar).")
    console.print(f"Abra no navegador: [bold]http://{args.host}:{args.port}[/bold]  "
                  "(Ctrl+C para sair)")
    run(host=args.host, port=args.port)
    return 0


# -- argument parsing -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ip", help="IP da TV (porta 5555 se omitida), ex.: 192.168.0.20")
    common.add_argument("--serial", help="serial do adb (veja `adb devices`)")
    common.add_argument("--profile", help="nome do perfil em profiles/ ou caminho de um .yaml")
    common.add_argument("--yes", "-y", action="store_true",
                        help="não perguntar nada (uso avançado; 'confirmar' fica de fora)")
    common.add_argument("--verbose", "-v", action="store_true", help="mostra os comandos adb")

    parser = argparse.ArgumentParser(
        prog="atvleve",
        description="Deixa TVs Android TV / Google TV mais leves, via ADB, sem root.",
    )
    parser.add_argument("--version", action="version", version=f"atvleve {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMANDO")

    def add(name, func, help_text):
        p = sub.add_parser(name, parents=[common], help=help_text, description=help_text)
        p.set_defaults(func=func)
        return p

    p = add("doctor", cmd_doctor, "verifica adb, conexão e autorização")
    p.add_argument("--pair", metavar="IP:PORTA", help="parear via Depuração sem fio")
    p.add_argument("--code", help="código de pareamento mostrado na TV")
    add("info", cmd_info, "mostra informações e diagnóstico da TV")
    p = add("scan", cmd_scan, "lista e classifica os pacotes")
    p.add_argument("--only", choices=[c.value for c in Category], help="filtra por classificação")
    p = add("top", cmd_top, "apps que mais consomem memória")
    p.add_argument("--limit", type=int, default=15)
    add("backup", cmd_backup, "salva o estado atual da TV")

    def plan_opts(p):
        p.add_argument("--uninstall", action="store_true",
                       help="desinstalar para o usuário em vez de desativar")
        p.add_argument("--include", action="append", metavar="PACOTE[,PACOTE]",
                       help="inclui pacotes 'confirmar' ou 'desconhecido' manualmente")
        p.add_argument("--no-tune", action="store_true", help="não mexer nas animações")
        p.add_argument("--scale", default=DEFAULT_SCALE, choices=["0", "0.5", "1"],
                       help="escala das animações (padrão 0.5)")

    plan_opts(add("plan", cmd_plan, "mostra o que seria feito, sem alterar nada"))
    p = add("apply", cmd_apply, "faz backup e executa o plano, com confirmação")
    plan_opts(p)
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH,
                   help=f"pacotes por lote entre checagens de saúde (padrão {DEFAULT_BATCH})")
    p = add("tune", cmd_tune, "aplica só os ajustes de desempenho (animações)")
    p.add_argument("--scale", default=DEFAULT_SCALE, choices=["0", "0.5", "1"])
    p.add_argument("--trim-cache", action="store_true",
                   help="também limpa o cache de todos os apps (libera espaço)")
    p = add("restore", cmd_restore, "restaura o último backup (ou um escolhido)")
    p.add_argument("--file", help="arquivo de backup específico")
    p.add_argument("--list", action="store_true", help="lista os backups existentes")
    p.add_argument("--all", action="store_true",
                   help="desfaz todos os apply/tune desta TV, do mais novo ao mais antigo")
    p = add("launcher", cmd_launcher, "mostra ou troca a tela inicial (launcher)")
    p.add_argument("--set", metavar="PACOTE",
                   help="pacote do novo launcher, ex.: me.efesser.flauncher")
    p.add_argument("--input-key", action="store_true",
                   help="com.gathin.tvhome: faz o botão Input do controle abrir as entradas")
    p.add_argument("--install", metavar="ARQUIVO.apk",
                   help="instala um APK de launcher que você já tem (nada é baixado)")
    p = add("theme", cmd_theme, "cor de fundo do launcher com.gathin.tvhome")
    p.add_argument("--color", metavar="#RRGGBB", help="nova cor de fundo, ex.: '#170A2B'")
    p = add("profile-export", cmd_profile_export, "gera um perfil-modelo a partir da TV")
    p.add_argument("--output", "-o", help="arquivo de saída")
    p.add_argument("--force", action="store_true", help="sobrescreve o arquivo de saída")
    p = add("web", cmd_web, "abre o painel no navegador")
    p.add_argument("--host", default="127.0.0.1", help="endereço do painel (padrão 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765, help="porta do painel (padrão 8765)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (AdbError, ProfileError, BackupError, SafetyError, LauncherError, UserError,
            ValueError) as exc:
        console.print(f"[bold red]Erro:[/bold red] {exc}")
        return 1
    except KeyboardInterrupt:
        console.print("\nInterrompido. Se um apply estava em andamento, rode "
                      f"[bold]{restore_hint(args)}[/bold].")
        return 130


if __name__ == "__main__":
    sys.exit(main())
