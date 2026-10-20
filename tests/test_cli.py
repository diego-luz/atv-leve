import json

import pytest

from atvleve import cli
from fake_tv import FakeTv


@pytest.fixture
def tvcli(monkeypatch, env):
    tv = FakeTv()
    monkeypatch.setattr(cli, "make_runner", lambda args: tv)
    return tv


def run(*argv):
    return cli.main(list(argv))


def test_doctor(tvcli, capsys):
    assert run("doctor", "--ip", "192.168.0.50") == 0
    out = capsys.readouterr().out
    assert "conectado" in out and "Tudo certo" in out


def test_doctor_adb_missing(monkeypatch, env, capsys):
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert run("doctor") == 1
    assert "platform-tools" in capsys.readouterr().out


def test_info(tvcli, capsys):
    assert run("info", "--ip", "192.168.0.50") == 0
    out = capsys.readouterr().out
    assert "AWS-TV-32-BL-02-A" in out and "1.5 GB" in out
    assert "RAM baixa" in out


def test_scan(tvcli, capsys):
    assert run("scan") == 0
    out = capsys.readouterr().out
    assert "test-tv" in out
    assert "protegido" in out and "seguro" in out and "desconhecido" in out


def test_plan_changes_nothing(tvcli, capsys):
    before = {k: v[1] for k, v in tvcli.packages.items()}
    settings = json.dumps(tvcli.settings)
    assert run("plan", "--include", "com.example.userapp,com.android.vending") == 0
    out = capsys.readouterr().out
    assert "com.example.vendorstore" in out
    assert "Recusado" in out and "com.android.vending" in out
    assert {k: v[1] for k, v in tvcli.packages.items()} == before
    assert json.dumps(tvcli.settings) == settings
    assert not any(w in c for c in tvcli.commands
                   for w in ("disable-user", "settings put", "uninstall", "pm enable"))


def test_apply_then_restore(tvcli, env, capsys):
    tvcli.home_breakers = set()
    before = {k: v[1] for k, v in tvcli.packages.items()}
    assert run("apply", "--yes") == 0
    assert tvcli.state_of("com.example.vendorstore") == "disabled"
    assert tvcli.state_of("com.google.android.katniss") == "enabled"  # confirm_first skipped
    assert tvcli.settings["global"]["window_animation_scale"] == "0.5"
    backups = list((env / "home" / "backups" / "FAKE123SERIAL").glob("*.json"))
    assert len(backups) == 1
    out = capsys.readouterr().out
    assert "atvleve restore" in out

    assert run("restore", "--yes") == 0
    assert {k: v[1] for k, v in tvcli.packages.items()} == before
    assert tvcli.settings["global"]["window_animation_scale"] == "1.0"
    assert "animator_duration_scale" not in tvcli.settings["global"]


def test_apply_asks_for_confirm_first(tvcli, monkeypatch):
    tvcli.home_breakers = set()
    answers = iter([False, True, True])  # vendorads? katniss? apply?
    asked = []

    def fake_ask(prompt, default=False):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(cli.Confirm, "ask", staticmethod(fake_ask))
    assert run("apply", "--no-tune") == 0
    assert len(asked) == 3
    assert tvcli.state_of("com.example.vendorads") == "enabled"
    assert tvcli.state_of("com.google.android.katniss") == "disabled"


def test_apply_cancel_changes_nothing(tvcli, monkeypatch, env):
    monkeypatch.setattr(cli.Confirm, "ask", staticmethod(lambda *a, **k: False))
    before = {k: v[1] for k, v in tvcli.packages.items()}
    assert run("apply") == 0
    assert {k: v[1] for k, v in tvcli.packages.items()} == before
    assert not (env / "home" / "backups").exists()


def test_apply_rollback_exit_code(tvcli, capsys):
    assert run("apply", "--yes", "--batch-size", "1") == 2
    out = capsys.readouterr().out
    assert "revertido" in out and "atvleve restore" in out
    assert tvcli.state_of("com.example.homebreaker") == "enabled"


def test_tune_and_restore(tvcli):
    assert run("tune", "--scale", "0", "--yes") == 0
    assert tvcli.settings["global"]["transition_animation_scale"] == "0"
    assert run("restore", "--yes") == 0
    assert "transition_animation_scale" not in tvcli.settings["global"]


def test_top(tvcli, capsys):
    assert run("top", "--limit", "3") == 0
    out = capsys.readouterr().out
    assert "PSS" in out and "katniss" in out and "vendorads" not in out


def test_profile_export(tvcli, env):
    out = env / "perfil.yaml"
    assert run("profile-export", "--output", str(out)) == 0
    import yaml

    data = yaml.safe_load(out.read_text())
    assert data["match"] == {"ro.product.model": ["AWS-TV-32-BL-02-A"]}
    assert data["safe_to_disable"] == []
    assert "com.example.vendorads" in data["inventory"]["system_enabled"]
    assert "FAKE123SERIAL" not in out.read_text()
    assert run("profile-export", "--output", str(out)) == 1  # no overwrite without --force

    # The exported file must itself be a valid profile.
    from atvleve.profiles import load_profile

    load_profile(out)


def test_restore_list(tvcli, capsys):
    assert run("backup") == 0
    assert run("restore", "--list") == 0
    assert "manual" in capsys.readouterr().out


def test_restore_all_undoes_several_runs(tvcli):
    tvcli.home_breakers = set()
    before = {k: v[1] for k, v in tvcli.packages.items()}
    settings = json.dumps(tvcli.settings)
    assert run("apply", "--yes", "--no-tune") == 0
    assert run("tune", "--yes") == 0
    assert run("restore", "--yes") == 0  # only the latest (tune)
    assert tvcli.state_of("com.example.vendorstore") == "disabled"
    assert run("restore", "--yes", "--all") == 0
    assert {k: v[1] for k, v in tvcli.packages.items()} == before
    assert json.dumps(tvcli.settings) == settings
