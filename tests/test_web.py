import pytest

from atvleve.analysis import validate_host
from fake_tv import FakeTv

flask = pytest.importorskip("flask")
from atvleve.web.app import create_app  # noqa: E402

MUTATING = ("disable-user", "pm enable", "uninstall", "install-existing", "settings put",
            "settings delete")


@pytest.fixture
def tv():
    return FakeTv()


@pytest.fixture
def app(env, tv):
    app = create_app(runner_factory=lambda: tv)
    app.testing = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def post(client, app):
    def _post(url, data=None, **kw):
        data = {"csrf": app.config["CSRF_TOKEN"], **(data or {})}
        return client.post(url, data=data, **kw)
    return _post


def state(tv):
    return {k: v[1] for k, v in tv.packages.items()}


def test_validate_host():
    assert validate_host("192.168.0.20", "") == "192.168.0.20:5555"
    assert validate_host("192.168.0.20", "37001") == "192.168.0.20:37001"
    assert validate_host("192.168.0.20:40000") == "192.168.0.20:40000"
    for bad in [("", "5555"), ("1.2.3.4;reboot", ""), ("1.2.3.4", "abc"), ("1.2.3.4", "70000")]:
        with pytest.raises(ValueError):
            validate_host(*bad)


def test_index(client):
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "IP da TV" in html and 'value="5555"' in html


def test_report_is_read_only_and_shows_improvements(post, tv):
    before = {k: v[1] for k, v in tv.packages.items()}
    res = post("/analisar", data={"ip": "192.168.0.50", "port": "5555"})
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "AWS-TV-32-BL-02-A" in html
    assert "Desativar 3 app(s)" in html  # safe list from test profile
    assert "Revisar 2 app(s)" in html              # confirm_first
    assert "Acelerar as animações" in html
    assert "Revisar e aplicar" in html
    assert "com.google.android.katniss:search" in html  # memory section
    assert {k: v[1] for k, v in tv.packages.items()} == before
    assert not any(w in c for c in tv.commands for w in MUTATING)


def test_report_without_profile_suggests_export(post, tv):
    tv.props["ro.product.model"] = "OUTRA-TV"
    html = post("/analisar", data={"ip": "192.168.0.50"}).get_data(as_text=True)
    assert "Ainda não há perfil" in html
    assert "atvleve profile-export" in html


def test_invalid_ip_shows_error(post, tv):
    res = post("/analisar", data={"ip": "abc;rm", "port": "5555"})
    assert res.status_code == 400
    assert "IP inválido" in res.get_data(as_text=True)
    assert tv.commands == []


def test_unauthorized_tv_shows_help(post, tv):
    tv.state = "unauthorized"
    res = post("/analisar", data={"ip": "192.168.0.50"})
    html = res.get_data(as_text=True)
    assert res.status_code == 400
    assert "Permitir depuração" in html


def test_pair_validates_code(post):
    res = post("/parear", data={"pair_host": "192.168.0.50:37000", "code": "12ab"})
    assert res.status_code == 400
    assert "Código de pareamento inválido" in res.get_data(as_text=True)


def test_adb_missing_message_has_no_terminal_markup(env, monkeypatch):
    from atvleve.adb import AdbRunner

    monkeypatch.setattr("shutil.which", lambda _: None)
    app = create_app(runner_factory=AdbRunner)
    html = app.test_client().post(
        "/analisar", data={"ip": "192.168.0.50", "csrf": app.config["CSRF_TOKEN"]}
    ).get_data(as_text=True)
    assert "sudo apt install adb" in html
    assert "[bold]" not in html


TV = {"ip": "192.168.0.50", "port": "5555"}


def test_post_without_token_is_refused(client, tv):
    assert client.post("/analisar", data=TV).status_code == 403
    assert client.post("/aplicar", data={**TV, "entendi": "sim"}).status_code == 403
    assert tv.commands == []


def test_post_from_another_site_is_refused(post, tv):
    res = post("/aplicar", {**TV, "entendi": "sim", "pkg": "com.example.vendorstore"},
               headers={"Origin": "http://evil.example"})
    assert res.status_code == 403
    assert tv.state_of("com.example.vendorstore") == "enabled"


def test_foreign_host_header_is_refused(client):
    assert client.get("/", headers={"Host": "rebind.evil.example:8765"}).status_code == 403


def test_review_lists_safe_checked_and_confirm_unchecked(post, tv):
    before = state(tv)
    html = post("/revisar", TV).get_data(as_text=True)
    assert 'value="com.example.vendorstore" checked' in html
    assert 'value="com.example.vendorads" >' in html or 'value="com.example.vendorads">' in html
    assert "com.android.vending" not in html
    assert state(tv) == before


def test_apply_requires_acknowledgement(post, tv):
    res = post("/aplicar", {**TV, "pkg": "com.example.vendorstore"})
    assert res.status_code == 400
    assert tv.state_of("com.example.vendorstore") == "enabled"


