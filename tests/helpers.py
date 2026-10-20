from atvleve.device import get_info, home_packages
from atvleve.packages import classify, list_packages
from atvleve.profiles import build_rules, find_profile, load_generic


def context(tv, profiles_dir, profile="test-tv"):
    rules = build_rules(load_generic(profiles_dir), find_profile(profile, profiles_dir))
    info = get_info(tv)
    pkgs = list_packages(tv)
    homes = home_packages(tv)
    return info, pkgs, rules, homes, classify(pkgs, rules, homes)
