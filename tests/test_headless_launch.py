import subprocess
import sys
from pathlib import Path

from ga_cli.cli import COMMANDS


def test_launch_help_exposes_headless_without_importing_gui():
    result = subprocess.run(
        [sys.executable, "launch.pyw", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "--headless" in result.stdout
    assert "GA_HEADLESS=1" in result.stdout


def test_wechat_headless_cli_shortcuts_use_launch_headless():
    for command in ("wechat-headless", "wx-headless"):
        assert COMMANDS[command]["cmd"][-2:] == ["--headless", "--wechat"]


def test_wechat_headless_paths_are_runtime_writable():
    source = Path("frontends/wechatapp.py").read_text(encoding="utf-8")

    assert "TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)" in source
    assert "_TEMP_DIR = str(temp_path())" in source
    assert "open(os.path.join(_TEMP_DIR, 'wechatapp.log')" in source
