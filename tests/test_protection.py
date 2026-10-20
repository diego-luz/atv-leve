"""The safety net: these packages must ALWAYS be protected by _generic.yaml."""

from pathlib import Path

import pytest

from atvleve.packages import Category, classify_one
from atvleve.profiles import build_rules, load_generic

ROOT = Path(__file__).resolve().parents[1]

MUST_PROTECT = [
    "android",
    "com.android.shell",
    "com.android.systemui",
    "com.android.vending",
    "com.google.android.gms",
    "com.google.android.gms.persistent",
    "com.google.android.gsf",
    "com.android.tv.settings",
    "com.android.settings",
    "com.android.providers.settings",
    "com.android.providers.tv",
    "com.android.providers.media",
    "com.google.android.providers.media.module",
    "com.android.packageinstaller",
    "com.google.android.packageinstaller",
    "com.google.android.permissioncontroller",
    "com.android.permissioncontroller",
    "com.android.bluetooth",
    "com.google.android.bluetooth",
    "com.android.inputmethod.latin",
    "com.google.android.inputmethod.latin",
    "com.google.android.leanback.ime",
    "com.google.android.tvlauncher",
    "com.google.android.leanbacklauncher",
    "com.google.android.apps.tv.launcherx",
    "com.android.tv",
    "com.android.tv.provision",
    "com.google.android.tungsten.setupwraith",
    "com.google.android.webview",
    "com.android.webview",
    "com.google.android.networkstack",
    "com.android.networkstack.tethering",
    "com.android.wifi.resources",
    "com.google.android.ext.services",
    "com.google.android.tv.remote.service",
    "com.android.systemui.overlay.tv",
    "com.mediatek.wwtv.tvcenter",
    "com.mediatek.tv.oneworld.tvcenter",
    "com.realtek.tv",
    "com.droidlogic.tvinput",
    "com.vendor.hdmicec",
    "com.vendor.tvsettings",
    "com.vendor.factorysetting",
    "com.vendor.livetv",
    # Seen on the real Aiwa/KTC (MT5867) scan:
    "com.mediatek.tvinput",
    "com.mediatek.wwtv.setupwizard",
    "com.android.tv.overlay.lowram",
    "com.android.inputdevices",
    "com.android.intentresolver",
    "com.android.rkpdapp",
    "com.android.cts.ctsshim",
    "com.dolby.android.audio.service",
    "com.mstar.android.tv.disclaimercustomization",
    "ktc.factorymenu.ui",
    "mediatek.factorymenu.ui",
    "com.ktc.tv.factory",
    "com.ktc.serialport",
]

# Names that must NOT be swallowed by an over-broad pattern (otherwise nothing could be cleaned).
MUST_NOT_PROTECT = [
    "com.google.android.youtube.tv",
    "com.google.android.play.games",
    "com.google.android.katniss",
    "com.netflix.ninja",
    "com.example.vendorstore",
    "tv.anoki.acr.anokiacroptin",
    "fusion.android.tv.demo",
    "com.google.android.tvrecommendations",
    "com.google.android.apps.mediashell",
]


@pytest.fixture(scope="module")
def rules():
    return build_rules(load_generic(ROOT / "profiles"), None)


@pytest.mark.parametrize("name", MUST_PROTECT)
def test_essential_packages_are_protected(rules, name):
    cat, _ = classify_one(name, rules, set())
    assert cat is Category.PROTECTED, name


@pytest.mark.parametrize("name", MUST_NOT_PROTECT)
def test_common_bloat_is_not_protected(rules, name):
    cat, _ = classify_one(name, rules, set())
    assert cat is not Category.PROTECTED, name


def test_current_launcher_is_protected_at_runtime(rules):
    cat, reason = classify_one("com.vendor.weirdhome", rules, {"com.vendor.weirdhome"})
    assert cat is Category.PROTECTED
    assert "inicial" in reason


def test_protection_beats_safe_list(rules):
    rules.safe.add("com.google.android.gms")
    try:
        assert classify_one("com.google.android.gms", rules, set())[0] is Category.PROTECTED
    finally:
        rules.safe.discard("com.google.android.gms")
