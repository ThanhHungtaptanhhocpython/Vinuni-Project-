import pytest

from src.edge_vla.config_loader import VlaConfig
from src.edge_vla.embodiment.spec import ActionSpace
from src.edge_vla.mcp_server.errors import AppError
from src.edge_vla.vla_runtime.policy import Policy, PolicyLoadError, PolicyRegistry, _resolve_checkpoint_dir
from tests.test_edge_vla.fakes import FakeEmbodiment

_SPEC = FakeEmbodiment().spec  # JOINT_POSITION_DEG — chỉ dùng làm placeholder cho các test không load thật


def _vla_config(**overrides) -> VlaConfig:
    defaults = dict(
        policy="smolvla",
        checkpoint_path="/opt/robot-agent/models/smolvla_default",
        device="cpu",
        inference_fps_target=10,
        default_max_steps=400,
        default_timeout_s=60,
    )
    defaults.update(overrides)
    return VlaConfig(**defaults)


class _FakePolicy(Policy):
    action_space = ActionSpace.JOINT_POSITION_DEG

    def reset(self) -> None:
        pass

    async def predict(self, obs):
        raise NotImplementedError


def test_resolve_checkpoint_dir_prefers_policy_specific_env(monkeypatch):
    monkeypatch.setenv("VLA_CHECKPOINT_PATH_SMOLVLA", "/data/checkpoints/specific")
    monkeypatch.setenv("VLA_CHECKPOINT_PATH", "/data/checkpoints/generic")

    result = _resolve_checkpoint_dir("smolvla", _vla_config())

    assert result == "/data/checkpoints/specific"


def test_resolve_checkpoint_dir_falls_back_to_generic_env(monkeypatch):
    monkeypatch.delenv("VLA_CHECKPOINT_PATH_MY_ARM_POLICY", raising=False)
    monkeypatch.setenv("VLA_CHECKPOINT_PATH", "/data/checkpoints/generic")

    result = _resolve_checkpoint_dir("my_arm_policy", _vla_config())

    assert result == "/data/checkpoints/generic"


def test_resolve_checkpoint_dir_falls_back_to_config_only_for_default_policy(monkeypatch):
    monkeypatch.delenv("VLA_CHECKPOINT_PATH_SMOLVLA", raising=False)
    monkeypatch.delenv("VLA_CHECKPOINT_PATH", raising=False)

    result = _resolve_checkpoint_dir("smolvla", _vla_config(policy="smolvla", checkpoint_path="/opt/models/x"))

    assert result == "/opt/models/x"


def test_resolve_checkpoint_dir_raises_for_unconfigured_custom_policy(monkeypatch):
    monkeypatch.delenv("VLA_CHECKPOINT_PATH_MY_ARM_POLICY", raising=False)
    monkeypatch.delenv("VLA_CHECKPOINT_PATH", raising=False)

    with pytest.raises(PolicyLoadError):
        _resolve_checkpoint_dir("my_arm_policy", _vla_config(policy="smolvla"))


def test_policy_registry_returns_cached_instance_without_reloading():
    registry = PolicyRegistry()
    fake = _FakePolicy()
    registry._cache["smolvla"] = fake

    result = registry.get("smolvla", _vla_config(), _SPEC, "libero_10")

    assert result is fake


def test_policy_registry_wraps_load_error_as_app_error(monkeypatch):
    monkeypatch.delenv("VLA_CHECKPOINT_PATH_UNKNOWN_POLICY", raising=False)
    monkeypatch.delenv("VLA_CHECKPOINT_PATH", raising=False)
    registry = PolicyRegistry()

    with pytest.raises(AppError) as exc_info:
        registry.get("unknown_policy", _vla_config(policy="smolvla"), _SPEC, "libero_10")

    assert exc_info.value.code == "ROBOT_040"
