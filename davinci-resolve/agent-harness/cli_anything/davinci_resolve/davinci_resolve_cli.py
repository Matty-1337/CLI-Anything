#!/usr/bin/env python3
"""Agent-native CLI for DaVinci Resolve Studio's local scripting bridge."""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

import click

from .utils.config import default_config_path, save_config
from .utils import resolve_backend as backend


def _json_default(value):
    return str(value)


def emit(ctx: click.Context, value: Any, message: str | None = None) -> Any:
    if ctx.find_root().obj.get("json"):
        click.echo(json.dumps(value, indent=2, default=_json_default))
    elif message:
        click.echo(message)
        if isinstance(value, (dict, list)):
            click.echo(json.dumps(value, indent=2, default=_json_default))
    elif isinstance(value, (dict, list)):
        click.echo(json.dumps(value, indent=2, default=_json_default))
    else:
        click.echo(value)
    return value


def command_guard(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (backend.ResolveConnectionError, FileNotFoundError, ValueError, RuntimeError) as exc:
            ctx = click.get_current_context()
            payload = {"ok": False, "error": str(exc), "type": type(exc).__name__}
            if ctx.find_root().obj.get("json"):
                click.echo(json.dumps(payload, indent=2))
            else:
                click.echo(f"Error: {exc}", err=True)
            raise click.exceptions.Exit(1)

    return wrapped


def mutation(ctx: click.Context, action: str, details: dict[str, Any], callback):
    if ctx.find_root().obj.get("dry_run"):
        return emit(ctx, {"ok": True, "dry_run": True, "action": action, **details})
    result = callback()
    if result is False or result is None:
        raise RuntimeError(f"Resolve rejected mutation: {action}")
    project = backend.current_project(backend.connect(), required=False)
    if project is not None:
        backend.connect().GetProjectManager().SaveProject()
    return emit(ctx, {"ok": True, "dry_run": False, "action": action, **details, "result": result})


def _resolve_and_project():
    resolve = backend.connect()
    return resolve, backend.current_project(resolve)


@click.group(invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--dry-run", is_flag=True, help="Plan mutating commands without changing Resolve.")
@click.pass_context
def cli(ctx: click.Context, use_json: bool, dry_run: bool):
    """Control the installed DaVinci Resolve Studio desktop application.

    With no subcommand, starts an interactive REPL. The CLI uses Resolve's
    bundled local scripting bridge; it does not require a cloud API key.
    """
    ctx.ensure_object(dict)
    ctx.obj.update(json=use_json, dry_run=dry_run)
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


@cli.command()
@click.pass_context
@command_guard
def doctor(ctx: click.Context):
    """Verify installed paths, desktop connection, and active project state."""
    paths = backend.path_status()
    connected = False
    app = None
    project = None
    error = None
    try:
        resolve = backend.connect()
        connected = True
        app = backend.application_info(resolve)
        current = backend.current_project(resolve, required=False)
        project = backend.project_summary(current) if current else None
    except backend.ResolveConnectionError as exc:
        error = str(exc)
    healthy = all(paths[key] for key in ("resolve_exe_exists", "script_api_exists", "script_module_exists", "script_lib_exists")) and connected
    payload = {
        "ok": healthy,
        "backend": "local-installed-resolve-scripting-bridge",
        "cloud_api": False,
        "paths": paths,
        "desktop_session": {"connected": connected, "authenticated_by": "DaVinci Resolve desktop session" if connected else None, "error": error},
        "application": app,
        "project": project,
    }
    emit(ctx, payload)
    if not healthy:
        raise click.exceptions.Exit(1)


@cli.group()
def configure():
    """Configure installed Resolve paths."""


@configure.command("paths")
@click.option("--resolve-exe", type=click.Path(path_type=Path))
@click.option("--script-api", type=click.Path(path_type=Path))
@click.option("--script-lib", type=click.Path(path_type=Path))
@click.pass_context
@command_guard
def configure_paths(ctx, resolve_exe, script_api, script_lib):
    """Persist explicit application, scripting API, and bridge paths."""
    values = {
        "resolve_exe": str(resolve_exe.resolve()) if resolve_exe else None,
        "script_api": str(script_api.resolve()) if script_api else None,
        "script_lib": str(script_lib.resolve()) if script_lib else None,
    }
    if not any(values.values()):
        return emit(ctx, {"config_path": str(default_config_path()), "paths": backend.discover_paths()})
    for key, value in values.items():
        if value and not Path(value).exists():
            raise FileNotFoundError(f"{key} path not found: {value}")
    if ctx.find_root().obj.get("dry_run"):
        return emit(ctx, {"ok": True, "dry_run": True, "config_path": str(default_config_path()), "updates": values})
    path = save_config(values)
    emit(ctx, {"ok": True, "config_path": str(path), "paths": backend.discover_paths()})


@cli.group()
def auth():
    """Inspect the local desktop-session connection (no API key required)."""


@auth.command("status")
@click.pass_context
@command_guard
def auth_status(ctx):
    resolve = backend.connect()
    app = backend.application_info(resolve)
    emit(ctx, {"ok": True, "connected": True, "mode": "local_desktop_session", "credentials_required": False, "application": app})


@auth.command("connect")
@click.option("--launch", is_flag=True, help="Launch Resolve if it is not already running.")
@click.pass_context
@command_guard
def auth_connect(ctx, launch):
    launched = None
    try:
        resolve = backend.connect()
    except backend.ResolveConnectionError:
        if not launch:
            raise
        launched = backend.launch()
        raise RuntimeError(
            f"Resolve was launched as PID {launched['pid']}. Finish startup/sign-in, then run auth connect again."
        )
    emit(ctx, {"ok": True, "connected": True, "launched": launched, "application": backend.application_info(resolve)})


@cli.group()
def project():
    """List, inspect, open, create, save, import, and export projects."""


@project.command("list")
@click.pass_context
@command_guard
def project_list(ctx):
    manager = backend.connect().GetProjectManager()
    emit(ctx, {"folder": manager.GetCurrentFolder(), "projects": manager.GetProjectListInCurrentFolder(), "folders": manager.GetFolderListInCurrentFolder()})


@project.command("current")
@click.pass_context
@command_guard
def project_current(ctx):
    _, current = _resolve_and_project()
    emit(ctx, backend.project_summary(current))


@project.command("open")
@click.argument("name")
@click.pass_context
@command_guard
def project_open(ctx, name):
    resolve = backend.connect()
    return mutation(ctx, "project.open", {"name": name}, lambda: resolve.GetProjectManager().LoadProject(name).GetName())


@project.command("create")
@click.argument("name")
@click.option("--media-location", type=click.Path(path_type=Path))
@click.pass_context
@command_guard
def project_create(ctx, name, media_location):
    resolve = backend.connect()
    manager = resolve.GetProjectManager()
    return mutation(ctx, "project.create", {"name": name, "media_location": str(media_location) if media_location else None}, lambda: manager.CreateProject(name, str(media_location) if media_location else None).GetName())


@project.command("save")
@click.pass_context
@command_guard
def project_save(ctx):
    manager = backend.connect().GetProjectManager()
    return mutation(ctx, "project.save", {}, manager.SaveProject)


@project.command("export")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--with-stills/--without-stills", default=True)
@click.pass_context
@command_guard
def project_export(ctx, path, with_stills):
    resolve, current = _resolve_and_project()
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return mutation(ctx, "project.export", {"name": current.GetName(), "path": str(target), "with_stills": with_stills}, lambda: resolve.GetProjectManager().ExportProject(current.GetName(), str(target), with_stills))


@cli.group()
def media():
    """Inspect and import media pool assets."""


@media.command("list")
@click.pass_context
@command_guard
def media_list(ctx):
    _, current = _resolve_and_project()
    rows = backend.walk_folder(current.GetMediaPool().GetRootFolder())
    emit(ctx, {"count": len(rows), "items": rows})


@media.command("import")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.pass_context
@command_guard
def media_import(ctx, paths):
    _, current = _resolve_and_project()
    normalized = [str(path.resolve()) for path in paths]

    def execute():
        items = current.GetMediaPool().ImportMedia([{"FilePath": path} for path in normalized])
        return [item.GetUniqueId() for item in items] if items else None

    return mutation(ctx, "media.import", {"paths": normalized}, execute)


@cli.group()
def timeline():
    """Inspect and edit timelines."""


@timeline.command("list")
@click.pass_context
@command_guard
def timeline_list(ctx):
    _, current = _resolve_and_project()
    rows = [backend.timeline_summary(current.GetTimelineByIndex(index)) for index in range(1, int(current.GetTimelineCount()) + 1)]
    emit(ctx, {"count": len(rows), "timelines": rows})


@timeline.command("current")
@click.pass_context
@command_guard
def timeline_current(ctx):
    _, current = _resolve_and_project()
    active = current.GetCurrentTimeline()
    if not active:
        raise RuntimeError("No timeline is currently open")
    emit(ctx, backend.timeline_summary(active))


@timeline.command("create")
@click.argument("name")
@click.option("--clip", "clips", multiple=True, help="Media item name, id, or exact file path.")
@click.pass_context
@command_guard
def timeline_create(ctx, name, clips):
    _, current = _resolve_and_project()
    pool = current.GetMediaPool()

    def execute():
        if clips:
            items = backend.find_media_items(current, clips)
            created = pool.CreateTimelineFromClips(name, [{"mediaPoolItem": item} for item in items])
        else:
            created = pool.CreateEmptyTimeline(name)
        return created.GetUniqueId() if created else None

    return mutation(ctx, "timeline.create", {"name": name, "clips": list(clips)}, execute)


@timeline.command("append")
@click.option("--clip", "clips", multiple=True, required=True, help="Media item name, id, or exact file path.")
@click.option("--start-frame", type=int)
@click.option("--end-frame", type=int)
@click.pass_context
@command_guard
def timeline_append(ctx, clips, start_frame, end_frame):
    _, current = _resolve_and_project()
    pool = current.GetMediaPool()

    def execute():
        items = backend.find_media_items(current, clips)
        infos = []
        for item in items:
            info = {"mediaPoolItem": item}
            if start_frame is not None:
                info["startFrame"] = start_frame
            if end_frame is not None:
                info["endFrame"] = end_frame
            infos.append(info)
        added = pool.AppendToTimeline(infos)
        return [item.GetUniqueId() for item in added] if added else None

    return mutation(ctx, "timeline.append", {"clips": list(clips), "start_frame": start_frame, "end_frame": end_frame}, execute)


@timeline.command("title")
@click.argument("title_name")
@click.option("--fusion", is_flag=True)
@click.pass_context
@command_guard
def timeline_title(ctx, title_name, fusion):
    _, current = _resolve_and_project()
    active = current.GetCurrentTimeline()
    if not active:
        raise RuntimeError("No timeline is currently open")
    callback = lambda: (active.InsertFusionTitleIntoTimeline(title_name) if fusion else active.InsertTitleIntoTimeline(title_name)).GetUniqueId()
    return mutation(ctx, "timeline.title", {"title_name": title_name, "fusion": fusion}, callback)


@timeline.command("captions")
@click.option("--language", default="auto")
@click.option("--chars-per-line", type=int, default=42)
@click.pass_context
@command_guard
def timeline_captions(ctx, language, chars_per_line):
    resolve, current = _resolve_and_project()
    active = current.GetCurrentTimeline()
    if not active:
        raise RuntimeError("No timeline is currently open")
    language_value = getattr(resolve, f"AUTO_CAPTION_{language.upper()}", resolve.AUTO_CAPTION_AUTO)
    settings = {resolve.SUBTITLE_LANGUAGE: language_value, resolve.SUBTITLE_CHARS_PER_LINE: chars_per_line}
    return mutation(ctx, "timeline.captions", {"language": language, "chars_per_line": chars_per_line}, lambda: active.CreateSubtitlesFromAudio(settings))


@timeline.command("export")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--format", "export_format", type=click.Choice(["drt", "otio", "fcpxml", "csv", "edl"]), default="drt")
@click.pass_context
@command_guard
def timeline_export(ctx, path, export_format):
    resolve, current = _resolve_and_project()
    active = current.GetCurrentTimeline()
    if not active:
        raise RuntimeError("No timeline is currently open")
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    export_type = {"drt": resolve.EXPORT_DRT, "otio": resolve.EXPORT_OTIO, "fcpxml": resolve.EXPORT_FCPXML_1_10, "csv": resolve.EXPORT_TEXT_CSV, "edl": resolve.EXPORT_EDL}[export_format]
    return mutation(ctx, "timeline.export", {"path": str(target), "format": export_format}, lambda: active.Export(str(target), export_type, resolve.EXPORT_NONE))


@cli.group()
def render():
    """Configure and operate Resolve's native render queue."""


@render.command("capabilities")
@click.option("--format", "format_name")
@click.pass_context
@command_guard
def render_capabilities(ctx, format_name):
    _, current = _resolve_and_project()
    formats = current.GetRenderFormats()
    payload = {"presets": current.GetRenderPresetList(), "formats": formats, "current": current.GetCurrentRenderFormatAndCodec(), "resolutions": current.GetRenderResolutions()}
    if format_name:
        extension = formats.get(format_name, format_name)
        payload["codecs"] = current.GetRenderCodecs(extension)
    emit(ctx, payload)


@render.command("jobs")
@click.pass_context
@command_guard
def render_jobs(ctx):
    _, current = _resolve_and_project()
    jobs = current.GetRenderJobList() or []
    emit(ctx, {"count": len(jobs), "rendering": current.IsRenderingInProgress(), "jobs": jobs})


@render.command("add")
@click.option("--preset")
@click.option("--target-dir", required=True, type=click.Path(path_type=Path))
@click.option("--custom-name")
@click.option("--format", "format_name")
@click.option("--codec")
@click.pass_context
@command_guard
def render_add(ctx, preset, target_dir, custom_name, format_name, codec):
    _, current = _resolve_and_project()
    target = target_dir.resolve()

    def execute():
        target.mkdir(parents=True, exist_ok=True)
        if preset and not current.LoadRenderPreset(preset):
            raise RuntimeError(f"Render preset not found or could not be loaded: {preset}")
        if format_name and codec and not current.SetCurrentRenderFormatAndCodec(format_name, codec):
            raise RuntimeError(f"Resolve rejected format/codec: {format_name}/{codec}")
        settings = {"TargetDir": str(target), "SelectAllFrames": 1}
        if custom_name:
            settings["CustomName"] = custom_name
        if not current.SetRenderSettings(settings):
            raise RuntimeError("Resolve rejected render settings")
        return current.AddRenderJob()

    return mutation(ctx, "render.add", {"preset": preset, "target_dir": str(target), "custom_name": custom_name, "format": format_name, "codec": codec}, execute)


@render.command("start")
@click.argument("job_ids", nargs=-1)
@click.option("--interactive", is_flag=True)
@click.pass_context
@command_guard
def render_start(ctx, job_ids, interactive):
    _, current = _resolve_and_project()
    selected = list(job_ids) or [job.get("JobId") for job in (current.GetRenderJobList() or []) if job.get("JobId")]
    if not selected:
        raise ValueError("No render jobs are queued")
    return mutation(ctx, "render.start", {"job_ids": selected, "interactive": interactive}, lambda: current.StartRendering(selected, interactive))


@render.command("status")
@click.argument("job_id", required=False)
@click.pass_context
@command_guard
def render_status(ctx, job_id):
    _, current = _resolve_and_project()
    if job_id:
        return emit(ctx, {"rendering": current.IsRenderingInProgress(), "job_id": job_id, "status": current.GetRenderJobStatus(job_id)})
    jobs = current.GetRenderJobList() or []
    emit(ctx, {"rendering": current.IsRenderingInProgress(), "jobs": [{"job": job, "status": current.GetRenderJobStatus(job.get("JobId")) if job.get("JobId") else None} for job in jobs]})


def _preview_root() -> Path:
    return Path.home() / ".cli-anything" / "previews" / "davinci-resolve"


@cli.group()
def preview():
    """Publish truthful preview bundles from Resolve's native current frame."""


@preview.command("recipes")
@click.pass_context
def preview_recipes(ctx):
    emit(ctx, {"recipes": [{"name": "current-frame", "artifacts": ["hero", "pipeline-json"], "source": "Resolve ExportCurrentFrameAsStill"}]})


@preview.command("capture")
@click.option("--recipe", type=click.Choice(["current-frame"]), default="current-frame")
@click.option("--output-dir", type=click.Path(path_type=Path))
@click.option("--label")
@click.pass_context
@command_guard
def preview_capture(ctx, recipe, output_dir, label):
    resolve, current = _resolve_and_project()
    timeline_obj = current.GetCurrentTimeline()
    if not timeline_obj:
        raise RuntimeError("No timeline is currently open")
    now = datetime.now(timezone.utc)
    source = {"project": current.GetName(), "project_id": current.GetUniqueId(), "timeline": timeline_obj.GetName(), "timeline_id": timeline_obj.GetUniqueId(), "timecode": timeline_obj.GetCurrentTimecode()}
    fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    bundle = (output_dir or _preview_root() / current.GetUniqueId() / recipe / now.strftime("%Y%m%dT%H%M%SZ-%f")).resolve()
    artifacts = bundle / "artifacts"
    if ctx.find_root().obj.get("dry_run"):
        return emit(ctx, {"ok": True, "dry_run": True, "recipe": recipe, "bundle_dir": str(bundle), "source": source})
    artifacts.mkdir(parents=True, exist_ok=False)
    still = artifacts / "current-frame.jpg"
    # Resolve 21 only permits ExportCurrentFrameAsStill from an edit-capable
    # page. Preserve the operator's current page around the native export.
    previous_page = resolve.GetCurrentPage()
    exported = False
    try:
        if previous_page not in {"edit", "cut", "color", "fusion"}:
            resolve.OpenPage("edit")
        exported = bool(current.ExportCurrentFrameAsStill(str(still)))
    finally:
        if previous_page and resolve.GetCurrentPage() != previous_page:
            resolve.OpenPage(previous_page)
    if not exported or not still.is_file():
        raise RuntimeError("Resolve did not export the current frame; place the playhead on a visible video frame")
    pipeline_path = artifacts / "timeline.json"
    pipeline_path.write_text(json.dumps(backend.timeline_summary(timeline_obj), indent=2, default=_json_default), encoding="utf-8")
    manifest = {
        "protocol": "preview-bundle/v1", "bundle_id": bundle.name, "created_at": now.isoformat(), "recipe": recipe,
        "source": source, "source_fingerprint": fingerprint,
        "artifacts": [
            {"artifact_id": "current-frame", "role": "hero", "kind": "image", "media_type": "image/jpeg", "path": "artifacts/current-frame.jpg", "label": label or "Current Resolve frame", "bytes": still.stat().st_size},
            {"artifact_id": "timeline-state", "role": "pipeline-json", "kind": "json", "media_type": "application/json", "path": "artifacts/timeline.json", "label": "Timeline state", "bytes": pipeline_path.stat().st_size},
        ],
    }
    summary = {"headline": label or f"{current.GetName()} / {timeline_obj.GetName()}", "facts": source, "warnings": []}
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (bundle / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    latest = bundle.parent / "latest.json"
    latest.write_text(json.dumps({"bundle_dir": str(bundle), "manifest_path": str(bundle / "manifest.json"), "updated_at": now.isoformat()}, indent=2), encoding="utf-8")
    emit(ctx, {"ok": True, "status": "created", "bundle_dir": str(bundle), "manifest_path": str(bundle / "manifest.json"), "summary_path": str(bundle / "summary.json"), "artifact_count": 2})


@preview.command("latest")
@click.option("--project-id")
@click.option("--recipe", default="current-frame")
@click.pass_context
@command_guard
def preview_latest(ctx, project_id, recipe):
    if not project_id:
        _, current = _resolve_and_project()
        project_id = current.GetUniqueId()
    latest = _preview_root() / project_id / recipe / "latest.json"
    if not latest.is_file():
        raise FileNotFoundError(f"No preview exists: {latest}")
    emit(ctx, json.loads(latest.read_text(encoding="utf-8")))


@cli.command()
@click.pass_context
def repl(ctx):
    """Start an interactive command loop."""
    from prompt_toolkit import prompt
    from prompt_toolkit.history import FileHistory

    history = Path.home() / ".cli-anything" / "davinci-resolve-history"
    history.parent.mkdir(parents=True, exist_ok=True)
    click.echo("DaVinci Resolve CLI. Type 'help' or 'exit'.")
    while True:
        try:
            line = prompt("davinci> ", history=FileHistory(str(history))).strip()
        except (EOFError, KeyboardInterrupt):
            click.echo()
            break
        if not line:
            continue
        if line in {"exit", "quit"}:
            break
        if line == "help":
            click.echo(ctx.parent.get_help())
            continue
        try:
            args = shlex.split(line, posix=sys.platform != "win32")
            cli.main(args=args, prog_name="davinci", standalone_mode=False, obj=ctx.find_root().obj)
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)


def main():
    cli()


if __name__ == "__main__":
    main()
