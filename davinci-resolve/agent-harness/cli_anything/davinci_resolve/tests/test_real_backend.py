import json
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("CLI_ANYTHING_DAVINCI_E2E") != "1",
    reason="set CLI_ANYTHING_DAVINCI_E2E=1 with Resolve running",
)


def _resolve_cli():
    command = shutil.which("cli-anything-davinci-resolve")
    if not command and os.name == "nt":
        candidate = Path(sysconfig.get_path("scripts")) / "cli-anything-davinci-resolve.exe"
        command = str(candidate) if candidate.is_file() else None
    if not command:
        pytest.fail("installed cli-anything-davinci-resolve command not found")
    print(f"[_resolve_cli] Using installed command: {command}")
    return command


def _run(*args):
    result = subprocess.run([_resolve_cli(), "--json", *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_real_read_surface():
    assert _run("doctor")["ok"] is True
    assert _run("auth", "status")["connected"] is True
    assert isinstance(_run("project", "list")["projects"], list)
    assert _run("project", "current")["name"]
    assert isinstance(_run("timeline", "list")["timelines"], list)
    assert "formats" in _run("render", "capabilities")


def test_real_preview_bundle(tmp_path):
    result = _run("preview", "capture", "--output-dir", str(tmp_path / "bundle"))
    manifest_path = Path(result["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["protocol"] == "preview-bundle/v1"
    for artifact in manifest["artifacts"]:
        assert (manifest_path.parent / artifact["path"]).is_file()
