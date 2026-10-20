import json

import pytest

from atvleve.backup import Backup, BackupError, latest_backup, list_backups, read_setting
from atvleve.tune import ANIMATION_KEYS, animation_changes, normalize_scale
from helpers import context


def test_backup_content(tv, profiles_dir, tmp_path):
    info, pkgs, *_ = context(tv, profiles_dir)
    b = Backup.create(tv, info, pkgs, list(ANIMATION_KEYS), root=tmp_path)
    data = json.loads(b.path.read_text())
    assert b.path.parent.name == "FAKE123SERIAL"
    assert data["device"]["model"] == "AWS-TV-32-BL-02-A"
    assert data["packages"]["com.example.alreadyoff"]["state"] == "disabled"
    values = {s["key"]: s["value"] for s in data["settings"]}
    assert values["window_animation_scale"] == "1.0"
    assert values["animator_duration_scale"] is None  # did not exist
    assert not list(tmp_path.rglob("*.tmp"))


def test_backup_write_failure_raises(tv, profiles_dir, tmp_path):
    info, pkgs, *_ = context(tv, profiles_dir)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(BackupError):
        Backup.create(tv, info, pkgs, [], root=blocker)


def test_latest_backup_prefers_ones_with_changes(tv, profiles_dir, tmp_path):
    info, pkgs, *_ = context(tv, profiles_dir)
    with_changes = Backup.create(tv, info, pkgs, [], root=tmp_path)
    with_changes.record_change("a.b", "disable", True)
    with_changes.save()
    empty = Backup.create(tv, info, pkgs, [], root=tmp_path)
    assert len(list_backups(root=tmp_path)) == 2
    assert latest_backup(info.backup_id, root=tmp_path) == empty.path
    assert latest_backup(info.backup_id, root=tmp_path, with_changes=True) == with_changes.path
    with pytest.raises(BackupError):
        latest_backup("other", root=tmp_path)


def test_load_rejects_foreign_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"hello": 1}')
    with pytest.raises(BackupError):
        Backup.load(p)


def test_read_setting_null(tv):
    assert read_setting(tv, "global", "animator_duration_scale") is None
    assert read_setting(tv, "global", "window_animation_scale") == "1.0"
    with pytest.raises(ValueError):
        read_setting(tv, "global", "x; reboot")


def test_scale_validation(tv):
    assert normalize_scale("0,5") == "0.5"
    assert normalize_scale(1.0) == "1"
    with pytest.raises(ValueError):
        normalize_scale("2")
    changes = animation_changes(tv, "0.5")
    assert all(c.needed for c in changes)
