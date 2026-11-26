#!/usr/bin/env python3
"""Write a non-interactive LIBERO config.yaml and (optionally) prefetch assets.

hf-libero's package `__init__` prompts on stdin the first time config.yaml is
missing — that hangs/EOF-crashes headless Docker / Cloud Run. Call this during
image build (and optionally at container start) so startup never hits `input()`.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml


def _libero_package_root() -> Path:
    import libero.libero as libero_pkg

    return Path(libero_pkg.__file__).resolve().parent


def _package_root_via_spec() -> Path:
    """Locate libero/libero without importing libero.libero (avoids stdin prompt)."""
    import importlib.util

    spec = importlib.util.find_spec("libero")
    if spec is None or not spec.submodule_search_locations:
        raise ModuleNotFoundError("libero")
    return Path(list(spec.submodule_search_locations)[0]) / "libero"


def write_config(config_dir: Path, assets_dir: Path) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        root = _package_root_via_spec()
    except Exception:
        root = _libero_package_root()

    cfg = {
        "benchmark_root": str(root),
        "bddl_files": str(root / "bddl_files"),
        "init_states": str(root / "init_files"),
        "datasets": str(root.parent / "datasets"),
        "assets": str(assets_dir),
    }
    path = config_dir / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def ensure_libero_config(
    config_dir: Path | None = None,
    assets_dir: Path | None = None,
) -> Path:
    """Idempotent: write config.yaml only if missing. Safe to call at import time."""
    config_dir = Path(
        config_dir
        or os.environ.get("LIBERO_CONFIG_PATH")
        or Path.home() / ".libero"
    )
    assets_dir = Path(
        assets_dir
        or os.environ.get("LIBERO_ASSETS_PATH")
        or Path.home() / ".cache" / "libero" / "assets"
    )
    os.environ.setdefault("LIBERO_CONFIG_PATH", str(config_dir))
    cfg_path = config_dir / "config.yaml"
    if not cfg_path.is_file():
        write_config(config_dir, assets_dir)
    return cfg_path


def download_assets(assets_dir: Path) -> Path:
    assets_dir.mkdir(parents=True, exist_ok=True)
    from libero.libero.utils.download_utils import download_assets_from_huggingface

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            out = Path(download_assets_from_huggingface(str(assets_dir)))
            break
        except Exception as exc:  # network blips on HF during docker build
            last_err = exc
            print(f"Asset download attempt {attempt}/3 failed: {exc}")
            if attempt == 3:
                raise
    else:
        raise RuntimeError("asset download failed") from last_err

    # libero.libero.get_assets_path() prefers <package>/assets over config.yaml.
    # Copy (not symlink) so the runtime image is self-contained even if
    # /app/libero_assets is omitted.
    try:
        pkg_assets = _package_root_via_spec() / "assets"
    except Exception:
        pkg_assets = _libero_package_root() / "assets"
    if not pkg_assets.exists():
        import shutil

        shutil.copytree(out, pkg_assets)
        print(f"Copied assets into package path: {pkg_assets}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config-dir",
        default=os.environ.get("LIBERO_CONFIG_PATH", "/app/libero_config"),
        type=Path,
    )
    parser.add_argument(
        "--assets-dir",
        default=os.environ.get("LIBERO_ASSETS_PATH", "/app/libero_assets"),
        type=Path,
    )
    parser.add_argument(
        "--download-assets",
        action="store_true",
        help="Prefetch MuJoCo mesh/texture assets from Hugging Face.",
    )
    args = parser.parse_args()

    cfg_path = write_config(args.config_dir, args.assets_dir)
    print(f"Wrote LIBERO config: {cfg_path}")

    if args.download_assets:
        # Config must exist before importing libero internals that call get_libero_path.
        os.environ["LIBERO_CONFIG_PATH"] = str(args.config_dir)
        out = download_assets(args.assets_dir)
        print(f"LIBERO assets ready at: {out}")


if __name__ == "__main__":
    main()
