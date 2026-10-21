"""Local web panel: type the TV's IP and port, see what can be improved and apply it.

Reading is free; changing the TV goes through a review page, explicit opt-in per
'confirmar' package, a mandatory backup, batch health checks and an undo button —
the same safety net as `atvleve apply`.
"""

from __future__ import annotations

import re
import secrets
import threading
from urllib.parse import urlsplit

from flask import Flask, abort, render_template, request

from ..adb import AdbError, AdbRunner
from ..analysis import (
    build_report,
    connect_host,
    launcher_options,
    review,
    run_apply,
    run_launcher_swap,
    run_restore,
    run_theme,
    theme_options,
    validate_host,
)
from ..backup import BackupError
from ..device import fmt_kb
from ..launcher import INPUT_KEY_LAUNCHER, LauncherError
from ..packages import Category
from ..profiles import ProfileError
from ..safety import SafetyError

CATEGORY_LABEL = {
    Category.PROTECTED: "protegido",
    Category.SAFE: "seguro",
    Category.CONFIRM: "confirmar",
    Category.UNKNOWN: "desconhecido",
}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
ERRORS = (AdbError, ProfileError, BackupError, SafetyError, LauncherError, ValueError)

RICH_MARKUP_RE = re.compile(r"\[/?(?:bold|dim|red|green|yellow|reverse)(?: [a-z]+)*\]")


def plain(message: object) -> str:
    """Texts are shared with the terminal; drop its rich markup for HTML."""
    return RICH_MARKUP_RE.sub("", str(message))


