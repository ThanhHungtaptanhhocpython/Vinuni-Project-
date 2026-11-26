# Build from repo root:
#   docker build -f src/edge_vla/Dockerfile .
#
# Production: ROBOT_MODE=sim + VLA_POLICY=ik (see deploy.yml).
# Installs base + sim + cpu extras only — no smolvla / CUDA stack.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# hf-egl-probe / egl-probe compile via CMake (setup.py shells out to `cmake`).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    libegl1-mesa-dev \
    libgl1-mesa-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/edge_vla
COPY src/edge_vla/pyproject.toml src/edge_vla/uv.lock ./

# sim = LIBERO/MuJoCo; cpu = CPU-only torch (no nvidia-* / triton). No smolvla extra.
# Do NOT delete *.tests dirs from the venv — numpy.testing imports
# numpy._core.tests, and transformers.AutoProcessor fails without it.
RUN uv sync --extra sim --extra cpu --frozen --no-dev \
 && .venv/bin/python -c "import torch; v=torch.__version__; assert '+cu' not in v, v" \
 && find .venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true \
 && rm -rf /root/.cache/uv

# Prefetch LIBERO config + MuJoCo assets so Cloud Run never hits interactive
# `input()` or a cold-start HF download (which can surface transformers errors).
COPY src/edge_vla/scripts/prepare_libero_runtime.py /tmp/prepare_libero_runtime.py
ENV LIBERO_CONFIG_PATH=/app/libero_config \
    LIBERO_ASSETS_PATH=/app/libero_assets
RUN .venv/bin/python /tmp/prepare_libero_runtime.py --download-assets \
 && rm -f /tmp/prepare_libero_runtime.py

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    libosmesa6 \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/edge_vla/.venv /app/edge_vla/.venv
COPY --from=builder /app/libero_config /app/libero_config
COPY --from=builder /app/libero_assets /app/libero_assets
COPY src/edge_vla/ /app/src/edge_vla/
RUN rm -rf /app/src/edge_vla/.venv /app/src/edge_vla/.venv*

ENV PATH="/app/edge_vla/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    ROBOT_MODE=sim \
    VLA_POLICY=ik \
    MUJOCO_GL=osmesa \
    EDGE_VLA_HEADLESS=1 \
    LIBERO_CONFIG_PATH=/app/libero_config \
    LIBERO_ASSETS_PATH=/app/libero_assets \
    PORT=8080

WORKDIR /app/src
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health')" || exit 1

CMD ["python", "-m", "edge_vla.server"]
