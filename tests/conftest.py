import pytest

from companyscout import config, db


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_HOME", str(tmp_path))
    monkeypatch.setenv("OUTREACH_PROVIDER", "mock")
    monkeypatch.setenv("OUTREACH_GMAIL", "mock")
    monkeypatch.setenv("OUTREACH_NOTIFY", "false")
    monkeypatch.setenv("COLUMNS", "220")  # keep Rich tables from wrapping names in captured output
    return tmp_path


@pytest.fixture
def cfg(home):
    return config.load()


@pytest.fixture
def session(home):
    s = db.connect()
    yield s
    s.close()
