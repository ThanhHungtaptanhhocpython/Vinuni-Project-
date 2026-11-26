"""Dựng `Config` hợp lệ tối thiểu cho test — tránh mỗi file test tự lặp lại việc này.

`RobotConfig` giờ có 2 khối con `sim`/`real` (xem `config_loader.py`); test tool chỉ cần đọc
được field nào tool đó dùng tới (`sim.task_suite`, `real.port`, `real.calibration_profile`,
...), không cần đầy đủ mọi field như production config.
"""

from __future__ import annotations

from src.edge_vla.config_loader import (
    Config,
    LoggingConfig,
    RealConfig,
    RobotConfig,
    SafetyConfig,
    SimConfig,
    TunnelConfig,
    VlaConfig,
)

CALIBRATION_PROFILE = "so101_follower_default"

DEFAULT_SAFETY_CONFIG = SafetyConfig(
    temperature_warn_c=55.0,
    temperature_abort_c=65.0,
    current_abort_ma=1500.0,
    load_warn_percent=75.0,
    heartbeat_abort_after_fails=3,
    angle_margin_deg=2.0,
)


def fake_config(
    *,
    mode: str = "real",
    policy: str = "fake_policy",
    device: str = "cpu",
    safety: SafetyConfig | None = None,
    task_suite: str = "libero_10",
) -> Config:
    return Config(
        robot=RobotConfig(
            mode=mode,  # type: ignore[arg-type]
            sim=SimConfig(task_suite=task_suite, max_episode_steps=None),
            real=RealConfig(
                port="/dev/ttyACM0",
                robot_id="so101_follower",
                calibration_profile=CALIBRATION_PROFILE,
                max_relative_target_deg=15.0,
            ),
        ),
        cameras={},
        vla=VlaConfig(
            policy=policy,
            checkpoint_path="/unused",
            device=device,
            inference_fps_target=50,
            default_max_steps=400,
            default_timeout_s=60,
        ),
        safety=safety or DEFAULT_SAFETY_CONFIG,
        tunnel=TunnelConfig(cloud_url="wss://example.invalid/edge/tunnel", heartbeat_interval_s=5),
        logging=LoggingConfig(level="INFO", local_retention_days=7, batch_interval_s=2, batch_max_entries=200),
    )
