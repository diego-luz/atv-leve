import pytest

from atvleve.profiles import (
    ProfileError,
    build_rules,
    find_profile,
    load_generic,
    load_profile,
    protected_conflicts,
    select_profile,
)


def write(tmp_path, text, name="p.yaml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_real_profiles_load():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "profiles"
    generic = load_generic(root)
    assert len(generic.protected) > 40
    aiwa = load_profile(root / "aiwa-aws-tv-32-bl-02-a.yaml")
    # Filled from the real scan; nothing is "safe" until tested on the TV.
    assert aiwa.safe_to_disable == []
    assert "tv.anoki.acr.anokiacroptin" in aiwa.confirm_first
    assert aiwa.match == {"ro.product.name": ["KTC_AIWA_2K_SA_21V2"],
                          "ro.product.board": ["stanford"]}


def test_placeholders_are_ignored(tmp_path):
    p = write(tmp_path, "protected: [TODO]\nsafe_to_disable: [TODO]\n")
    assert load_profile(p).protected == [] and load_profile(p).safe_to_disable == []


def test_select_profile_by_model(profiles_dir):
    props = {"ro.product.model": "AWS-TV-32-BL-02-A"}
    p = select_profile(lambda k: props.get(k, ""), profiles_dir)
    assert p.name == "test-tv"
    assert select_profile(lambda k: "nope", profiles_dir) is None


def test_profile_with_empty_match_never_autoselects(tmp_path):
    write(tmp_path, "brand: x\nmatch:\n  ro.product.model: []\n", "x.yaml")
    assert select_profile(lambda k: "", tmp_path) is None


def test_find_profile_unknown(profiles_dir):
    with pytest.raises(ProfileError, match="não encontrado"):
        find_profile("nao-existe", profiles_dir)


def test_glob_not_allowed_in_safe_list(tmp_path):
    p = write(tmp_path, "safe_to_disable: ['com.foo.*']\n")
    with pytest.raises(ProfileError, match="curinga"):
        load_profile(p)


def test_invalid_name_rejected(tmp_path):
    p = write(tmp_path, "confirm_first: ['com.foo; reboot']\n")
    with pytest.raises(ProfileError, match="inválido"):
        load_profile(p)


def test_unknown_keys_rejected(tmp_path):
    with pytest.raises(ProfileError, match="desconhecidas"):
        load_profile(write(tmp_path, "safe: [a.b]\n"))


def test_same_package_in_two_lists_rejected(tmp_path):
    with pytest.raises(ProfileError, match="ao mesmo tempo"):
        load_profile(write(tmp_path, "safe_to_disable: [a.b]\nconfirm_first: [a.b]\n"))


def test_generic_without_protection_refused(tmp_path):
    write(tmp_path, "protected: []\n", "_generic.yaml")
    with pytest.raises(ProfileError):
        load_generic(tmp_path)


def test_missing_generic_refused(tmp_path):
    with pytest.raises(ProfileError, match="recusa"):
        load_generic(tmp_path)


def test_conflicts_reported(profiles_dir):
    rules = build_rules(load_generic(profiles_dir), find_profile("test-tv", profiles_dir))
    assert ("com.google.android.gms", "com.google.android.gms") in protected_conflicts(rules)
    assert rules.protected_match("com.example.vendorstore.core")
