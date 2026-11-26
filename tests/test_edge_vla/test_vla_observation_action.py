"""`build_observation` giờ đọc thẳng từ `Embodiment` (một nguồn sự thật) thay vì ráp từ
`SharedState` — xem `vla_runtime/observation.py`."""

import numpy as np

from src.edge_vla.vla_runtime.action import sanity_check_action
from src.edge_vla.vla_runtime.observation import build_observation
from tests.test_edge_vla.fakes import FakeEmbodiment


def test_build_observation_reads_state_and_images_from_embodiment():
    embodiment = FakeEmbodiment()
    embodiment.positions = np.array([0.0, 5.0, 10.0, 15.0, 20.0, 25.0], dtype=np.float32)

    obs = build_observation(embodiment, "pick the red block")

    assert obs.instruction == "pick the red block"
    assert obs.state.shape == (6,)
    assert obs.state.tolist() == [0.0, 5.0, 10.0, 15.0, 20.0, 25.0]
    assert set(obs.images) == {"top", "wrist"}
    assert obs.images["top"].shape == (240, 320, 3)


def test_build_observation_forwards_raw_observation_untouched():
    """`raw` là đường thoát cho adapter policy của LIBERO — phải đi qua nguyên vẹn."""
    embodiment = FakeEmbodiment()
    embodiment.raw_observation = {"robot_state": {"joints": {"pos": [1.0]}}}

    obs = build_observation(embodiment, "go")

    assert obs.raw == {"robot_state": {"joints": {"pos": [1.0]}}}


def test_sanity_check_action_rejects_nan_and_inf():
    current = np.zeros(6, dtype=np.float32)
    assert sanity_check_action(np.array([np.nan, 0, 0, 0, 0, 0]), current) is None
    assert sanity_check_action(np.array([np.inf, 0, 0, 0, 0, 0]), current) is None


def test_sanity_check_action_rejects_large_jump():
    current = np.zeros(6, dtype=np.float32)
    jumpy = np.array([31.0, 0, 0, 0, 0, 0])
    assert sanity_check_action(jumpy, current) is None


def test_sanity_check_action_accepts_valid_action():
    current = np.zeros(6, dtype=np.float32)
    action = np.array([10.0, -10.0, 5.0, 0.0, 2.0, 1.0])
    result = sanity_check_action(action, current)
    assert result is not None
    assert np.array_equal(result, action)
