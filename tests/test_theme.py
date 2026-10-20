import json

import pytest

from atvleve.analysis import run_theme, theme_options
from atvleve.backup import restore
from atvleve.cli import main
from atvleve.theme import THEME_FILE, normalize_color, too_light
from fake_tv import FakeTv

GATHIN = "com.gathin.tvhome/com.gathin.tvhome.MainActivity"
A11Y = "enabled_accessibility_services"


@pytest.fixture
def tv():
    tv = FakeTv()
    tv.install_launcher(GATHIN)
    tv.preferred_home = GATHIN
    tv.packages["com.google.android.apps.tv.launcherx"][1] = "disabled"
    tv.packages["com.google.android.tungsten.setupwraith"][1] = "disabled"
    return tv


def test_normalize_color():
    assert normalize_color("170a2b") == "#170A2B"
    for bad in ("#12345", "red", "#GGGGGG", "#000000; reboot", ""):
        with pytest.raises(ValueError):
            normalize_color(bad)


def test_too_light():
    assert not too_light("#170A2B") and too_light("#FFFFFF") and too_light("#A0A0A0")


def test_only_with_the_cleanyourtv_launcher(env):
    with pytest.raises(ValueError):
        run_theme(FakeTv(), "#170A2B", settle=0)


def test_sets_color_keeps_other_keys_and_restore(tv, env):
    tv.files[THEME_FILE] = json.dumps({"columns": 6, "background_color": "#000000"})
    before = tv.files[THEME_FILE]
    out = run_theme(tv, "#170a2b", settle=0)
    theme = json.loads(tv.files[THEME_FILE])
    assert theme == {"columns": 6, "background_color": "#170A2B"}
    assert out.previous == "#000000" and out.color == "#170A2B" and not out.warning
    assert tv.force_stopped == 1
    assert theme_options(tv).current == "#170A2B"
    assert restore(tv, out.backup, log=lambda m: None).ok
    assert tv.files[THEME_FILE] == before


def test_restore_removes_file_that_did_not_exist(tv, env):
    out = run_theme(tv, "#0B1F17", settle=0)
    assert THEME_FILE in tv.files
    restore(tv, out.backup, log=lambda m: None)
    assert THEME_FILE not in tv.files


def test_restart_keeps_input_key_service(tv, env):
    svc = "com.gathin.tvhome/com.gathin.tvhome.InputKeyService"
    tv.settings["secure"][A11Y] = svc
    run_theme(tv, "#0B1F17", settle=0)
    assert tv.settings["secure"][A11Y] == svc
    assert tv.settings["secure"]["accessibility_enabled"] == "1"


def test_light_color_warns(tv, env):
    assert run_theme(tv, "#FFFFFF", settle=0).warning


def test_cli(tv, env, monkeypatch, capsys):
    monkeypatch.setattr("atvleve.cli.make_runner", lambda args: tv)
    monkeypatch.setattr("atvleve.theme.time.sleep", lambda s: None)
    assert main(["theme", "--ip", "192.168.0.50"]) == 0
    assert "#000000" in capsys.readouterr().out
    assert main(["theme", "--ip", "192.168.0.50", "--color", "#2A0A14"]) == 0
    assert json.loads(tv.files[THEME_FILE])["background_color"] == "#2A0A14"
    assert main(["restore", "--ip", "192.168.0.50", "--yes"]) == 0
    assert THEME_FILE not in tv.files
