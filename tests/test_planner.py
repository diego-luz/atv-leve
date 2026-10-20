import pytest

from atvleve.backup import Backup, restore
from atvleve.packages import Category
from atvleve.planner import apply_plan, build_plan
from atvleve.safety import SafetyError, assert_safe_targets, check_health
from atvleve.tune import animation_changes
from helpers import context


def snapshot(tv):
    return ({k: v[1] for k, v in tv.packages.items()}, {k: dict(v) for k, v in tv.settings.items()})


def mutating(cmd):
    return any(w in cmd for w in ("disable-user", "pm enable", "uninstall", "install-existing",
                                  "settings put", "settings delete"))


def test_plan_contents(tv, profiles_dir):
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    plan = build_plan(classified, rules, homes, info.home)
    assert set(plan.targets) == {
        "com.google.android.play.games", "com.example.vendorstore", "com.example.homebreaker"}
    assert {c.name for c in plan.pending_confirm} == {
        "com.google.android.katniss", "com.example.vendorads"}
    assert "com.example.alreadyoff" in plan.already_done
    assert "com.google.android.gms" not in plan.targets


def test_building_a_plan_never_changes_the_tv(tv, profiles_dir):
    before = snapshot(tv)
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    build_plan(classified, rules, homes, info.home, include=["com.example.userapp"],
               settings=animation_changes(tv, "0.5"))
    assert snapshot(tv) == before
    assert not any(mutating(c) for c in tv.commands)


def test_no_protected_package_in_any_plan(tv, profiles_dir):
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    everything = [c.name for c in classified]
    plan = build_plan(classified, rules, homes, info.home, include=everything,
                      confirmed=everything)
    protected = {c.name for c in classified if c.category is Category.PROTECTED}
    assert protected and not protected & set(plan.targets)
    assert {n for n, _ in plan.refused} >= protected


def test_unknown_only_with_include(tv, profiles_dir):
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    assert "com.example.userapp" not in build_plan(classified, rules, homes, info.home).targets
    plan = build_plan(classified, rules, homes, info.home, include=["com.example.userapp"])
    assert "com.example.userapp" in plan.targets


def test_confirm_needs_confirmation(tv, profiles_dir):
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    plan = build_plan(classified, rules, homes, info.home, confirmed=["com.example.vendorads"])
    assert "com.example.vendorads" in plan.targets
    assert "com.google.android.katniss" not in plan.targets


def test_include_not_installed_is_refused(tv, profiles_dir):
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    plan = build_plan(classified, rules, homes, info.home, include=["com.nao.existe"])
    assert ("com.nao.existe", "não está instalado nesta TV") in plan.refused


def test_assert_safe_targets_last_line_of_defense(tv, profiles_dir):
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    with pytest.raises(SafetyError):
        assert_safe_targets(["com.android.vending"], rules, homes, info.home)
    with pytest.raises(SafetyError):
        assert_safe_targets(["com.google.android.apps.tv.launcherx"], rules, set(), info.home)
    assert_safe_targets(["com.example.vendorstore"], rules, homes, info.home)


def test_apply_and_restore_round_trip(tv, profiles_dir, tmp_path):
    before = snapshot(tv)
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    settings = animation_changes(tv, "0.5")
    plan = build_plan(classified, rules, homes, info.home,
                      confirmed=["com.example.vendorads"], settings=settings)
    plan.items = [i for i in plan.items if i.name != "com.example.homebreaker"]
    backup = Backup.create(tv, info, pkgs, [(s.namespace, s.key) for s in settings],
                           root=tmp_path / "backups")
    result = apply_plan(tv, plan, backup, batch_size=2, log=lambda *_: None)

    assert result.ok, result
    assert tv.state_of("com.example.vendorads") == "disabled"
    assert tv.settings["global"]["window_animation_scale"] == "0.5"
    assert tv.settings["global"]["animator_duration_scale"] == "0.5"
    assert backup.data["status"] == "completed"
    assert check_health(tv).ok

    report = restore(tv, Backup.load(backup.path), log=lambda *_: None)
    assert report.ok
    assert snapshot(tv) == before  # including settings that did not exist before


