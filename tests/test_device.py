from pathlib import Path

from atvleve.device import (
    get_info,
    is_real_home,
    parse_components,
    parse_df,
    parse_dumpsys_meminfo,
    parse_meminfo,
    resolve_home,
)

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text()


def test_parse_meminfo():
    m = parse_meminfo(read("meminfo.txt"))
    assert m["MemTotal"] == 1530112
    assert m["MemAvailable"] == 412876
    assert m["Active(anon)"] == 310228


def test_parse_df_variants():
    assert parse_df(read("df_toybox.txt")) == (4579328, 867284)
    total, free = parse_df(read("df_human.txt"))
    assert total == int(4.3 * 1024**2) and free == 847 * 1024
    total, free = parse_df(read("df_old.txt"))
    assert total == int(5.5 * 1024**2) and free == int(4.3 * 1024**2)
    assert parse_df(read("df_wrapped.txt")) == (4579328, 867284)
    assert parse_df("") == (None, None)
    assert parse_df("garbage\nmore garbage") == (None, None)


def test_parse_components():
    comps = parse_components(read("resolve_home_launcherx.txt"))
    assert comps == [
        "com.google.android.apps.tv.launcherx/"
        "com.google.android.apps.tv.launcherx.home.HomeActivity"
    ]
    assert parse_components(read("resolve_home_none.txt")) == []
    assert parse_components("com.foo/.Main") == ["com.foo/com.foo.Main"]


def test_fake_homes_are_not_real():
    assert is_real_home("com.foo/com.foo.Home")
    assert not is_real_home(None)
    assert not is_real_home("com.android.tv.settings/com.android.tv.settings.FallbackHome")
    assert not is_real_home("android/com.android.internal.app.ResolverActivity")


def test_parse_dumpsys_prefers_pss():
    metric, procs = parse_dumpsys_meminfo(read("dumpsys_meminfo.txt"))
    assert metric == "PSS"
    assert procs[0].name == "system" and procs[0].kb == 187532
    assert [p.name for p in procs][:3] == [
        "system", "com.google.android.apps.tv.launcherx", "com.google.android.katniss:search"
    ]
    assert len(procs) == 7  # OOM section is not mixed in


def test_parse_dumpsys_rss_fallback():
    text = "Total RSS by process:\n    1,000K: a.b (pid 1)\n\n"
    assert parse_dumpsys_meminfo(text)[0] == "RSS"
    assert parse_dumpsys_meminfo("nothing") == ("", [])


def test_get_info(tv):
    info = get_info(tv)
    assert info.brand == "Aiwa"
    assert info.model == "AWS-TV-32-BL-02-A"
    assert info.sdk == "34"
    assert info.mem_total_kb == 1530112
    assert info.data_free_kb == 867284
    assert info.home.startswith("com.google.android.apps.tv.launcherx/")
    assert info.backup_id == "FAKE123SERIAL"


def test_get_info_tolerates_missing_data(tv):
    tv.props = {}
    info = get_info(tv)
    assert info.model == ""
    assert info.backup_id == "192.168.0.50_5555"


def test_resolve_home_none_when_launcher_disabled(tv):
    tv.packages["com.google.android.apps.tv.launcherx"][1] = "disabled"
    assert not is_real_home(resolve_home(tv))
