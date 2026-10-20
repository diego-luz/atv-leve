import pytest

from atvleve.adb import (
    AdbDeviceError,
    AdbNotFoundError,
    AdbResult,
    AdbRunner,
    normalize_host,
    validate_package,
)


def test_normalize_host():
    assert normalize_host("192.168.0.10") == "192.168.0.10:5555"
    assert normalize_host(" 10.0.0.2:37123 ") == "10.0.0.2:37123"


@pytest.mark.parametrize("name", ["android", "com.foo.bar", "com.foo_1.Bar2"])
def test_valid_packages(name):
    assert validate_package(name) == name


@pytest.mark.parametrize("name", ["", "com.foo;reboot", "com foo", "com.foo && rm", "../x", "a..b"])
def test_invalid_packages_rejected(name):
    with pytest.raises(ValueError):
        validate_package(name)


def test_result_ok_detects_errors_with_zero_exit():
    assert AdbResult(0, "Package x new state: disabled-user").ok
    assert not AdbResult(0, "Error: java.lang.IllegalArgumentException").ok
    assert not AdbResult(1, "fine").ok


def test_adb_not_found(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(AdbNotFoundError) as exc:
        AdbRunner().find_adb()
    assert "platform-tools" in str(exc.value)


class StubRunner(AdbRunner):
    def __init__(self, result):
        super().__init__(serial="x", adb_path="/fake/adb")
        self.result = result
        self.calls = []

    def _exec(self, args, timeout):
        self.calls.append(args)
        return self.result


def test_serial_is_passed():
    r = StubRunner(AdbResult(0, "ok"))
    r.shell("echo")
    assert r.calls[0] == ["-s", "x", "shell", "echo"]


@pytest.mark.parametrize(
    "stderr,word",
    [
        ("error: device unauthorized.", "autorizou"),
        ("error: device offline", "offline"),
        ("error: no devices/emulators found", "Nenhuma TV"),
        ("error: more than one device/emulator", "mais de um"),
    ],
)
def test_device_errors_are_translated(stderr, word):
    r = StubRunner(AdbResult(1, "", stderr))
    with pytest.raises(AdbDeviceError) as exc:
        r.shell("echo")
    assert word in str(exc.value)


def test_devices_parsing():
    out = "List of devices attached\n1.2.3.4:5555\tdevice\nabc\tunauthorized\n"
    r = StubRunner(AdbResult(0, out))
    assert r.devices() == [("1.2.3.4:5555", "device"), ("abc", "unauthorized")]
