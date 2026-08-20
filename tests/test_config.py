from pathlib import Path

import pytest

from perpmirror.config import load_config
from perpmirror.exceptions import ConfigurationError


def test_example_config_defaults_to_dry_run(monkeypatch, tmp_path: Path) -> None:
    source = Path("config.example.yaml").read_text()
    config = tmp_path / "config.yaml"
    config.write_text(source)
    monkeypatch.setenv("LEADER_API_KEY", "x")
    monkeypatch.setenv("LEADER_SECRET_KEY", "x")
    monkeypatch.setenv("FOLLOWER1_API_KEY", "x")
    monkeypatch.setenv("FOLLOWER1_SECRET_KEY", "x")
    monkeypatch.setenv("FOLLOWER2_API_KEY", "x")
    monkeypatch.setenv("FOLLOWER2_SECRET_KEY", "x")
    monkeypatch.setenv("FOLLOWER2_PASSPHRASE", "x")
    settings = load_config(config, tmp_path / ".env")
    assert settings.app.dry_run is True


def test_live_requires_explicit_environment_ack(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(Path("config.example.yaml").read_text().replace("dry_run: true", "dry_run: false"))
    monkeypatch.delenv("PERPMIRROR_LIVE_ACK", raising=False)
    with pytest.raises(ConfigurationError, match="LIVE refused"):
        load_config(config, tmp_path / ".env")


def test_cli_force_dry_run_overrides_live_before_gate(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(Path("config.example.yaml").read_text().replace("dry_run: true", "dry_run: false"))
    monkeypatch.delenv("PERPMIRROR_LIVE_ACK", raising=False)
    settings = load_config(config, tmp_path / ".env", force_dry_run=True)
    assert settings.app.dry_run is True
