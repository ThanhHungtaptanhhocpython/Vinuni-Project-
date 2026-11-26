#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="${CONDA_ENV:-lerobot_arm}"

usage() {
    cat <<EOF
Usage: $(basename "${BASH_SOURCE[0]}") [OPTIONS]

Run the Edge VLA server on Linux, in either simulation or real-robot mode.

Options:
  -m, --mode {sim|real}     Robot mode. Defaults to \$ROBOT_MODE, or prompts
                             interactively if not set and running in a terminal.
  -c, --config PATH          Robot config YAML (real mode). Defaults to
                             \$EDGE_VLA_ROBOT_CONFIG, or the built-in
                             src/edge_vla/robot_config.yaml.
  -e, --conda-env NAME       Conda environment to activate. Default: lerobot_arm
                             (or \$CONDA_ENV).
  -p, --port PORT            MCP server port. Default: \$VLA_MCP_PORT or 8931.
      --policy-path PATH     Override the policy checkpoint path
                             (sets \$VLA_POLICY_PATH).
  -P, --policy {smolvla|ik}  Sim policy backend (sets \$VLA_POLICY). Default:
                             smolvla. 'ik' is a CPU-only ground-truth scripted
                             policy — no torch, no GPU needed.
  -h, --help                 Show this help and exit.

Examples:
  $(basename "${BASH_SOURCE[0]}")                          # interactive mode picker
  $(basename "${BASH_SOURCE[0]}") --mode sim
  $(basename "${BASH_SOURCE[0]}") --mode sim --policy ik   # CPU-only, no GPU
  $(basename "${BASH_SOURCE[0]}") --mode real --config ./src/edge_vla/robot_config.yaml
EOF
}

ROBOT_MODE="${ROBOT_MODE:-}"
CONFIG_PATH="${EDGE_VLA_ROBOT_CONFIG:-}"
PORT="${VLA_MCP_PORT:-8931}"
POLICY_PATH="${VLA_POLICY_PATH:-}"
VLA_POLICY="${VLA_POLICY:-smolvla}"

while [ $# -gt 0 ]; do
    case "$1" in
        -m|--mode)
            ROBOT_MODE="$2"
            shift 2
            ;;
        -c|--config)
            CONFIG_PATH="$2"
            shift 2
            ;;
        -e|--conda-env)
            CONDA_ENV="$2"
            shift 2
            ;;
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        --policy-path)
            POLICY_PATH="$2"
            shift 2
            ;;
        -P|--policy)
            VLA_POLICY="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ -z "$ROBOT_MODE" ]; then
    if [ -t 0 ]; then
        echo "Select robot mode:"
        select choice in "sim" "real"; do
            case "$choice" in
                sim|real)
                    ROBOT_MODE="$choice"
                    break
                    ;;
                *)
                    echo "Invalid choice, try again." >&2
                    ;;
            esac
        done
    else
        ROBOT_MODE="sim"
    fi
fi

if [ "$ROBOT_MODE" != "sim" ] && [ "$ROBOT_MODE" != "real" ]; then
    echo "Invalid --mode '$ROBOT_MODE'; must be 'sim' or 'real'." >&2
    exit 1
fi
export ROBOT_MODE

if [ "$VLA_POLICY" != "smolvla" ] && [ "$VLA_POLICY" != "ik" ]; then
    echo "Invalid --policy '$VLA_POLICY'; must be 'smolvla' or 'ik'." >&2
    exit 1
fi
export VLA_POLICY

# EGL only exists on Linux; macOS's MuJoCo build renders via CGL (Apple's
# native GL context). Forcing egl here on Darwin is what produced the
# "gladLoadGL error" in the old MUJOCO_LOG.TXT.
#
# In "ik" mode the policy itself is CPU-only (no torch); without a GPU, EGL
# rendering also needs to fall back to osmesa (software rasterizer) so the
# whole process is CPU-only, not just the policy.
if [ "$ROBOT_MODE" != "real" ] && [ -z "${MUJOCO_GL:-}" ]; then
    case "$(uname -s)" in
        Darwin) export MUJOCO_GL="cgl" ;;
        *)
            if [ "$VLA_POLICY" = "ik" ] && ! command -v nvidia-smi >/dev/null 2>&1; then
                export MUJOCO_GL="osmesa"
            else
                export MUJOCO_GL="egl"
            fi
            ;;
    esac
fi

if [ "$ROBOT_MODE" = "real" ] && [ -z "$CONFIG_PATH" ]; then
    CONFIG_PATH="$ROOT_DIR/src/edge_vla/robot_config.yaml"
fi

if [ -n "$CONFIG_PATH" ]; then
    if [ ! -f "$CONFIG_PATH" ]; then
        echo "Robot config not found: $CONFIG_PATH" >&2
        exit 1
    fi
    export EDGE_VLA_ROBOT_CONFIG="$(cd "$(dirname "$CONFIG_PATH")" && pwd)/$(basename "$CONFIG_PATH")"
fi

if [ -n "$POLICY_PATH" ]; then
    export VLA_POLICY_PATH="$POLICY_PATH"
fi

export VLA_MCP_PORT="$PORT"

activate_conda() {
    if ! command -v conda >/dev/null 2>&1; then
        if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
            # shellcheck source=/dev/null
            source "$HOME/miniconda3/etc/profile.d/conda.sh"
        elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
            # shellcheck source=/dev/null
            source "$HOME/anaconda3/etc/profile.d/conda.sh"
        else
            echo "Cannot find conda. Please install Conda or source conda.sh before running this script." >&2
            exit 1
        fi
    else
        local conda_base
        conda_base="$(conda info --base)"
        # shellcheck source=/dev/null
        source "$conda_base/etc/profile.d/conda.sh"
    fi

    conda activate "$CONDA_ENV"
}

if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "$PIDS" ]; then
        echo "Port $PORT is in use (PIDs: $PIDS) — stopping leftover listener(s)."
        # shellcheck disable=SC2086
        kill $PIDS 2>/dev/null || true
        sleep 0.5
        STILL="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
        if [ -n "$STILL" ]; then
            # shellcheck disable=SC2086
            kill -9 $STILL 2>/dev/null || true
        fi
    fi
fi

echo "Starting Edge VLA server (mode=$ROBOT_MODE, policy=$VLA_POLICY, port=$PORT, conda_env=$CONDA_ENV)${EDGE_VLA_ROBOT_CONFIG:+, config=$EDGE_VLA_ROBOT_CONFIG}"

activate_conda
cd "$ROOT_DIR/src"

exec python -m edge_vla.server
