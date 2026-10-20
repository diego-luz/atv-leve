from pathlib import Path

from atvleve.packages import Category, classify, classify_one, list_packages, parse_pm_list
from atvleve.profiles import RuleSet

FIX = Path(__file__).parent / "fixtures"


def test_parse_pm_list_formats():
    names = parse_pm_list((FIX / "pm_list_mixed.txt").read_text())
    assert names == {"com.android.shell", "com.example.foo", "com.google.android.gms", "android"}


def test_list_packages_states(tv):
    tv.packages["com.example.vendorads"][1] = "uninstalled"
    pkgs = list_packages(tv)
    assert pkgs["com.example.userapp"].system is False
    assert pkgs["com.example.alreadyoff"].state == "disabled"
    assert pkgs["com.example.vendorads"].state == "uninstalled"
    assert pkgs["com.example.vendorads"].system is True
    assert pkgs["android"].state == "enabled"


def test_classification_order():
    rules = RuleSet(protected=["com.prot*"], safe={"com.safe", "com.prot.x"},
                    confirm={"com.conf"})
    assert classify_one("com.prot.x", rules, set())[0] is Category.PROTECTED
    assert classify_one("com.safe", rules, {"com.safe"})[0] is Category.PROTECTED  # launcher
    assert classify_one("com.safe", rules, set())[0] is Category.SAFE
    assert classify_one("com.conf", rules, set())[0] is Category.CONFIRM
    assert classify_one("com.other", rules, set())[0] is Category.UNKNOWN


def test_classify_sorted_by_category(tv):
    rules = RuleSet(protected=["android"], safe={"com.example.vendorstore"}, confirm=set())
    out = classify(list_packages(tv), rules, set())
    cats = [c.category for c in out]
    assert cats == sorted(cats, key=list(Category).index)
