"""A stateful fake TV behind the real AdbRunner (only `_exec` is replaced)."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from atvleve.adb import AdbResult, AdbRunner

FIXTURES = Path(__file__).parent / "fixtures"

LAUNCHER = (
    "com.google.android.apps.tv.launcherx/"
    "com.google.android.apps.tv.launcherx.home.HomeActivity"
)
FALLBACK = "com.android.tv.settings/com.android.tv.settings.FallbackHome"
WIZARD = "com.google.android.tungsten.setupwraith"
RECOVERY = f"{WIZARD}/{WIZARD}.RecoveryActivity"
FLAUNCHER = "me.efesser.flauncher/me.efesser.flauncher.MainActivity"

DEFAULT_PROPS = {
    "ro.product.brand": "Aiwa",
    "ro.product.manufacturer": "Aiwa",
    "ro.product.model": "AWS-TV-32-BL-02-A",
    "ro.product.device": "fakeboard",
    "ro.build.version.release": "14",
    "ro.build.version.sdk": "34",
    "ro.build.id": "UKNV.260602.001",
    "ro.config.low_ram": "true",
    "ro.serialno": "FAKE123SERIAL",
}

# name -> (system, state)
DEFAULT_PACKAGES = {
    "android": (True, "enabled"),
    "com.android.shell": (True, "enabled"),
    "com.android.systemui": (True, "enabled"),
    "com.android.vending": (True, "enabled"),
    "com.android.tv.settings": (True, "enabled"),
    "com.android.providers.tv": (True, "enabled"),
    "com.android.providers.settings": (True, "enabled"),
    "com.google.android.gms": (True, "enabled"),
    "com.google.android.gsf": (True, "enabled"),
    "com.google.android.permissioncontroller": (True, "enabled"),
    "com.google.android.bluetooth": (True, "enabled"),
    "com.google.android.leanback.ime": (True, "enabled"),
    "com.google.android.webview": (True, "enabled"),
    "com.google.android.apps.tv.launcherx": (True, "enabled"),
    "com.google.android.tungsten.setupwraith": (True, "enabled"),
    "com.google.android.tv.remote.service": (True, "enabled"),
    "com.mediatek.wwtv.tvcenter": (True, "enabled"),
    "com.google.android.youtube.tv": (True, "enabled"),
    "com.google.android.katniss": (True, "enabled"),
    "com.google.android.play.games": (True, "enabled"),
    "com.example.vendorstore": (True, "enabled"),
    "com.example.vendorads": (True, "enabled"),
    "com.example.alreadyoff": (True, "disabled"),
    "com.example.homebreaker": (True, "enabled"),
    "com.example.userapp": (False, "enabled"),
}


class FakeTv(AdbRunner):
    def __init__(self, packages=None, props=None, settings=None, serial="192.168.0.50:5555"):
        super().__init__(serial=serial, adb_path="/fake/adb")
        self.props = dict(DEFAULT_PROPS if props is None else props)
        self.packages = {k: list(v) for k, v in (packages or DEFAULT_PACKAGES).items()}
        self.settings = {"global": {"window_animation_scale": "1.0"}, "system": {}, "secure": {}}
        if settings:
            for ns, values in settings.items():
                self.settings[ns].update(values)
        self.launchers = {"com.google.android.apps.tv.launcherx": LAUNCHER}
        self.stock_launcher = "com.google.android.apps.tv.launcherx"
        self.preferred_home: str | None = None
        self.refuse_set_home = False
        # tvlauncher declares HOME priority 2: while enabled it beats the user's preference.
        self.stock_wins = False
        self.files: dict[str, str] = {}
        # Disabling one of these makes HOME disappear (simulates a hidden dependency).
        self.home_breakers = {"com.example.homebreaker"}
        self.refuse_disable: set[str] = set()
        self.state = "device"
        self.commands: list[str] = []

    # -- helpers for tests --
    def state_of(self, name):
        return self.packages[name][1]

    def _enabled(self, name):
        return name in self.packages and self.packages[name][1] == "enabled"

    def _home_ok(self):
        if any(self._enabled(b) is False and b in self.packages for b in self.home_breakers):
            return False
        return any(self._enabled(p) for p in self.launchers)

    def install_launcher(self, component=FLAUNCHER):
        pkg = component.split("/", 1)[0]
        self.packages[pkg] = [False, "enabled"]
        self.launchers[pkg] = component

    def _wizard_takes_home(self):
        # Seen on MT5867 sets: with the stock launcher gone, the setup wizard's
        # RecoveryActivity claims HOME over any other launcher.
        return self._enabled(WIZARD) and not self._enabled(self.stock_launcher)

    def _homes(self):
        if not self._home_ok():
            return [FALLBACK]
        homes = [c for p, c in self.launchers.items() if self._enabled(p)]
        if self._wizard_takes_home():
            homes.insert(0, RECOVERY)
        return homes + [FALLBACK]

    def _resolve_home(self):
        homes = [h for h in self._homes() if h != FALLBACK]
        if not homes:
            return FALLBACK
        if self._wizard_takes_home():
            return RECOVERY
        stock = self.launchers.get(self.stock_launcher)
        if self.stock_wins and stock in homes:
            return stock
        if self.preferred_home in homes:
            return self.preferred_home
        return homes[0] if len(homes) == 1 else "android/com.android.internal.app.ResolverActivity"

    # -- the only override --
    def _exec(self, args, timeout):
        args = list(args)
        if args[:1] == ["-s"]:
            args = args[2:]
        cmd = args[0]
        if cmd == "version":
            return AdbResult(0, "Android Debug Bridge version 1.0.41\n")
        if cmd == "connect":
            return AdbResult(0, f"connected to {args[1]}\n")
        if cmd == "devices":
            return AdbResult(0, f"List of devices attached\n{self.serial}\t{self.state}\n\n")
        if cmd == "get-state":
            return AdbResult(0, self.state + "\n")
        if cmd == "push":
            self.files[args[-1]] = Path(args[-2]).read_text(encoding="utf-8")
            return AdbResult(0, f"{args[-2]}: 1 file pushed.\n")
        if cmd == "install":
            self.installed_apks = getattr(self, "installed_apks", []) + [args[-1]]
            self.install_launcher(getattr(self, "apk_component", FLAUNCHER))
            return AdbResult(0, "Performing Streamed Install\nSuccess\n")
        if cmd == "shell":
            self.commands.append(args[1])
            return self._shell(args[1])
        return AdbResult(1, "", f"unknown command {cmd}")

    def _pm_list(self, flags):
        names = []
        for name, (system, state) in sorted(self.packages.items()):
            if "-u" not in flags and state == "uninstalled":
                continue
            if "-s" in flags and not system:
                continue
            if "-3" in flags and system:
                continue
            if "-d" in flags and state != "disabled":
                continue
            names.append(f"package:{name}")
        return AdbResult(0, "\n".join(names) + "\n")

    def _shell(self, line):
        parts = shlex.split(line)
        if parts[0] == "cat" and parts[1].startswith("/sdcard/"):
            if parts[1] not in self.files:
                return AdbResult(1, "", f"cat: {parts[1]}: No such file or directory")
            return AdbResult(0, self.files[parts[1]])
        if parts[:2] == ["rm", "-f"]:
            self.files.pop(parts[2], None)
            return AdbResult(0, "")
        if parts[0] == "mkdir":
            return AdbResult(0, "")
        if parts[:2] == ["am", "force-stop"]:
            self.force_stopped = getattr(self, "force_stopped", 0) + 1
            return AdbResult(0, "")
        if parts[0] == "getprop":
            return AdbResult(0, self.props.get(parts[1], "") + "\n")
        if parts[:2] == ["cat", "/proc/meminfo"]:
            return AdbResult(0, (FIXTURES / "meminfo.txt").read_text())
        if parts[0] == "df":
            return AdbResult(0, (FIXTURES / "df_toybox.txt").read_text())
        if parts[:2] == ["dumpsys", "meminfo"]:
            return AdbResult(0, (FIXTURES / "dumpsys_meminfo.txt").read_text())
        if parts[:3] == ["pm", "list", "packages"]:
            return self._pm_list(parts[3:])
        if parts[:2] == ["pm", "trim-caches"]:
            self.cache_trimmed = True
            return AdbResult(0, "")
        if parts[:2] == ["pm", "path"]:
            return AdbResult(0, "package:/system/framework/framework-res.apk\n")
        if "resolve-activity" in parts:
            return AdbResult(0, "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 "
                                f"isDefault=true\n{self._resolve_home()}\n")
        if parts[:2] == ["am", "start"]:
            comp = parts[-1]
            head = f"Starting: Intent {{ cmp={comp} }}\n"
            if comp in getattr(self, "crashing", set()) or comp not in self._homes():
                return AdbResult(0, head + "Error: Activity not started\n")
            return AdbResult(0, head + "Status: ok\nLaunchState: COLD\n")
        if "set-home-activity" in parts:
            comp = parts[-1]
            if self.refuse_set_home or comp not in self._homes():
                return AdbResult(255, "", f"Error: failed to set home activity {comp}")
            if not getattr(self, "preferred_home_dropped", False):
                self.preferred_home = comp
            return AdbResult(0, "Success\n")
        if "query-activities" in parts:
            out = "".join(f"priority=0 preferredOrder=0\n{c}\n" for c in self._homes())
            return AdbResult(0, out)
        if parts[:2] == ["pm", "disable-user"]:
            name = parts[-1]
            if name not in self.packages or self.packages[name][1] == "uninstalled":
                return AdbResult(255, "", f"Exception: Unknown package: {name}")
            if name in self.refuse_disable:
                return AdbResult(255, "", "Exception: java.lang.SecurityException: Shell "
                                          "cannot change component state")
            self.packages[name][1] = "disabled"
            return AdbResult(0, f"Package {name} new state: disabled-user\n")
        if parts[:2] == ["pm", "enable"]:
            name = parts[-1]
            if name not in self.packages or self.packages[name][1] == "uninstalled":
                return AdbResult(255, "", f"Exception: Unknown package: {name}")
            self.packages[name][1] = "enabled"
            return AdbResult(0, f"Package {name} new state: enabled\n")
        if parts[:2] == ["pm", "uninstall"]:
            name = parts[-1]
            self.packages[name][1] = "uninstalled"
            return AdbResult(0, "Success\n")
        if "install-existing" in parts:
            name = parts[-1]
            if name not in self.packages:
                return AdbResult(1, "", f"Failure [package {name} not found]")
            self.packages[name][1] = "enabled"
            return AdbResult(0, f"Package {name} installed for user: 0\n")
        if parts[0] == "settings":
            op, ns, key = parts[1], parts[2], parts[3]
            if op == "get":
                return AdbResult(0, self.settings[ns].get(key, "null") + "\n")
            if op == "put":
                self.settings[ns][key] = parts[4]
                return AdbResult(0, "")
            if op == "delete":
                existed = self.settings[ns].pop(key, None) is not None
                return AdbResult(0, f"Deleted {int(existed)} rows\n")
        if re.match(r"^(cmd|pm)\b", line):
            return AdbResult(255, "", "Error: unknown command")
        return AdbResult(127, "", f"/system/bin/sh: {parts[0]}: not found")
