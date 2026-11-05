import pytest


@pytest.fixture(autouse=True)
def no_codex_signed_in(tmp_path, monkeypatch):
    """A Codex signed in on the developer's machine must not change what a test sees."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
