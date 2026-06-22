"""Path authority for GenericAgent app code and runtime state.

The source tree remains usable without environment variables: when
``GENERICAGENT_HOME`` is unset, runtime state defaults to the app root.  Nix
packages set ``GENERICAGENT_APP_ROOT`` to immutable app code and
``GENERICAGENT_HOME`` to a writable runtime directory.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_ROOT = Path(os.environ.get("GENERICAGENT_APP_ROOT", Path(__file__).resolve().parent)).resolve()
RUNTIME_ROOT = Path(os.environ.get("GENERICAGENT_HOME", APP_ROOT)).expanduser().resolve()


def app_path(*parts: str | os.PathLike[str]) -> Path:
    return APP_ROOT.joinpath(*parts)


def asset_path(*parts: str | os.PathLike[str]) -> Path:
    return app_path("assets", *parts)


def runtime_path(*parts: str | os.PathLike[str]) -> Path:
    return RUNTIME_ROOT.joinpath(*parts)


def memory_path(*parts: str | os.PathLike[str]) -> Path:
    return runtime_path("memory", *parts)


def temp_path(*parts: str | os.PathLike[str]) -> Path:
    return runtime_path("temp", *parts)


def runtime_asset_path(*parts: str | os.PathLike[str]) -> Path:
    return runtime_path("assets", *parts)


def extension_overlay_path(extension: str, *parts: str | os.PathLike[str]) -> Path:
    return runtime_asset_path(extension, *parts)


def mykey_py_path() -> Path:
    return runtime_path("mykey.py")


def mykey_json_path() -> Path:
    return runtime_path("mykey.json")


def boards_path() -> Path:
    return runtime_path("boards.json")


def bbs_files_path(*parts: str | os.PathLike[str]) -> Path:
    return runtime_path("bbs_files", *parts)


def ensure_runtime_dirs() -> None:
    for path in (
        RUNTIME_ROOT,
        memory_path(),
        temp_path(),
        temp_path("model_responses"),
        bbs_files_path(),
        runtime_asset_path(),
        extension_overlay_path("tmwd_cdp_bridge"),
    ):
        path.mkdir(parents=True, exist_ok=True)
