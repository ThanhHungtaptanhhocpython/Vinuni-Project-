"""Test double cho `Embodiment` + helper dựng `ToolContext` dùng chung cho test tool.

Sau refactor "Embodiment", các test tool không còn cần `RobotBackend`/`RobotDriver`/
`CameraService` — chỉ cần một `Embodiment` giả chạy hoàn toàn trong bộ nhớ. `FakeEmbodiment`
mô phỏng đúng ngữ nghĩa tối thiểu mà mọi tool trông cậy vào:

  - `apply_action` kéo `positions` về phía action một phần cố định (đủ hội tụ trong vài bước,
    không cần physics thật) và raise `EmbodimentStopped` nếu đang dừng.
  - `apply_stop`/`clear_stop` set/clear cờ dừng nội bộ, không tắt torque (bất biến an toàn).
  - `read_state`/`read_frames` trả dữ liệu tổng hợp hợp lệ theo đúng schema DOC 0.

Mặc định cấu hình giống SO-101 thật (6 khớp `JOINT_ORDER`, `JOINT_POSITION_DEG`, 2 camera
top/wrist) — test nào cần mô phỏng sim (không có chẩn đoán servo) tự ép `joints=None` qua
`force_joints=None`.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from src.edge_vla.embodiment.base import Embodiment, EmbodimentState, EmbodimentStopped, StepResult
from src.edge_vla.embodiment.spec import ActionSpace, ActuationModel, CameraSpec, EmbodimentSpec, TaskRef
from src.edge_vla.mcp_server import context
from src.edge_vla.mcp_server.shared_state import SharedState
from src.edge_vla.models import JOINT_ORDER, JointState, StopMode

_APPROACH_RATE = 0.5  # mỗi apply_action đi được 50% quãng đường còn lại tới action đích

_DEFAULT_SPEC = EmbodimentSpec(
    embodiment_id="so101",
    action_space=ActionSpace.JOINT_POSITION_DEG,
    actuation_model=ActuationModel.CONTINUOUS_SERVO,
    action_dim=len(JOINT_ORDER),
    joint_names=tuple(j.value for j in JOINT_ORDER),
    cameras=(
        CameraSpec(camera_id="top", width=320, height=240, fps=30),
        CameraSpec(camera_id="wrist", width=320, height=240, fps=30),
    ),
    control_hz=50.0,
    is_simulated=False,
    supports_success_signal=False,
    supports_go_home=True,
)


def _default_joint_state(name, position: float) -> JointState:
    return JointState(
        name=name,
        motor_id=1,
        present_position=position,
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


class FakeEmbodiment(Embodiment):
    """Test double in-memory cho `Embodiment` — không cần mujoco/lerobot/phần cứng.

    Test tuỳ biến hành vi bằng cách gán trực tiếp thuộc tính sau khi dựng, vd:
        emb = FakeEmbodiment()
        emb.healthy = False
        emb.joints = None            # giả lập sim: không có chẩn đoán servo
        emb.in_limit = False
    """

    def __init__(self, spec: EmbodimentSpec | None = None) -> None:
        self._spec = spec or _DEFAULT_SPEC
        dof = self._spec.action_dim
        self.positions = np.zeros(dof, dtype=np.float32)
        self.home_position = np.zeros(dof, dtype=np.float32)

        # `None` = mô phỏng sim (không chẩn đoán servo). Mặc định: danh sách JointState hợp lệ.
        self.joints: list[JointState] | None = [
            _default_joint_state(name, 0.0) for name in JOINT_ORDER
        ]

        self.in_limit = True
        self.healthy = True
        self.raw_observation: dict | None = None

        self._stopped = False
        self.last_stop_mode: StopMode | None = None
        self.stop_calls: list[StopMode] = []
        self.actions: list[np.ndarray] = []
        self.connected = False
        self.current_task: TaskRef | None = None
        self.episode_active = False

        # Kết quả `apply_action` kế tiếp trả về — test override để mô phỏng SUCCESS/FAILED.
        self.next_step_result: StepResult | None = None

    @property
    def spec(self) -> EmbodimentSpec:
        return self._spec

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def begin_episode(self, task: TaskRef) -> None:
        self.current_task = task
        self.episode_active = True
        self.clear_stop()

    async def end_episode(self) -> None:
        self.episode_active = False

    def read_state(self) -> EmbodimentState:
        joints = None
        if self.joints is not None:
            joints = [
                dataclasses.replace(joint, present_position=float(self.positions[i]))
                if i < len(self.positions)
                else joint
                for i, joint in enumerate(self.joints)
            ]
        return EmbodimentState(
            positions=self.positions.copy(),
            joints=joints,
            in_limit=self.in_limit,
            healthy=self.healthy,
            raw_observation=self.raw_observation,
        )

    def read_frames(self) -> dict[str, np.ndarray]:
        return {
            camera.camera_id: np.full((240, 320, 3), 128, dtype=np.uint8)
            for camera in self._spec.cameras
        }

    async def apply_action(self, action: np.ndarray) -> StepResult:
        if self._stopped:
            raise EmbodimentStopped("apply_action từ chối vì đang dừng")

        self.actions.append(np.asarray(action, dtype=np.float32))
        action = np.asarray(action, dtype=np.float32)
        self.positions = self.positions + (action - self.positions) * _APPROACH_RATE

        if self.next_step_result is not None:
            return self.next_step_result
        return StepResult()

    def apply_stop(self, mode: StopMode) -> None:
        self._stopped = True
        self.last_stop_mode = mode
        self.stop_calls.append(mode)

    def clear_stop(self) -> None:
        self._stopped = False

    async def go_home(self) -> None:
        self.positions = self.home_position.copy()


def set_test_context(
    *,
    shared_state: SharedState | None = None,
    embodiment: FakeEmbodiment | None = None,
    config=None,
) -> tuple[SharedState, FakeEmbodiment]:
    """Dựng + set `ToolContext` dùng chung cho test tool. Trả `(shared_state, embodiment)` để
    test tiếp tục điều khiển/kiểm tra trạng thái sau khi set."""
    from tests.test_edge_vla.fake_config import fake_config

    shared_state = shared_state or SharedState()
    embodiment = embodiment or FakeEmbodiment()
    config = config or fake_config()

    context.set_context(shared_state, embodiment, config)
    return shared_state, embodiment
