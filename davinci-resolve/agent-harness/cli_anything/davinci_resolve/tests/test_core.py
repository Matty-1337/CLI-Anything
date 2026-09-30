import json
from pathlib import Path

from click.testing import CliRunner

from cli_anything.davinci_resolve.davinci_resolve_cli import cli, mutation
from cli_anything.davinci_resolve.utils import config


def test_locked_config_roundtrip(tmp_path):
    target = tmp_path / "config.json"
    config.save_config({"resolve_exe": "C:/Resolve.exe"}, target)
    config.save_config({"script_api": "C:/Scripting"}, target)
    assert config.load_config(target) == {
        "resolve_exe": "C:/Resolve.exe",
        "script_api": "C:/Scripting",
    }


def test_cli_help_lists_production_groups():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("auth", "configure", "doctor", "fusion", "media", "preview", "project", "render", "timeline"):
        assert command in result.output


def test_configure_paths_read_only(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "default_config_path", lambda: tmp_path / "config.json")
    result = CliRunner().invoke(cli, ["--json", "configure", "paths"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "paths" in payload


def test_dry_run_never_calls_mutation_callback():
    runner = CliRunner()

    @click_command_for_test
    def command(ctx):
        return mutation(ctx, "test.action", {"value": 1}, lambda: (_ for _ in ()).throw(AssertionError("called")))

    result = runner.invoke(command, ["--dry-run", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["dry_run"] is True


def click_command_for_test(function):
    import click

    @click.command()
    @click.option("--dry-run", is_flag=True)
    @click.option("--json", "use_json", is_flag=True)
    @click.pass_context
    def wrapped(ctx, dry_run, use_json):
        ctx.ensure_object(dict)
        ctx.obj.update(dry_run=dry_run, json=use_json)
        return function(ctx)

    return wrapped
