"""Regression tests for the local development launchers."""

from __future__ import annotations

import os
import subprocess

import pytest

from scripts import run as local_run


@pytest.fixture
def isolated_launcher_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep launcher configuration tests independent of the developer's .env."""
    for name in (
        "DEBUG",
        "FRONTEND_PORT",
        "HOST",
        "PORT",
        "REACT_APP_API_BASE_URL",
        "REDIS_ENABLED",
        "REDIS_URL",
        "STARTUP_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(local_run, "_DOTENV_VALUES", {})


def test_load_config_uses_distinct_api_and_frontend_ports(
    monkeypatch: pytest.MonkeyPatch, isolated_launcher_config: None
) -> None:
    """An invalid host-shell DEBUG value cannot poison the backend child process."""
    monkeypatch.setattr(local_run, "_DOTENV_VALUES", {"DEBUG": "false"})
    monkeypatch.setenv("DEBUG", "release")
    monkeypatch.setenv("PORT", "5101")
    monkeypatch.setenv("FRONTEND_PORT", "3101")
    monkeypatch.setenv("REACT_APP_API_BASE_URL", "http://localhost:5101/api")
    monkeypatch.setenv("REDIS_ENABLED", "false")

    config = local_run.load_config()

    assert config.backend_url == "http://localhost:5101"
    assert config.frontend_url == "http://localhost:3101"
    assert config.api_base_url == "http://localhost:5101/api"
    assert config.debug is False
    assert config.redis_enabled is False
    assert config.startup_mode == "full"
    assert local_run._backend_env(config)["DEBUG"] == "false"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("PORT", "not-a-port", "PORT must be an integer"),
        ("FRONTEND_PORT", "70000", "FRONTEND_PORT must be between 1 and 65535"),
    ],
)
def test_load_config_rejects_invalid_ports(
    monkeypatch: pytest.MonkeyPatch,
    isolated_launcher_config: None,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit, match=message):
        local_run.load_config()


def test_load_config_rejects_shared_api_and_frontend_port(
    monkeypatch: pytest.MonkeyPatch, isolated_launcher_config: None
) -> None:
    monkeypatch.setenv("PORT", "5101")
    monkeypatch.setenv("FRONTEND_PORT", "5101")

    with pytest.raises(SystemExit, match="PORT and FRONTEND_PORT must use different values"):
        local_run.load_config()


def test_load_config_rejects_unknown_startup_mode(
    monkeypatch: pytest.MonkeyPatch, isolated_launcher_config: None
) -> None:
    monkeypatch.setenv("STARTUP_MODE", "offline")

    with pytest.raises(SystemExit, match="STARTUP_MODE must be 'full' or 'api'"):
        local_run.load_config()


def test_api_mode_skips_background_workers(capsys: pytest.CaptureFixture[str]) -> None:
    config = local_run.LocalConfig(
        host="0.0.0.0",
        port=5101,
        frontend_port=3101,
        debug=False,
        startup_mode="api",
        api_base_url="http://localhost:5101/api",
        redis_url="redis://localhost:6379/0",
        redis_enabled=True,
    )

    assert local_run.start_workers(config) == []
    assert "STARTUP_MODE=api — skipping background workers." in capsys.readouterr().out


def test_shell_launcher_rejects_shared_api_and_frontend_port() -> None:
    """Keep the shell launcher aligned with the Python launcher's validation."""
    environment = {
        **os.environ,
        "PORT": "5101",
        "FRONTEND_PORT": "5101",
    }
    completed = subprocess.run(
        ["bash", "start.sh"],
        cwd=local_run.ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "PORT and FRONTEND_PORT must use different values." in completed.stderr
