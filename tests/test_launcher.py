import pytest

from atvleve.analysis import launcher_options, run_launcher_swap
from atvleve.backup import restore
from atvleve.cli import main
from atvleve.device import get_info
from atvleve.launcher import LauncherError, alternatives, set_home, swap
from atvleve.packages import list_packages
from atvleve.safety import check_health
from fake_tv import FLAUNCHER, LAUNCHER, RECOVERY, WIZARD

STOCK = "com.google.android.apps.tv.launcherx"
FL = "me.efesser.flauncher"


def do_swap(tv, tmp_path, package=FL):
    return swap(tv, package, get_info(tv), list_packages(tv), root=tmp_path, log=lambda m: None)


def test_wizard_grabs_home_in_the_fake(tv):
    # Sanity check of the simulation the order below relies on.
    tv.install_launcher()
    tv.packages[STOCK][1] = "disabled"
    assert tv._resolve_home() == RECOVERY
    assert not check_health(tv).ok  # the wizard is not a working home screen


def test_stock_launcher_is_not_an_alternative(tv):
    assert alternatives(tv) == []
    tv.install_launcher()
    assert [c.package for c in alternatives(tv)] == [FL]
    assert alternatives(tv)[0].label == "FLauncher"


def test_refuses_when_launcher_not_installed(tv, tmp_path):
    with pytest.raises(LauncherError):
        do_swap(tv, tmp_path)
    assert not any("disable-user" in c for c in tv.commands)


def test_refuses_the_stock_launcher_itself(tv, tmp_path):
    tv.install_launcher()
    with pytest.raises(LauncherError):
        do_swap(tv, tmp_path, STOCK)


def test_swap_sets_home_then_disables_wizard_before_stock(tv, tmp_path):
    tv.install_launcher()
    backup, result = do_swap(tv, tmp_path)
    assert result.ok, result.message
    assert tv.state_of(WIZARD) == "disabled"
    assert tv.state_of(STOCK) == "disabled"
    assert tv._resolve_home() == FLAUNCHER
    order = [c for c in tv.commands if "set-home-activity" in c or "disable-user" in c]
    assert "set-home-activity" in order[0]
    disables = [c for c in order if "disable-user" in c]
    assert disables[0].endswith(WIZARD) and disables[1].endswith(STOCK)
    assert "set-home-activity" in order[-1]  # re-asserted after the last change
    assert backup.data["home_before"] == LAUNCHER
    assert backup.data["status"] == "completed"
    assert check_health(tv).ok


def test_never_uninstalls(tv, tmp_path):
    tv.install_launcher()
    do_swap(tv, tmp_path)
    assert not any("uninstall" in c for c in tv.commands)


def test_rollback_when_home_is_lost(tv, tmp_path):
    tv.install_launcher()
    tv.refuse_disable = {WIZARD}  # the wizard stays and grabs HOME when stock goes
    tv.packages[WIZARD][1] = "enabled"
    backup, result = do_swap(tv, tmp_path)
    assert not result.ok and result.rolled_back
    assert tv.state_of(STOCK) == "enabled"
    assert tv._resolve_home() == LAUNCHER
    assert backup.data["status"] == "rolled_back"


def test_rollback_when_home_moves_after_disabling(tv, tmp_path):
    tv.install_launcher()
    tv.packages[WIZARD][1] = "disabled"  # nothing to disable first, so stock goes first...
    tv.home_breakers = {STOCK}           # ...and this TV needs the stock package for HOME
    backup, result = do_swap(tv, tmp_path)
    assert not result.ok and result.rolled_back
    assert tv.state_of(STOCK) == "enabled"
    assert check_health(tv).ok


def test_set_home_refused_touches_nothing(tv, tmp_path):
    tv.install_launcher()
    tv.refuse_set_home = True
    _, result = do_swap(tv, tmp_path)
    assert not result.ok
    assert not any("disable-user" in c for c in tv.commands)


def test_set_home_rejects_bad_component(tv):
    with pytest.raises(ValueError):
        set_home(tv, "me.efesser.flauncher/x; reboot")


def test_restore_brings_everything_back(tv, tmp_path):
    tv.install_launcher()
    backup, result = do_swap(tv, tmp_path)
    assert result.ok
    report = restore(tv, backup, log=lambda m: None)
    assert report.ok, report.failed
    assert tv.state_of(WIZARD) == "enabled"
    assert tv.state_of(STOCK) == "enabled"
    assert tv._resolve_home() == LAUNCHER
    assert tv.state_of(FL) == "enabled"  # the new launcher stays as a choice


def test_run_launcher_swap_measures_ram(tv, env):
    tv.install_launcher()
    out = run_launcher_swap(tv, FL, settle=0)
    assert out.result.ok and out.health.ok
    assert out.mem_before_kb and out.mem_after_kb
    opts = launcher_options(tv)
    assert opts.current == FLAUNCHER


def test_cli_lists_and_swaps(tv, env, monkeypatch, capsys):
    monkeypatch.setattr("atvleve.cli.make_runner", lambda args: tv)
    monkeypatch.setattr("atvleve.analysis.time.sleep", lambda s: None)
    assert main(["launcher", "--ip", "192.168.0.50"]) == 0
    assert "Nenhum launcher alternativo" in capsys.readouterr().out
    tv.install_launcher()
    assert main(["launcher", "--ip", "192.168.0.50", "--set", FL, "--yes"]) == 0
    assert tv._resolve_home() == FLAUNCHER
    assert main(["restore", "--ip", "192.168.0.50", "--yes"]) == 0
    assert tv._resolve_home() == LAUNCHER


