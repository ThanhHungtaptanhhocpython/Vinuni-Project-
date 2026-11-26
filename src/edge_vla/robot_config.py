"""Mode and rollout config loading for real SO-101 operation."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml


class ConfigError(ValueError):
    """Invalid Edge VLA runtime configuration."""


def resolve_robot_mode(env: Mapping[str, str] = os.environ) -> Literal["sim", "real"]:
    mode = env.get("ROBOT_MODE", "sim").strip().lower()
    if mode not in ("sim", "real"):
        raise ConfigError(f"ROBOT_MODE không hợp lệ: {mode!r}; phải là 'sim' hoặc 'real'")
    return mode


def _default_config_path() -> Path:
    return Path(__file__).with_name("robot_config.yaml")


def load_rollout_config(path: str | None = None):
    """Load a lerobot RolloutConfig through lerobot's own config parser path."""
    import draccus
    import lerobot.scripts.lerobot_rollout  # noqa: F401
    from lerobot.configs import parser
    from lerobot.configs.parser import extract_path_fields_from_config
    from lerobot.rollout.configs import RolloutConfig
    from lerobot.utils.import_utils import register_third_party_plugins

    register_third_party_plugins()

    config_path = Path(path or os.environ.get("EDGE_VLA_ROBOT_CONFIG", "") or _default_config_path())
    if not config_path.exists():
        raise ConfigError(f"Không tìm thấy robot config: {config_path}")

    with config_path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Robot config phải là YAML object: {config_path}")

    policy_path = os.environ.get("VLA_POLICY_PATH")
    if policy_path:
        policy = data.setdefault("policy", {})
        if not isinstance(policy, dict):
            raise ConfigError("Khối policy trong robot config phải là object")
        policy["path"] = policy_path

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        yaml.safe_dump(data, tmp, sort_keys=False)
        tmp_path = tmp.name

    cleaned = tmp_path
    try:
        # lerobot 0.6.1 exposes the path-field hook with the draccus dunder name.
        path_fields = RolloutConfig.__get_path_fields__()
        cleaned = extract_path_fields_from_config(tmp_path, path_fields)
        return draccus.parse(config_class=RolloutConfig, config_path=cleaned, args=[])
    finally:
        parser._config_path_args.pop("policy", None)  # type: ignore[attr-defined]
        parser._config_yaml_overrides.pop("policy", None)  # type: ignore[attr-defined]
        Path(tmp_path).unlink(missing_ok=True)
        if cleaned != tmp_path:
            Path(cleaned).unlink(missing_ok=True)
