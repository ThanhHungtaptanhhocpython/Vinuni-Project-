import pytest

from src.edge_vla.embodiment.calibration import CalibrationError, load_calibration
from src.edge_vla.models import JointName

MINIMAL_CALIBRATION = """
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


def test_load_calibration_reads_packaged_default_profile():
    calib = load_calibration("so101_follower_default")

    assert calib.profile == "so101_follower_default"
    assert calib.range_deg(JointName.SHOULDER_PAN) == (-110.0, 110.0)
    assert calib.home_position[JointName.ELBOW_FLEX] == 90.0


def test_load_calibration_missing_file_raises(tmp_path):
    with pytest.raises(CalibrationError):
        load_calibration("does_not_exist", calibration_dir=tmp_path)


def test_load_calibration_invalid_json_raises(tmp_path):
    (tmp_path / "broken.json").write_text("{not valid json")

    with pytest.raises(CalibrationError):
        load_calibration("broken", calibration_dir=tmp_path)


def test_load_calibration_missing_joint_raises(tmp_path):
    import json

    data = json.loads(MINIMAL_CALIBRATION)
    del data["joints"]["gripper"]
    (tmp_path / "incomplete.json").write_text(json.dumps(data))

    with pytest.raises(CalibrationError):
        load_calibration("incomplete", calibration_dir=tmp_path)


def test_load_calibration_custom_dir(tmp_path):
    (tmp_path / "test_profile.json").write_text(MINIMAL_CALIBRATION)

    calib = load_calibration("test_profile", calibration_dir=tmp_path)

    assert calib.range_deg(JointName.WRIST_ROLL) == (-90.0, 90.0)
