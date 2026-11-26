import pytest

from src.edge_vla.config_loader import ConfigError, load_config, resolve_robot_mode

FULL_CONFIG_YAML = """
robot:
  mode: "sim"
  sim:
    task_suite: "libero_10"
    max_episode_steps: 400
  real:
    port: "/dev/ttyACM0"
    robot_id: "so101_follower"
    calibration_profile: "so101_follower_default"
    max_relative_target_deg: 15.0

cameras:
  top:
    device: "/dev/video0"
    resolution: [640, 480]
    fps: 30
  wrist:
    device: "/dev/video2"
    resolution: [640, 480]
    fps: 30

vla:
  policy: "act_so101_v3"
  checkpoint_path: "/opt/robot-agent/models/act_so101_v3.ckpt"
  device: "cuda:0"
  inference_fps_target: 30
  default_max_steps: 400
  default_timeout_s: 60

safety:
  temperature_warn_c: 55.0
  temperature_abort_c: 65.0
  current_abort_ma: 1500.0
  load_warn_percent: 75.0
  heartbeat_abort_after_fails: 4
  angle_margin_deg: 2.0

tunnel:
  cloud_url: "wss://api.robot-agent.example.com/edge/tunnel"
  heartbeat_interval_s: 5

logging:
  level: "INFO"
  local_retention_days: 7
  batch_interval_s: 2
  batch_max_entries: 200
"""


def test_resolve_robot_mode_defaults_to_real():
    assert resolve_robot_mode({}, env={}) == "real"


def test_resolve_robot_mode_uses_config_yaml_when_no_env():
    config = {"robot": {"mode": "sim"}}
    assert resolve_robot_mode(config, env={}) == "sim"


def test_resolve_robot_mode_env_overrides_config():
    config = {"robot": {"mode": "real"}}
    assert resolve_robot_mode(config, env={"ROBOT_MODE": "sim"}) == "sim"


def test_resolve_robot_mode_rejects_invalid_env_value():
    with pytest.raises(ConfigError):
        resolve_robot_mode({}, env={"ROBOT_MODE": "fake"})


def test_load_config_reads_yaml_once(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(FULL_CONFIG_YAML)
    env_path = tmp_path / ".env"
    env_path.write_text("")

    config = load_config(config_path, env_path)

    assert config.robot.mode == "sim"
    assert config.robot.sim.task_suite == "libero_10"
    assert config.robot.sim.max_episode_steps == 400
    assert config.robot.real.port == "/dev/ttyACM0"
    assert config.robot.real.calibration_profile == "so101_follower_default"
    assert config.cameras["top"].resolution == (640, 480)
    assert config.cameras["wrist"].fps == 30
    assert config.vla.policy == "act_so101_v3"
    assert config.safety.temperature_abort_c == 65.0
    assert config.tunnel.heartbeat_interval_s == 5
    assert config.logging.level == "INFO"


def test_load_config_env_overrides_yaml_mode(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(FULL_CONFIG_YAML)
    env_path = tmp_path / ".env"
    env_path.write_text("ROBOT_MODE=real\n")

    config = load_config(config_path, env_path)

    assert config.robot.mode == "real"


def test_load_config_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml", tmp_path / ".env")


def test_load_config_defaults_real_block_when_missing(tmp_path):
    """`robot.real` khuyết trong YAML vẫn phải load được — dùng default trong config_loader."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(FULL_CONFIG_YAML.replace(
        'real:\n    port: "/dev/ttyACM0"\n    robot_id: "so101_follower"\n'
        '    calibration_profile: "so101_follower_default"\n    max_relative_target_deg: 15.0\n',
        "",
    ))
    env_path = tmp_path / ".env"
    env_path.write_text("")

    config = load_config(config_path, env_path)

    assert config.robot.real.port == "/dev/ttyACM0"
    assert config.robot.real.calibration_profile == "so101_follower_default"
