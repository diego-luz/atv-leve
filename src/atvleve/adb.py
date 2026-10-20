"""Single point of contact with the `adb` binary.

Every adb call in atvleve goes through `AdbRunner`. Tests replace it with a fake
that overrides `_exec`, so nothing else in the code base spawns processes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

DEFAULT_PORT = 5555
DEFAULT_TIMEOUT = 30

# Package names are interpolated into shell commands, so they must be strictly validated.
PACKAGE_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")
SETTING_KEY_RE = re.compile(r"^[A-Za-z0-9_.]+$")
SETTING_NAMESPACES = ("global", "system", "secure")

INSTALL_HELP = """\
O programa [bold]adb[/bold] (Android platform-tools) não foi encontrado no PATH.

[bold]Linux (Debian/Ubuntu):[/bold]  sudo apt install adb
[bold]Linux (Fedora):[/bold]         sudo dnf install android-tools
[bold]macOS (Homebrew):[/bold]       brew install android-platform-tools
[bold]Windows:[/bold]
  1. Baixe o "SDK Platform-Tools for Windows" em
     https://developer.android.com/tools/releases/platform-tools
  2. Descompacte em C:\\platform-tools
  3. Adicione C:\\platform-tools ao PATH (ou rode o ATV Leve de dentro dessa pasta)

Depois feche e abra o terminal e rode [bold]adb version[/bold] para conferir.
"""


class AdbError(Exception):
    """Base error for adb problems. Messages are user-facing (pt-BR)."""


class AdbNotFoundError(AdbError):
    pass


class AdbDeviceError(AdbError):
    """No device, more than one device, unauthorized or offline."""


class AdbTimeoutError(AdbError):
    pass


@dataclass
class AdbResult:
    returncode: int
    stdout: str
    stderr: str = ""

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()

    @property
    def ok(self) -> bool:
        """True when the command succeeded.

        Many `pm`/`cmd` commands print errors with exit code 0, so the text is checked too.
        """
        if self.returncode != 0:
            return False
        text = self.output
        return not any(m in text for m in ("Error:", "Exception", "Failure", "Unknown package"))


def validate_package(name: str) -> str:
    if not PACKAGE_RE.match(name or ""):
        raise ValueError(f"nome de pacote inválido: {name!r}")
    return name


def validate_setting(namespace: str, key: str) -> None:
    if namespace not in SETTING_NAMESPACES:
        raise ValueError(f"namespace de settings inválido: {namespace!r}")
    if not SETTING_KEY_RE.match(key or ""):
        raise ValueError(f"chave de settings inválida: {key!r}")


def normalize_host(ip: str) -> str:
    """'192.168.0.10' -> '192.168.0.10:5555'."""
    ip = ip.strip()
    if ":" in ip:
        return ip
    return f"{ip}:{DEFAULT_PORT}"


class AdbRunner:
    def __init__(
        self,
        serial: str | None = None,
        adb_path: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        verbose: bool = False,
        log=None,
    ):
        self.serial = serial
        self.adb_path = adb_path
        self.timeout = timeout
        self.verbose = verbose
        self.log = log or (lambda msg: None)

    # -- low level -------------------------------------------------------

    def find_adb(self) -> str:
        if self.adb_path:
            return self.adb_path
        path = shutil.which("adb")
        if not path:
            raise AdbNotFoundError(INSTALL_HELP)
        self.adb_path = path
        return path

    def _exec(self, args: list[str], timeout: int) -> AdbResult:
        """The only place where a process is spawned."""
        cmd = [self.find_adb(), *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbTimeoutError(
                f"O adb demorou mais de {timeout}s para responder ({' '.join(args)})."
            ) from exc
        except FileNotFoundError as exc:
            raise AdbNotFoundError(INSTALL_HELP) from exc
        return AdbResult(proc.returncode, proc.stdout, proc.stderr)

    def run(self, *args: str, timeout: int | None = None, device: bool = True) -> AdbResult:
        full = list(args)
        if device and self.serial:
            full = ["-s", self.serial, *full]
        if self.verbose:
            self.log(f"[dim]$ adb {' '.join(full)}[/dim]")
        result = self._exec(full, timeout or self.timeout)
        if device:
            self._raise_for_device_error(result)
        if self.verbose and result.output:
            self.log(f"[dim]{result.output[:2000]}[/dim]")
        return result

    @staticmethod
    def _raise_for_device_error(result: AdbResult) -> None:
        err = result.stderr.lower()
        if "unauthorized" in err:
            raise AdbDeviceError(
                "A TV ainda não autorizou este computador. Olhe a tela da TV e aceite o "
                "pedido \"Permitir depuração\" (marque \"Sempre permitir\")."
            )
        if "device offline" in err:
            raise AdbDeviceError(
                "A TV aparece como offline. Rode `adb disconnect` e conecte de novo; se "
                "persistir, desligue e ligue a depuração na TV."
            )
        if "more than one device" in err:
            raise AdbDeviceError(
                "Há mais de um aparelho conectado. Use --serial (veja `adb devices`) ou --ip."
            )
        if "no devices" in err or ("device" in err and "not found" in err):
            raise AdbDeviceError(
                "Nenhuma TV conectada. Use --ip IP_DA_TV ou rode `atvleve doctor --ip IP_DA_TV`."
            )

    # -- helpers -----------------------------------------------------------

    def shell(self, command: str, timeout: int | None = None) -> AdbResult:
        return self.run("shell", command, timeout=timeout)

    def shell_out(self, command: str, timeout: int | None = None) -> str:
        """Run a shell command and return stdout, or '' if it failed."""
        result = self.shell(command, timeout=timeout)
        return result.stdout if result.returncode == 0 else ""

    def version(self) -> str:
        result = self.run("version", device=False)
        return result.stdout.splitlines()[0] if result.stdout else ""

    def push(self, local: str, remote: str) -> AdbResult:
        return self.run("push", local, remote, timeout=60)

    def install(self, apk_path: str) -> AdbResult:
        """`adb install -r` of a file the user already has; nothing is downloaded."""
        return self.run("install", "-r", apk_path, timeout=180)

    def connect(self, host: str) -> AdbResult:
        return self.run("connect", normalize_host(host), device=False, timeout=20)

    def pair(self, host: str, code: str) -> AdbResult:
        return self.run("pair", host, code, device=False, timeout=30)

    def devices(self) -> list[tuple[str, str]]:
        result = self.run("devices", device=False)
        out = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                out.append((parts[0], parts[1]))
        return out

    def get_state(self) -> str:
        """'device', 'offline', 'unauthorized' or '' when unreachable."""
        try:
            result = self.run("get-state", timeout=10)
        except AdbDeviceError as exc:
            return "unauthorized" if "autorizou" in str(exc) else ""
        return result.stdout.strip()

    def getprop(self, key: str) -> str:
        if not SETTING_KEY_RE.match(key):
            raise ValueError(f"propriedade inválida: {key!r}")
        return self.shell_out(f"getprop {key}").strip()
