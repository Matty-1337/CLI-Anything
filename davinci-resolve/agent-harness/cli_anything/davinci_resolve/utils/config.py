"""Persistent local path configuration with locked writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def default_config_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
    return root / "cli-anything-davinci-resolve" / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or default_config_path()
    if not target.exists():
        return {}
    with target.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def _locked_save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("r+", encoding="utf-8")
    except FileNotFoundError:
        handle = path.open("w", encoding="utf-8")
    with handle:
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
        except (ImportError, OSError):
            pass
        try:
            handle.seek(0)
            handle.truncate()
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            if locked:
                try:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


def save_config(updates: dict[str, Any], path: Path | None = None) -> Path:
    target = path or default_config_path()
    current = load_config(target)
    current.update({key: value for key, value in updates.items() if value is not None})
    _locked_save_json(target, current)
    return target
