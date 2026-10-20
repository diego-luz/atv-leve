import shutil
from pathlib import Path

import pytest

from fake_tv import FakeTv

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tv():
    return FakeTv()


@pytest.fixture
def profiles_dir(tmp_path):
    """Real _generic.yaml plus the test profiles, isolated in tmp."""
    d = tmp_path / "profiles"
    d.mkdir()
    shutil.copy(ROOT / "profiles" / "_generic.yaml", d)
    for p in (FIXTURES / "profiles").glob("*.yaml"):
        shutil.copy(p, d)
    return d


@pytest.fixture
def env(monkeypatch, tmp_path, profiles_dir):
    """Point the CLI at isolated profiles and backup folders."""
    monkeypatch.setenv("ATVLEVE_PROFILES", str(profiles_dir))
    monkeypatch.setenv("ATVLEVE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ATVLEVE_IP", raising=False)
    return tmp_path