def create_app(runner_factory=AdbRunner, allow_remote: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["RUNNER_FACTORY"] = runner_factory
    app.config["CSRF_TOKEN"] = secrets.token_urlsafe(32)
    app.config["ALLOW_REMOTE"] = allow_remote
    app.config["SETTLE"] = 5.0  # seconds before measuring RAM after a launcher swap
    app.config["THEME_SETTLE"] = 1.0  # wait before re-enabling accessibility after a restart
    busy = threading.Lock()  # one adb operation that changes the TV at a time

    @app.template_filter("kb")
    def _kb(value):
        return fmt_kb(value)

    @app.template_filter("plain")
    def _plain(value):
        return plain(value)

    @app.context_processor
    def _globals():
        return {"Category": Category, "CATEGORY_LABEL": CATEGORY_LABEL,
                "csrf": app.config["CSRF_TOKEN"]}

    @app.before_request
    def _guard():
        # DNS rebinding: a local panel only answers to local host names.
        hostname = request.host.rsplit(":", 1)[0].strip("[]")
        if not app.config["ALLOW_REMOTE"] and hostname not in LOCAL_HOSTS:
            abort(403)
        if request.method != "POST":
            return None
        # CSRF: another site in the browser must not be able to drive the TV.
        if not secrets.compare_digest(request.form.get("csrf", ""), app.config["CSRF_TOKEN"]):
            abort(403)
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if origin and urlsplit(origin).netloc != request.host:
            abort(403)
        return None

    def form_values():
        return {
            "ip": request.values.get("ip", "").strip(),
            "port": request.values.get("port", "").strip() or "5555",
        }

    def connected():
        form = form_values()
        host = validate_host(form["ip"], form["port"])
        adb = app.config["RUNNER_FACTORY"]()
        connect_host(adb, host)
        return form, host, adb

    def failed(exc, form=None, **extra):
        return render_template("index.html", form=form or form_values(), error=plain(exc),
                               **extra), 400

    @app.get("/")
    def index():
        return render_template("index.html", form=form_values())

    @app.post("/analisar")
    def analyze():
        try:
            form, host, adb = connected()
            report = build_report(adb, host)
        except ERRORS as exc:
            return failed(exc)
        return render_template("report.html", r=report, form=form)

    @app.post("/revisar")
    def review_page():
        try:
            form, host, adb = connected()
            ctx, plan, choices = review(adb)
        except ERRORS as exc:
            return failed(exc)
        return render_template("review.html", form=form, host=host, ctx=ctx, plan=plan,
                               choices=choices)

    @app.post("/aplicar")
    def apply():
        if request.form.get("entendi") != "sim":
            return failed("Marque a confirmação antes de aplicar.")
        selected = set(request.form.getlist("pkg"))
        tune = request.form.get("tune") == "sim"
        trim = request.form.get("trim") == "sim"
        if not busy.acquire(blocking=False):
            return failed("Já existe uma alteração em andamento nesta TV. Aguarde terminar.")
        try:
            form, host, adb = connected()
            outcome = run_apply(adb, selected, tune, trim)
        except ERRORS as exc:
            return failed(exc)
        finally:
            busy.release()
        return render_template("result.html", form=form, host=host, mode="apply", o=outcome)

    @app.post("/launcher")
    def launcher_page():
        try:
            form, host, adb = connected()
            opts = launcher_options(adb)
        except ERRORS as exc:
            return failed(exc)
        return render_template("launcher.html", form=form, host=host, opts=opts,
                               input_key_pkg=INPUT_KEY_LAUNCHER)

    @app.post("/trocar-launcher")
    def launcher_swap():
        if request.form.get("entendi") != "sim":
            return failed("Marque a confirmação antes de trocar a tela inicial.")
        package = request.form.get("launcher", "")
        if not busy.acquire(blocking=False):
            return failed("Já existe uma alteração em andamento nesta TV. Aguarde terminar.")
        try:
            form, host, adb = connected()
            # Only a launcher the TV itself reports as an alternative gets through.
            if package not in {c.package for c in launcher_options(adb).alternatives}:
                raise LauncherError("Escolha um dos launchers instalados na TV.")
            input_key = request.form.get("input_key") == "sim" and package == INPUT_KEY_LAUNCHER
            outcome = run_launcher_swap(adb, package, settle=app.config["SETTLE"],
                                        input_key=input_key)
        except ERRORS as exc:
            return failed(exc)
        finally:
            busy.release()
        return render_template("result.html", form=form, host=host, mode="launcher", o=outcome)

    @app.post("/tema")
    def theme_page():
        try:
            form, host, adb = connected()
            opts = theme_options(adb)
        except ERRORS as exc:
            return failed(exc)
        return render_template("theme.html", form=form, host=host, opts=opts)

    @app.post("/aplicar-tema")
    def theme_apply():
        color = request.form.get("custom", "").strip() or request.form.get("color", "")
        if not busy.acquire(blocking=False):
            return failed("Já existe uma alteração em andamento nesta TV. Aguarde terminar.")
        try:
            form, host, adb = connected()
            outcome = run_theme(adb, color, settle=app.config["THEME_SETTLE"])
            opts = theme_options(adb)
        except ERRORS as exc:
            return failed(exc)
        finally:
            busy.release()
        return render_template("theme.html", form=form, host=host, opts=opts, o=outcome)

    @app.post("/desfazer")
    def undo():
        if not busy.acquire(blocking=False):
            return failed("Já existe uma alteração em andamento nesta TV. Aguarde terminar.")
        try:
            form, host, adb = connected()
            outcome = run_restore(adb)
        except ERRORS as exc:
            return failed(exc)
        finally:
            busy.release()
        return render_template("result.html", form=form, host=host, mode="restore", o=outcome)

    @app.post("/parear")
    def pair():
        form = form_values()
        pair_host = request.form.get("pair_host", "").strip()
        code = request.form.get("code", "").strip()
        try:
            host = validate_host(pair_host)
            if not code.isdigit() or not 4 <= len(code) <= 10:
                raise ValueError("Código de pareamento inválido (só números, como aparece na TV).")
            adb = app.config["RUNNER_FACTORY"]()
            result = adb.pair(host, code)
            if "success" not in result.output.lower():
                raise ValueError(f"Pareamento falhou: {result.output or 'sem resposta'}")
        except (AdbError, ValueError) as exc:
            return failed(exc, form, show_pair=True)
        return render_template(
            "index.html", form=form,
            notice="Pareado. Agora informe a porta de conexão mostrada em "
                   "\"Depuração sem fio\" (é diferente da porta de pareamento).",
        )

    return app


def run(host: str = "127.0.0.1", port: int = 8765, debug: bool = False,
        allow_remote: bool | None = None) -> None:
    # allow_remote=False keeps the localhost-only guard while listening on another address
    # (inside Docker the panel binds 0.0.0.0 but the browser reaches it as localhost).
    if allow_remote is None:
        allow_remote = host not in LOCAL_HOSTS
    create_app(allow_remote=allow_remote).run(host=host, port=port, debug=debug, threaded=True)
