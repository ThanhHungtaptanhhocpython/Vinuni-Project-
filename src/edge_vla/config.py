"""Hard limits and paths for Edge VLA.

These values are fixed in code and must not be adjustable by the agent — the
edge process is the sole owner of its hard safety/operational bounds.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from .robot_config import ConfigError

# `run_edge_vla_linux.sh` (conda path) does not source `.env` itself like
# `run_edge_vla.sh` does — load it here so env vars reach the process no
# matter which script (or a bare `python -m edge_vla.server`) starts it.
# `override=False`: an already-exported var (CLI flag, shell env) wins.
load_dotenv(Path(__file__).with_name(".env"), override=False)

# --- Hard limits (agent cannot exceed these) ---
MAX_RETRY = 10
# Cloud `_monitor_episode` calls check_status every ~2–17s; k=500 on slow osmesa
# sim can run 300s+. 8 polls exhausted the budget around step 450/500.
MAX_POLL = 32
WAIT_CLAMP = (1, 15)
MAX_K = 10000
TASK_TIMEOUT_S = 1800

# --- Scenario ---
SUITE = "libero_object"
POLICY_REPO = "HuggingFaceVLA/smolvla_libero"

# --- Policy backend selection ---
POLICY_BACKEND = os.environ.get("VLA_POLICY", "smolvla").strip().lower()  # "smolvla" | "ik"
if POLICY_BACKEND not in ("smolvla", "ik"):
    raise ConfigError(f"VLA_POLICY không hợp lệ: {POLICY_BACKEND!r}; phải là 'smolvla' hoặc 'ik'")

# --- IK scripted policy (sim, CPU-only) ---
IK_CONTROL_MODE = "absolute"
IK_APPROACH_HEIGHT = 0.10
IK_GRASP_Z_OFFSET = 0.0
# Basket walls in libero_object measure ~0.15m above basket_pos (MuJoCo geom
# bounds, measured empirically). object_pos/eef height is the object's
# *center*, not its bottom, so IK_LIFT_HEIGHT/IK_DROP_HEIGHT need enough
# margin above the rim to also clear the carried object's own half-height —
# otherwise the object's underside drags across the basket wall during
# TRANSPORT (observed: pushes the basket ~8cm instead of dropping into it).
IK_LIFT_HEIGHT = 0.25
IK_DROP_HEIGHT = 0.32
IK_POS_TOL = 0.008
IK_MAX_STEP = 0.02
IK_GRIPPER_SETTLE_STEPS = 12
IK_STATE_TIMEOUT_STEPS = 100

# The policy's default n_action_steps=1 re-runs inference every step. Replaying
# an action chunk (n_action_steps=10) trades a small drop in reactivity for a
# large throughput gain, since inference then only runs once per 10 steps.
N_ACTION_STEPS = int(os.environ.get("VLA_N_ACTION_STEPS", "10"))

# --- Observation / control ---
OBS_HEIGHT = 256
OBS_WIDTH = 256
CONTROL_FREQ = 20
CONTROL_MODE = "relative"

# --- Safety thresholds (network-independent, checked every step) ---
# Set well above normal policy operating range (to avoid false positives) but
# low enough to catch a genuine actuator/joint runaway.
SAFETY_MAX_ACTUATOR_FORCE = 150.0
SAFETY_MAX_JOINT_VEL = 6.0
SAFETY_INJECT_FILE = "/tmp/vla_inject_safety"

# --- Ports ---
# Cloud Run injects PORT; local dev uses VLA_MCP_PORT (default 8931).
MCP_PORT = int(os.environ.get("PORT") or os.environ.get("VLA_MCP_PORT", "8931"))

# --- Cloud registration (see cloud_registration.py) ---
# Base URL of the cloud-agent backend this Edge registers itself with on
# boot. Empty = registration is skipped (local-only run).
CLOUD_AGENT_BASE_URL = os.environ.get("CLOUD_AGENT_BASE_URL", "").strip()
# Public/LAN/tunnel URL that cloud-agent can call back for /mjpeg and
# /servo-stream. Empty keeps registration working but stream relay disabled.
EDGE_PUBLIC_URL = os.environ.get("EDGE_PUBLIC_URL", "").strip().rstrip("/")
# Shared secret used to SIGN the registration JWT — must match EDGE_JWT_SECRET
# in cloud-agent's own env exactly (HS256, symmetric).
EDGE_JWT_SECRET = os.environ.get("EDGE_JWT_SECRET", "").strip()
# Stable robot id. Empty = auto-generate + persist locally on first boot
# (see cloud_registration.resolve_robot_id).
EDGE_ROBOT_ID = os.environ.get("EDGE_ROBOT_ID", "").strip()
