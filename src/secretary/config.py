"""Configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Auto-load .env from project root (silently skip if python-dotenv not installed)
try:
    from dotenv import load_dotenv

    _PROJECT_ROOT = Path(__file__).parent.parent.parent
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "default.yaml"
_DATA_DIR = os.environ.get("SECRETARY_DATA_DIR", "")


def _build_goals_db() -> str:
    return os.path.join(_DATA_DIR, "goals.db") if _DATA_DIR else ""


def _build_tasks_db() -> str:
    return os.path.join(os.path.dirname(_DATA_DIR), "databases", "tasks.db") if _DATA_DIR else ""


def _build_obsidian_vault() -> str:
    return os.environ.get("SECRETARY_OBSIDIAN_VAULT", "")


def _build_portfolio_path() -> str:
    return os.environ.get("SECRETARY_PORTFOLIO_PATH", "")


def _build_checkpoint_file() -> str:
    return os.path.join(_DATA_DIR, "last_checkpoint.jsonl") if _DATA_DIR else ""


@dataclass
class DataConfig:
    goals_db: str = field(default_factory=_build_goals_db)
    tasks_db: str = field(default_factory=_build_tasks_db)
    obsidian_vault: str = field(default_factory=_build_obsidian_vault)
    portfolio_path: str = field(default_factory=_build_portfolio_path)
    checkpoint_file: str = field(default_factory=_build_checkpoint_file)


@dataclass
class MonitorThresholds:
    memory_percent: float = 95.0
    cpu_percent: float = 95.0
    disk_percent: float = 95.0
    cpu_window_minutes: int = 5


@dataclass
class MonitorConfig:
    thresholds: MonitorThresholds = field(default_factory=MonitorThresholds)
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NotifyChannelConfig:
    adapter: str = "hermes"
    host: str = ""
    port: int = 465
    user: str = ""


@dataclass
class NotifyConfig:
    channels: dict[str, NotifyChannelConfig] = field(default_factory=dict)
    rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResourceGuardConfig:
    check_before: list[str] = field(default_factory=lambda: ["llm_call", "heavy_computation"])
    backoff_initial: int = 600
    backoff_multiplier: int = 2
    backoff_max: int = 9600


@dataclass
class EngineConfig:
    resource_guard: ResourceGuardConfig = field(default_factory=ResourceGuardConfig)


@dataclass
class HarnessConfig:
    default: str = "hermes"
    hermes: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    tick_interval: int = 30
    data: DataConfig = field(default_factory=DataConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    harness: HarnessConfig = field(default_factory=HarnessConfig)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _env_override(config: dict) -> dict:
    """Apply environment variable overrides."""
    env_map = {
        "SECRETARY_GOALS_DB": ("data", "goals_db"),
        "SECRETARY_TASKS_DB": ("data", "tasks_db"),
        "SECRETARY_TICK_INTERVAL": ("tick_interval",),
        "SMTP_PASSWORD": None,  # handled separately
    }
    for env_key, path in env_map.items():
        value = os.environ.get(env_key)
        if value and path:
            d = config
            for key in path[:-1]:
                d = d.setdefault(key, {})
            if path[-1] == "tick_interval":
                d[path[-1]] = int(value)
            else:
                d[path[-1]] = value
    return config


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from YAML file with env overrides."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH

    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

    # Extract secretary section
    raw = raw.get("secretary", raw)
    raw = _env_override(raw)

    # Build Config with nested dataclasses
    data_cfg = DataConfig(**raw.get("data", {}))

    monitor_raw = raw.get("monitor", {})
    thresholds_raw = monitor_raw.get("thresholds", {})
    monitor_cfg = MonitorConfig(
        thresholds=MonitorThresholds(**thresholds_raw),
        checks=monitor_raw.get("checks", []),
    )

    notify_raw = raw.get("notify", {})
    channels = {
        k: NotifyChannelConfig(**v) if isinstance(v, dict) else NotifyChannelConfig(adapter=v)
        for k, v in notify_raw.get("channels", {}).items()
    }
    notify_cfg = NotifyConfig(channels=channels, rules=notify_raw.get("rules", []))

    engine_raw = raw.get("engine", {})
    rg_raw = engine_raw.get("resource_guard", {})
    engine_cfg = EngineConfig(resource_guard=ResourceGuardConfig(**rg_raw))

    harness_raw = raw.get("harness", {})
    harness_cfg = HarnessConfig(
        default=harness_raw.get("default", "hermes"),
        hermes=harness_raw.get("hermes", {}),
    )

    return Config(
        tick_interval=raw.get("tick_interval", 30),
        data=data_cfg,
        monitor=monitor_cfg,
        notify=notify_cfg,
        engine=engine_cfg,
        harness=harness_cfg,
    )