def test_uninstall_round_trip(tv, profiles_dir, tmp_path):
    before = snapshot(tv)
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    plan = build_plan(classified, rules, homes, info.home, uninstall=True)
    plan.items = [i for i in plan.items if i.name != "com.example.homebreaker"]
    backup = Backup.create(tv, info, pkgs, [], root=tmp_path / "backups")
    apply_plan(tv, plan, backup, log=lambda *_: None)
    assert tv.state_of("com.example.vendorstore") == "uninstalled"
    # the already-disabled one was uninstalled too and must come back disabled
    assert tv.state_of("com.example.alreadyoff") == "uninstalled"
    restore(tv, backup, log=lambda *_: None)
    assert snapshot(tv) == before


def test_automatic_rollback_when_home_disappears(tv, profiles_dir, tmp_path):
    before = snapshot(tv)
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    plan = build_plan(classified, rules, homes, info.home)
    # Batch 1: play.games (ok); batch 2: homebreaker (breaks HOME)
    plan.items.sort(key=lambda i: i.name != "com.google.android.play.games")
    backup = Backup.create(tv, info, pkgs, [], root=tmp_path / "backups")
    result = apply_plan(tv, plan, backup, batch_size=1, log=lambda *_: None)

    assert result.aborted
    assert "com.example.homebreaker" in result.rolled_back
    assert tv.state_of("com.example.homebreaker") == "enabled"
    assert check_health(tv).ok
    assert backup.data["status"] == "rolled_back"
    restore(tv, backup, log=lambda *_: None)
    assert snapshot(tv) == before


def test_rollback_reverts_whole_batch(tv, profiles_dir, tmp_path):
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    plan = build_plan(classified, rules, homes, info.home)
    backup = Backup.create(tv, info, pkgs, [], root=tmp_path / "backups")
    result = apply_plan(tv, plan, backup, batch_size=10, log=lambda *_: None)
    assert result.aborted
    assert set(result.rolled_back) == set(plan.targets)
    assert all(tv.state_of(n) == "enabled" for n in plan.targets)
    assert result.done == []


def test_refused_by_tv_is_reported_not_fatal(tv, profiles_dir, tmp_path):
    tv.refuse_disable.add("com.example.vendorstore")
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    plan = build_plan(classified, rules, homes, info.home)
    plan.items = [i for i in plan.items if i.name != "com.example.homebreaker"]
    backup = Backup.create(tv, info, pkgs, [], root=tmp_path / "backups")
    result = apply_plan(tv, plan, backup, log=lambda *_: None)
    assert not result.aborted
    assert [n for n, _ in result.failed] == ["com.example.vendorstore"]
    assert "com.google.android.play.games" in result.done


def test_apply_refuses_unhealthy_tv(tv, profiles_dir, tmp_path):
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    plan = build_plan(classified, rules, homes, info.home)
    tv.packages["com.google.android.apps.tv.launcherx"][1] = "disabled"
    backup = Backup.create(tv, info, pkgs, [], root=tmp_path / "backups")
    result = apply_plan(tv, plan, backup, log=lambda *_: None)
    assert result.aborted and not result.done
    assert not any("disable-user" in c for c in tv.commands)


def test_apply_requires_backup_on_disk(tv, profiles_dir, tmp_path):
    info, pkgs, rules, homes, classified = context(tv, profiles_dir)
    plan = build_plan(classified, rules, homes, info.home)
    backup = Backup.create(tv, info, pkgs, [], root=tmp_path / "backups")
    backup.path.unlink()
    with pytest.raises(RuntimeError):
        apply_plan(tv, plan, backup, log=lambda *_: None)
    assert not any("disable-user" in c for c in tv.commands)