def test_does_not_disable_when_new_launcher_does_not_start(tv, tmp_path):
    tv.install_launcher()
    tv.crashing = {FLAUNCHER}
    _, result = do_swap(tv, tmp_path)
    assert not result.ok and "não abriu" in result.message
    assert not any("disable-user" in c for c in tv.commands)
    assert tv._resolve_home() != FLAUNCHER


def test_install_apk_checks_the_file(tv, tmp_path):
    from atvleve.launcher import install_apk
    with pytest.raises(LauncherError):
        install_apk(tv, tmp_path / "nao-existe.apk")
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(LauncherError):
        install_apk(tv, tmp_path / "x.txt")
    apk = tmp_path / "tvhome.apk"
    apk.write_bytes(b"PK")
    assert "Success" in install_apk(tv, apk)
    assert tv.installed_apks == [str(apk.resolve())]


def test_cli_install_only_installs(tv, env, monkeypatch, tmp_path):
    monkeypatch.setattr("atvleve.cli.make_runner", lambda args: tv)
    apk = tmp_path / "tvhome.apk"
    apk.write_bytes(b"PK")
    assert main(["launcher", "--ip", "192.168.0.50", "--install", str(apk)]) == 0
    assert FL in tv.packages
    assert tv._resolve_home() == "android/com.android.internal.app.ResolverActivity" or \
        tv.preferred_home is None
    assert not any("disable-user" in c or "set-home" in c for c in tv.commands)


GATHIN = "com.gathin.tvhome/com.gathin.tvhome.MainActivity"
A11Y = "enabled_accessibility_services"


def test_input_key_only_for_the_cleanyourtv_launcher(tv, tmp_path):
    tv.install_launcher()
    with pytest.raises(LauncherError):
        swap(tv, FL, get_info(tv), list_packages(tv), root=tmp_path, log=lambda m: None,
             input_key=True)


def test_input_key_keeps_other_services_and_restore_puts_them_back(tv, tmp_path):
    from atvleve.launcher import INPUT_KEY_SERVICE
    tv.install_launcher(GATHIN)
    tv.settings["secure"][A11Y] = "com.example.reader/com.example.reader.Svc"
    backup, result = swap(tv, "com.gathin.tvhome", get_info(tv), list_packages(tv),
                          root=tmp_path, log=lambda m: None, input_key=True)
    assert result.ok and not result.warnings
    services = tv.settings["secure"][A11Y].split(":")
    assert services == ["com.example.reader/com.example.reader.Svc", INPUT_KEY_SERVICE]
    assert tv.settings["secure"]["accessibility_enabled"] == "1"
    assert restore(tv, backup, log=lambda m: None).ok
    assert tv.settings["secure"][A11Y] == "com.example.reader/com.example.reader.Svc"
    assert "accessibility_enabled" not in tv.settings["secure"]


def test_input_key_not_duplicated(tv, tmp_path):
    from atvleve.launcher import INPUT_KEY_SERVICE
    tv.install_launcher(GATHIN)
    tv.settings["secure"][A11Y] = INPUT_KEY_SERVICE
    _, result = swap(tv, "com.gathin.tvhome", get_info(tv), list_packages(tv), root=tmp_path,
                     log=lambda m: None, input_key=True)
    assert result.ok and tv.settings["secure"][A11Y] == INPUT_KEY_SERVICE


def test_no_input_key_without_flag(tv, tmp_path):
    tv.install_launcher(GATHIN)
    swap(tv, "com.gathin.tvhome", get_info(tv), list_packages(tv), root=tmp_path,
         log=lambda m: None)
    assert A11Y not in tv.settings["secure"]


def test_cli_input_key_needs_cleanyourtv_home(tv, env, monkeypatch):
    monkeypatch.setattr("atvleve.cli.make_runner", lambda args: tv)
    monkeypatch.setattr("atvleve.analysis.time.sleep", lambda s: None)
    assert main(["launcher", "--ip", "192.168.0.50", "--input-key"]) == 1
    assert A11Y not in tv.settings["secure"]
    tv.install_launcher(GATHIN)
    assert main(["launcher", "--ip", "192.168.0.50", "--set", "com.gathin.tvhome", "-y"]) == 0
    assert main(["launcher", "--ip", "192.168.0.50", "--input-key"]) == 0
    assert "InputKeyService" in tv.settings["secure"][A11Y]
    assert main(["restore", "--ip", "192.168.0.50", "--yes"]) == 0
    assert A11Y not in tv.settings["secure"]


def test_stock_launcher_with_higher_priority(tv, tmp_path):
    # Seen on the Aiwa (tvlauncher priority=2): the preference only wins once it is disabled.
    tv.install_launcher(GATHIN)
    tv.stock_wins = True
    backup, result = swap(tv, "com.gathin.tvhome", get_info(tv), list_packages(tv),
                          root=tmp_path, log=lambda m: None)
    assert result.ok, result.message
    assert tv._resolve_home() == GATHIN
    assert tv.state_of(STOCK) == "disabled" and tv.state_of(WIZARD) == "disabled"
    assert restore(tv, backup, log=lambda m: None).ok
    assert tv._resolve_home() == LAUNCHER


def test_home_landing_elsewhere_rolls_back(tv, tmp_path):
    tv.install_launcher(GATHIN)
    tv.stock_wins = True
    tv.install_launcher("com.other.home/com.other.home.Main")
    tv.preferred_home_dropped = True  # preference lost: after stock goes, HOME is a chooser
    _, result = swap(tv, "com.gathin.tvhome", get_info(tv), list_packages(tv), root=tmp_path,
                     log=lambda m: None)
    assert not result.ok and result.rolled_back
    assert tv.state_of(STOCK) == "enabled" and tv.state_of(WIZARD) == "enabled"
