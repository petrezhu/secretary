"""Unit tests for config loading."""

from secretary.config import Config, load_config


def test_load_default_config():
    config = load_config()
    assert isinstance(config, Config)
    assert config.tick_interval == 30
    # goals_db is empty when SECRETARY_DATA_DIR is not set
    if config.data.goals_db:
        assert config.data.goals_db.endswith("goals.db")


def test_config_data_defaults():
    config = load_config()
    assert config.monitor.thresholds.memory_percent == 95.0
    assert config.monitor.thresholds.cpu_percent == 95.0
    assert config.engine.resource_guard.backoff_initial == 600


def test_config_harness():
    config = load_config()
    assert config.harness.default == "hermes"
