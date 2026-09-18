"""Version-matched local DaVinci Resolve scripting backend.

This module does not call a cloud API. It loads the scripting bridge shipped
with the installed desktop application and connects to the current Resolve
process and signed-in desktop session.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from .config import load_config

WINDOWS_EXE = Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe")
WINDOWS_API = Path(
    os.environ.get("PROGRAMDATA", r"C:\ProgramData")
) / r"Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
WINDOWS_LIB = Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")


class ResolveConnectionError(RuntimeError):
    """Raised when the installed application cannot be reached."""


def discover_paths() -> dict[str, str]:
    config = load_config()
    if sys.platform.startswith("win"):
        defaults = {
            "resolve_exe": str(WINDOWS_EXE),
            "script_api": str(WINDOWS_API),
            "script_lib": str(WINDOWS_LIB),
        }
    elif sys.platform == "darwin":
        defaults = {
            "resolve_exe": "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/MacOS/Resolve",
            "script_api": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
            "script_lib": "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
        }
    else:
        defaults = {
            "resolve_exe": "/opt/resolve/bin/resolve",
            "script_api": "/opt/resolve/Developer/Scripting",
            "script_lib": "/opt/resolve/libs/Fusion/fusionscript.so",
        }
    for key in defaults:
        env_key = {"resolve_exe": "DAVINCI_RESOLVE_EXE", "script_api": "RESOLVE_SCRIPT_API", "script_lib": "RESOLVE_SCRIPT_LIB"}[key]
        defaults[key] = str(config.get(key) or os.environ.get(env_key) or defaults[key])
    return defaults


def path_status() -> dict[str, Any]:
    paths = discover_paths()
    api = Path(paths["script_api"])
    module = api / "Modules" / "DaVinciResolveScript.py"
    return {
        **paths,
        "resolve_exe_exists": Path(paths["resolve_exe"]).is_file(),
        "script_api_exists": api.is_dir(),
        "script_module": str(module),
        "script_module_exists": module.is_file(),
        "script_lib_exists": Path(paths["script_lib"]).is_file(),
    }


def launch() -> dict[str, Any]:
    exe = Path(discover_paths()["resolve_exe"])
    if not exe.is_file():
        raise FileNotFoundError(f"DaVinci Resolve executable not found: {exe}")
    process = subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"launched": True, "pid": process.pid, "executable": str(exe)}


def _load_bridge():
    paths = discover_paths()
    module_path = Path(paths["script_api"]) / "Modules" / "DaVinciResolveScript.py"
    if not module_path.is_file():
        raise ResolveConnectionError(f"Resolve scripting module not found: {module_path}")
    os.environ["RESOLVE_SCRIPT_API"] = paths["script_api"]
    os.environ["RESOLVE_SCRIPT_LIB"] = paths["script_lib"]
    spec = importlib.util.spec_from_file_location("DaVinciResolveScript", module_path)
    if spec is None or spec.loader is None:
        raise ResolveConnectionError(f"Unable to load Resolve scripting module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    # The vendor wrapper replaces this sys.modules entry with the native
    # fusionscript module during execution. Return that replacement rather
    # than the now-stale Python wrapper object.
    sys.modules["DaVinciResolveScript"] = module
    spec.loader.exec_module(module)
    return sys.modules.get("DaVinciResolveScript", module)


def connect():
    try:
        bridge = _load_bridge()
        resolve = bridge.scriptapp("Resolve")
    except (AttributeError, ImportError, OSError) as exc:
        raise ResolveConnectionError(f"Resolve scripting bridge failed: {exc}") from exc
    if resolve is None:
        raise ResolveConnectionError(
            "DaVinci Resolve is installed but no scriptable desktop session answered. "
            "Open Resolve Studio, finish any sign-in/startup dialog, and retry."
        )
    return resolve


def application_info(resolve) -> dict[str, Any]:
    version = resolve.GetVersion() if hasattr(resolve, "GetVersion") else []
    product = resolve.GetProductName() if hasattr(resolve, "GetProductName") else "DaVinci Resolve"
    page = resolve.GetCurrentPage() if hasattr(resolve, "GetCurrentPage") else None
    return {"product": product, "version": version, "version_string": ".".join(map(str, version)), "current_page": page}


def current_project(resolve, required: bool = True):
    project = resolve.GetProjectManager().GetCurrentProject()
    if required and project is None:
        raise ResolveConnectionError("Resolve is connected, but no project is currently open.")
    return project


def project_summary(project) -> dict[str, Any]:
    timeline = project.GetCurrentTimeline()
    return {
        "name": project.GetName(),
        "id": project.GetUniqueId(),
        "timeline_count": project.GetTimelineCount(),
        "current_timeline": timeline.GetName() if timeline else None,
        "settings": project.GetSettings(),
    }


def timeline_summary(timeline) -> dict[str, Any]:
    return {
        "name": timeline.GetName(),
        "id": timeline.GetUniqueId(),
        "start_frame": timeline.GetStartFrame(),
        "end_frame": timeline.GetEndFrame(),
        "start_timecode": timeline.GetStartTimecode(),
        "current_timecode": timeline.GetCurrentTimecode(),
        "tracks": {kind: timeline.GetTrackCount(kind) for kind in ("video", "audio", "subtitle")},
        "settings": timeline.GetSettings(),
    }


def walk_folder(folder, prefix: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    folder_name = folder.GetName()
    location = f"{prefix}/{folder_name}" if prefix else folder_name
    for clip in folder.GetClipList() or []:
        props = clip.GetClipProperty() or {}
        rows.append({"folder": location, "name": clip.GetName(), "id": clip.GetUniqueId(), "file_path": props.get("File Path"), "properties": props})
    for child in folder.GetSubFolderList() or []:
        rows.extend(walk_folder(child, location))
    return rows


def find_media_items(project, identifiers: Iterable[str]):
    wanted = {value.casefold() for value in identifiers}
    root = project.GetMediaPool().GetRootFolder()

    def visit(folder):
        found = []
        for clip in folder.GetClipList() or []:
            props = clip.GetClipProperty() or {}
            choices = {str(clip.GetName()).casefold(), str(clip.GetUniqueId()).casefold(), str(props.get("File Path", "")).casefold()}
            if wanted & choices:
                found.append(clip)
        for child in folder.GetSubFolderList() or []:
            found.extend(visit(child))
        return found

    found = visit(root)
    if len(found) != len(wanted):
        raise ValueError(f"Resolved {len(found)} of {len(wanted)} requested media items")
    return found
