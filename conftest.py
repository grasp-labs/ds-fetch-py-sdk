import pytest


@pytest.fixture(autouse=True)
def isolated_session(tmp_path, monkeypatch):
    """Keep tests away from the real session cache and credential variables."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in ("AIC_ENV", "AIC_TOKEN", "AIC_CLIENT_ID", "AIC_CLIENT_SECRET", "AIC_EMAIL", "AIC_PASSWORD", "AIC_MFA_CODE", "AIC_FRESH"):
        monkeypatch.delenv(name, raising=False)
