"""Loads SmolVLA (HuggingFaceVLA/smolvla_libero) once at startup and reuses it
for every task."""

from __future__ import annotations

import numpy as np
import torch
from lerobot.configs.policies import PreTrainedConfig
from lerobot.envs.configs import LiberoEnv as LiberoEnvConfig
from lerobot.envs.factory import make_env_pre_post_processors
from lerobot.envs.utils import preprocess_observation
from lerobot.policies.factory import make_policy, make_pre_post_processors

from .. import config


def _add_batch_dim(obs: dict) -> dict:
    """LiberoEnv (non-vectorized) returns robot_state without a batch dim.
    preprocess_observation() only adds a batch dim to pixels/agent_pos, so it
    must be added manually here to match the (B, ...) shape LiberoProcessorStep
    expects."""
    out = dict(obs)
    if "robot_state" in out:

        def expand(d):
            return {k: expand(v) if isinstance(v, dict) else np.expand_dims(v, 0) for k, v in d.items()}

        out["robot_state"] = expand(out["robot_state"])
    return out


class PolicyRunner:
    def __init__(self):
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device

        env_cfg = LiberoEnvConfig(
            task=config.SUITE,
            obs_type="pixels_agent_pos",
            observation_height=config.OBS_HEIGHT,
            observation_width=config.OBS_WIDTH,
            control_mode=config.CONTROL_MODE,
            fps=config.CONTROL_FREQ,
        )

        policy_cfg = PreTrainedConfig.from_pretrained(config.POLICY_REPO)
        policy_cfg.pretrained_path = config.POLICY_REPO
        policy_cfg.device = device

        self.policy = make_policy(cfg=policy_cfg, env_cfg=env_cfg)
        self.policy.eval()
        self.policy.config.n_action_steps = config.N_ACTION_STEPS

        self.preprocessor, self.postprocessor = make_pre_post_processors(
            policy_cfg=policy_cfg,
            pretrained_path=config.POLICY_REPO,
            preprocessor_overrides={"device_processor": {"device": device}},
        )
        self.env_preprocessor, self.env_postprocessor = make_env_pre_post_processors(
            env_cfg=env_cfg, policy_cfg=policy_cfg
        )

    def reset(self):
        """Clear the policy's internal action queue — must be called before each
        new take_action, since a leftover chunk is no longer valid for a new
        task/scene."""
        self.policy.reset()

    def act(self, observation: dict, task_text: str) -> np.ndarray:
        obs = preprocess_observation(_add_batch_dim(observation))
        obs["task"] = [task_text]
        obs = self.env_preprocessor(obs)
        obs = self.preprocessor(obs)

        with torch.inference_mode():
            action = self.policy.select_action(obs)
        action = self.postprocessor(action)
        return action.to("cpu").numpy()[0]
