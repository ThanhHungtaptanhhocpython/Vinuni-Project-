from src.edge_vla.main import bootstrap
from src.edge_vla.models import RobotStatus

FULL_CONFIG_YAML = """
robot:
  mode: "sim"
  sim:
    task_suite: "libero_10"
    max_episode_steps: null
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


def test_bootstrap_loads_config_and_starts_idle(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(FULL_CONFIG_YAML)
    env_path = tmp_path / ".env"
    env_path.write_text("")

    monkeypatch.setenv("ROBOT_AGENT_CONFIG", str(config_path))
    monkeypatch.setenv("ROBOT_AGENT_ENV", str(env_path))
    monkeypatch.delenv("ROBOT_MODE", raising=False)

    config, state = bootstrap()

    assert config.robot.mode == "sim"
    assert config.robot.sim.task_suite == "libero_10"
    assert state.robot_status == RobotStatus.IDLE
    assert state.current_episode_id is None