def test_apply_only_selected_and_ignores_smuggled_names(post, tv, env):
    tv.home_breakers = set()
    data = {**TV, "entendi": "sim",
            "pkg": ["com.example.vendorads", "com.android.vending", "com.example.userapp"]}
    html = post("/aplicar", data).get_data(as_text=True)
    assert "Reinicie a TV" in html
    assert tv.state_of("com.example.vendorads") == "disabled"       # confirm, ticked
    assert tv.state_of("com.example.vendorstore") == "enabled"      # safe, but unticked
    assert tv.state_of("com.android.vending") == "enabled"          # protected: ignored
    assert tv.state_of("com.example.userapp") == "enabled"          # unknown: ignored
    assert tv.settings["global"]["window_animation_scale"] == "1.0"  # tune not ticked
    assert list((env / "home" / "backups").rglob("*.json"))


def test_apply_then_undo(post, tv):
    tv.home_breakers = set()
    before = state(tv)
    post("/aplicar", {**TV, "entendi": "sim", "tune": "sim",
                      "pkg": ["com.example.vendorstore", "com.google.android.play.games"]})
    assert tv.state_of("com.example.vendorstore") == "disabled"
    assert tv.settings["global"]["window_animation_scale"] == "0.5"
    html = post("/desfazer", TV).get_data(as_text=True)
    assert "Tudo como" in html
    assert state(tv) == before
    assert tv.settings["global"]["window_animation_scale"] == "1.0"


def test_apply_rollback_is_reported(post, tv):
    html = post("/aplicar", {**TV, "entendi": "sim",
                             "pkg": ["com.example.homebreaker"]}).get_data(as_text=True)
    assert "Parado por" in html
    assert tv.state_of("com.example.homebreaker") == "enabled"


def test_nothing_selected(post, tv):
    res = post("/aplicar", {**TV, "entendi": "sim"})
    assert res.status_code == 400
    assert "Nada selecionado" in res.get_data(as_text=True)


def test_trim_cache_only(post, tv):
    html = post("/aplicar", {**TV, "entendi": "sim", "trim": "sim"}).get_data(as_text=True)
    assert getattr(tv, "cache_trimmed", False)
    assert "liberados" in html


def test_report_suggests_swapping_the_stock_launcher(post, tv):
    html = post("/analisar", TV).get_data(as_text=True)
    assert "Trocar tela inicial" in html


def test_launcher_page_asks_to_install_when_none(post, tv):
    html = post("/launcher", TV).get_data(as_text=True)
    assert "FLauncher" in html and "Trocar agora" not in html


def test_launcher_swap_requires_ack_and_installed_launcher(post, tv, app):
    app.config["SETTLE"] = 0
    tv.install_launcher()
    assert post("/trocar-launcher", {**TV, "launcher": "me.efesser.flauncher"}).status_code == 400
    res = post("/trocar-launcher", {**TV, "entendi": "sim", "launcher": "com.example.userapp"})
    assert res.status_code == 400
    assert not any(m in c for c in tv.commands for m in MUTATING)


def test_launcher_swap_then_undo(post, tv, app):
    from fake_tv import FLAUNCHER, LAUNCHER
    app.config["SETTLE"] = 0
    tv.install_launcher()
    assert "Trocar agora" in post("/launcher", TV).get_data(as_text=True)
    html = post("/trocar-launcher", {**TV, "entendi": "sim",
                                     "launcher": "me.efesser.flauncher"}).get_data(as_text=True)
    assert "Tela nova" in html
    assert tv._resolve_home() == FLAUNCHER
    assert "Tudo como" in post("/desfazer", TV).get_data(as_text=True)
    assert tv._resolve_home() == LAUNCHER


def test_launcher_page_offers_input_key_only_for_cleanyourtv(post, tv, app):
    tv.install_launcher()
    assert "Botão Input" not in post("/launcher", TV).get_data(as_text=True)
    tv.install_launcher("com.gathin.tvhome/com.gathin.tvhome.MainActivity")
    assert "Botão Input" in post("/launcher", TV).get_data(as_text=True)
    app.config["SETTLE"] = 0
    post("/trocar-launcher", {**TV, "entendi": "sim", "launcher": "com.gathin.tvhome",
                              "input_key": "sim"})
    assert "InputKeyService" in tv.settings["secure"]["enabled_accessibility_services"]


def test_theme_page_and_apply(post, tv, app):
    from atvleve.theme import THEME_FILE
    gathin = "com.gathin.tvhome/com.gathin.tvhome.MainActivity"
    assert "Só com o" in post("/tema", TV).get_data(as_text=True)
    tv.install_launcher(gathin)
    tv.preferred_home = gathin
    assert "Escolher a cor" in post("/analisar", TV).get_data(as_text=True)
    assert "Azul-noite" in post("/tema", TV).get_data(as_text=True)
    app.config["THEME_SETTLE"] = 0
    html = post("/aplicar-tema", {**TV, "color": "#070A14"}).get_data(as_text=True)
    assert "Cor nova" in html and "#070A14" in tv.files[THEME_FILE]
    res = post("/aplicar-tema", {**TV, "color": "#000000; reboot"})
    assert res.status_code == 400
    html = post("/aplicar-tema", {**TV, "color": "#000000", "custom": "#0B1F17"})
    assert "#0B1F17" in tv.files[THEME_FILE]
