from dataclasses import replace

from src.edge_vla.config_loader import SafetyConfig
from src.edge_vla.embodiment.calibration import load_calibration
from src.edge_vla.embodiment.safety import SafetyMonitor
from src.edge_vla.models import JointName, JointState

CALIBRATION_JSON = """
{
  "profile": "test_profile",
  "joints": {
    "shoulder_pan":  { "raw_min": 0, "raw_max": 4095, "deg_min": -90.0, "deg_max": 90.0, "center_offset": 2048 },
    "shoulder_lift": { "raw_min": 0, "raw_max": 4095, "deg_min": -90.0, "deg_max": 90.0, "center_offset": 2048 },
    "elbow_flex":    { "raw_min": 0, "raw_max": 4095, "deg_min": -90.0, "deg_max": 90.0, "center_offset": 2048 },
    "wrist_flex":    { "raw_min": 0, "raw_max": 4095, "deg_min": -90.0, "deg_max": 90.0, "center_offset": 2048 },
    "wrist_roll":    { "raw_min": 0, "raw_max": 4095, "deg_min": -90.0, "deg_max": 90.0, "center_offset": 2048 },
    "gripper":       { "raw_min": 0, "raw_max": 4095, "deg_min": -90.0, "deg_max": 90.0, "center_offset": 2048 }
  },
  "home_position": {
    "shoulder_pan": 0.0, "shoulder_lift": 0.0, "elbow_flex": 0.0,
    "wrist_flex": 0.0, "wrist_roll": 0.0, "gripper": 0.0
  }
}
"""

SAFETY_CONFIG = SafetyConfig(
    temperature_warn_c=55.0,
    temperature_abort_c=65.0,
    current_abort_ma=1500.0,
    load_warn_percent=75.0,
    heartbeat_abort_after_fails=3,
    angle_margin_deg=2.0,
)

DEFAULT_JOINT_STATE = JointState(
    name=JointName.SHOULDER_PAN,
    motor_id=1,
    present_position=0.0,
    goal_position=None,
    present_speed=0.0,
    present_load=0.0,
    present_current=0.0,
    present_voltage=12.0,
    present_temperature=25.0,
    torque_enabled=True,
    moving=False,
    in_limit=True,
    error_flags=[],
)


class _AbortRecorder:
    """Thay cho `SharedState` trong API cũ: `SafetyMonitor` giờ chỉ gọi một callback, còn
    "abort nghĩa là gì" do người dựng quyết định (`main.py` nối vào đường E-Stop thật)."""

    def __init__(self) -> None:
        self.reasons: list[str] = []

    def __call__(self, reason: str) -> None:
        self.reasons.append(reason)

    @property
    def aborted(self) -> bool:
        return bool(self.reasons)

    @property
    def flags(self) -> set[str]:
        return {reason.split(":")[0] for reason in self.reasons}


def _make_monitor(tmp_path):
    (tmp_path / "test_profile.json").write_text(CALIBRATION_JSON)
    calib = load_calibration("test_profile", calibration_dir=tmp_path)
    recorder = _AbortRecorder()
    return SafetyMonitor(SAFETY_CONFIG, calib, recorder), recorder


def test_check_joint_within_limits_does_not_trigger_abort(tmp_path):
    monitor, aborts = _make_monitor(tmp_path)

    monitor.check_joint(JointName.SHOULDER_PAN, DEFAULT_JOINT_STATE)

    assert not aborts.aborted
    assert aborts.flags == set()


def test_check_joint_overheat_triggers_emergency_abort(tmp_path):
    monitor, aborts = _make_monitor(tmp_path)
    hot = replace(DEFAULT_JOINT_STATE, present_temperature=70.0)

    monitor.check_joint(JointName.SHOULDER_PAN, hot)

    assert aborts.aborted
    assert "OVERHEAT" in aborts.flags


def test_check_joint_temperature_warn_does_not_abort(tmp_path):
    monitor, aborts = _make_monitor(tmp_path)
    warm = replace(DEFAULT_JOINT_STATE, present_temperature=60.0)

    monitor.check_joint(JointName.SHOULDER_PAN, warm)

    assert not aborts.aborted


def test_check_joint_overcurrent_needs_500ms_consecutive_before_abort(tmp_path):
    monitor, aborts = _make_monitor(tmp_path)
    overcurrent = replace(DEFAULT_JOINT_STATE, present_current=2000.0)

    for _ in range(25):  # 25 * 20ms = 500ms, not yet > 500ms
        monitor.check_joint(JointName.SHOULDER_PAN, overcurrent)
    assert not aborts.aborted

    monitor.check_joint(JointName.SHOULDER_PAN, overcurrent)  # 26th -> 520ms, > 500ms triggers
    assert aborts.aborted
    assert "OVERCURRENT" in aborts.flags


def test_check_joint_overcurrent_resets_when_current_drops(tmp_path):
    monitor, aborts = _make_monitor(tmp_path)
    overcurrent = replace(DEFAULT_JOINT_STATE, present_current=2000.0)

    for _ in range(25):
        monitor.check_joint(JointName.SHOULDER_PAN, overcurrent)
    monitor.check_joint(JointName.SHOULDER_PAN, DEFAULT_JOINT_STATE)  # current drops back
    for _ in range(25):
        monitor.check_joint(JointName.SHOULDER_PAN, overcurrent)

    assert not aborts.aborted


def test_check_joint_overload_flag_triggers_abort(tmp_path):
    monitor, aborts = _make_monitor(tmp_path)
    overloaded = replace(DEFAULT_JOINT_STATE, error_flags=["OVERLOAD"])

    monitor.check_joint(JointName.SHOULDER_PAN, overloaded)

    assert aborts.aborted
    assert "OVERLOAD" in aborts.flags


def test_clamp_goal_within_range_unchanged(tmp_path):
    monitor, _ = _make_monitor(tmp_path)

    assert monitor.clamp_goal(JointName.SHOULDER_PAN, 10.0) == 10.0


def test_clamp_goal_clamps_to_margin(tmp_path):
    monitor, _ = _make_monitor(tmp_path)

    assert monitor.clamp_goal(JointName.SHOULDER_PAN, 200.0) == 88.0  # 90 - 2 margin
    assert monitor.clamp_goal(JointName.SHOULDER_PAN, -200.0) == -88.0
