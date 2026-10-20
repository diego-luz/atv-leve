"""Reading device information and diagnostics from the TV."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .adb import AdbRunner

HOME_INTENT = "-a android.intent.action.MAIN -c android.intent.category.HOME"
COMPONENT_RE = re.compile(r"^([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)/([A-Za-z0-9_.$]+)$")

# Components that answer the HOME intent but are not a usable launcher.
FAKE_HOME_CLASSES = ("FallbackHome", "ResolverActivity", "ChooserActivity")
# The setup wizard can claim HOME (RecoveryActivity) when the launcher is gone: not a home.
SETUP_WIZARD_MARKERS = ("setupwraith", "setupwizard")

INFO_PROPS = {
    "brand": "ro.product.brand",
    "manufacturer": "ro.product.manufacturer",
    "model": "ro.product.model",
    "device": "ro.product.device",
    "release": "ro.build.version.release",
    "sdk": "ro.build.version.sdk",
    "build_id": "ro.build.id",
    "low_ram": "ro.config.low_ram",
    "serialno": "ro.serialno",
}


@dataclass
class DeviceInfo:
    serial: str = ""
    brand: str = ""
    manufacturer: str = ""
    model: str = ""
    device: str = ""
    release: str = ""
    sdk: str = ""
    build_id: str = ""
    low_ram: str = ""
    serialno: str = ""
    mem_total_kb: int | None = None
    mem_available_kb: int | None = None
    data_total_kb: int | None = None
    data_free_kb: int | None = None
    home: str | None = None
    props: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def backup_id(self) -> str:
        """Stable, filesystem-safe identifier (ro.serialno survives IP changes)."""
        raw = self.serialno or self.serial or "desconhecido"
        return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


# -- parsers (pure functions, tested with fixtures) ---------------------------


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse /proc/meminfo into {key: kB}."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"^(\w+(?:\(\w+\))?):\s+(\d+)", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


_SIZE_RE = re.compile(r"^([\d.]+)([KMGT]?)$", re.I)
_UNITS = {"": 1, "K": 1, "M": 1024, "G": 1024**2, "T": 1024**3}


def _size_to_kb(value: str) -> int | None:
    m = _SIZE_RE.match(value)
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def parse_df(text: str) -> tuple[int | None, int | None]:
    """Return (total_kb, free_kb) for the last data line of `df` output.

    Handles toybox (`Filesystem 1K-blocks Used Available Use% Mounted on`), human
    readable sizes (`5.5G`) and the old toolbox format (`/data 5.5G 1.2G 4.3G 4096`).
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None, None
    header = lines[0].lower()
    parts = lines[-1].split()
    if len(lines) >= 3 and len(lines[-2].split()) == 1:
        # Long filesystem name wrapped to its own line.
        parts = [lines[-2].strip(), *parts]
    try:
        if "1k-blocks" in header or "1024-blocks" in header:
            # Filesystem 1K-blocks Used Available ...
            return int(parts[1]), int(parts[3])
        if "free" in header:
            # Old toolbox: Filesystem Size Used Free Blksize
            return _size_to_kb(parts[1]), _size_to_kb(parts[3])
        # Filesystem Size Used Avail ...
        return _size_to_kb(parts[1]), _size_to_kb(parts[3])
    except (IndexError, ValueError):
        return None, None


def parse_components(text: str) -> list[str]:
    """Extract 'pkg/cls' components from resolve-activity / query-activities output."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("name="):  # some versions print "name=pkg/cls"
            line = line[5:]
        m = COMPONENT_RE.match(line)
        if m:
            pkg, cls = m.groups()
            if cls.startswith("."):
                cls = pkg + cls
            comp = f"{pkg}/{cls}"
            if comp not in out:
                out.append(comp)
    return out


def is_real_home(component: str | None) -> bool:
    if not component:
        return False
    pkg, _, cls = component.partition("/")
    if any(m in pkg.lower() for m in SETUP_WIZARD_MARKERS):
        return False
    return not any(cls.endswith(fake) for fake in FAKE_HOME_CLASSES)


def component_package(component: str) -> str:
    return component.split("/", 1)[0]


# -- device queries ------------------------------------------------------------


def resolve_home(adb: AdbRunner) -> str | None:
    """Return the component that currently answers the HOME intent, or None."""
    for prefix in ("cmd package", "pm"):
        result = adb.shell(f"{prefix} resolve-activity --brief {HOME_INTENT}")
        if result.returncode == 0 and "No activity found" not in result.output:
            comps = parse_components(result.stdout)
            if comps:
                return comps[-1]
        if "No activity found" in result.output:
            return None
    return None


def list_home_activities(adb: AdbRunner) -> list[str]:
    """All enabled activities that can act as HOME (including fake ones)."""
    for prefix in ("cmd package", "pm"):
        result = adb.shell(f"{prefix} query-activities --brief {HOME_INTENT}")
        if result.returncode == 0:
            comps = parse_components(result.stdout)
            if comps:
                return comps
    return []


def home_packages(adb: AdbRunner) -> set[str]:
    """Packages that provide a real launcher, plus the current one."""
    pkgs = {component_package(c) for c in list_home_activities(adb) if is_real_home(c)}
    current = resolve_home(adb)
    if current and is_real_home(current):
        pkgs.add(component_package(current))
    return pkgs


def get_info(adb: AdbRunner) -> DeviceInfo:
    info = DeviceInfo(serial=adb.serial or "")
    for attr, prop in INFO_PROPS.items():
        value = adb.getprop(prop)
        setattr(info, attr, value)
        info.props[prop] = value

    mem = parse_meminfo(adb.shell_out("cat /proc/meminfo"))
    info.mem_total_kb = mem.get("MemTotal")
    info.mem_available_kb = mem.get("MemAvailable", mem.get("MemFree"))

    for cmd in ("df -k /data", "df /data"):
        total, free = parse_df(adb.shell_out(cmd))
        if total is not None:
            info.data_total_kb, info.data_free_kb = total, free
            break

    info.home = resolve_home(adb)
    return info


def fmt_kb(kb: int | None) -> str:
    if kb is None:
        return "desconhecido"
    if kb >= 1024**2:
        return f"{kb / 1024**2:.1f} GB"
    return f"{kb / 1024:.0f} MB"


# -- memory usage (atvleve top) ---------------------------------------------------


@dataclass
class ProcMem:
    name: str
    pid: int
    kb: int


def parse_dumpsys_meminfo(text: str) -> tuple[str, list[ProcMem]]:
    """Parse the 'Total PSS by process' section (falls back to RSS).

    Returns (metric, processes) where metric is 'PSS' or 'RSS'.
    """
    sections: dict[str, list[ProcMem]] = {}
    current: str | None = None
    line_re = re.compile(r"^\s*([\d,]+)K:\s+(\S+)\s+\(pid\s+(\d+)")
    for line in text.splitlines():
        header = re.match(r"^Total (PSS|RSS) by process", line.strip())
        if header:
            current = header.group(1)
            sections[current] = []
            continue
        if current is None:
            continue
        m = line_re.match(line)
        if m:
            sections[current].append(
                ProcMem(m.group(2), int(m.group(3)), int(m.group(1).replace(",", "")))
            )
        elif line.strip() and not line.startswith(" "):
            current = None
    for metric in ("PSS", "RSS"):
        if sections.get(metric):
            procs = sorted(sections[metric], key=lambda p: p.kb, reverse=True)
            return metric, procs
    return "", []


def top_memory(adb: AdbRunner) -> tuple[str, list[ProcMem]]:
    return parse_dumpsys_meminfo(adb.shell_out("dumpsys meminfo", timeout=90))
