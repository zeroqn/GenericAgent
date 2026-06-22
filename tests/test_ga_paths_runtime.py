from __future__ import annotations

import importlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class RuntimePathTests(unittest.TestCase):
    def reload_ga_paths(self, tmpdir: Path):
        os.environ["GENERICAGENT_HOME"] = str(tmpdir / "runtime")
        os.environ["GENERICAGENT_APP_ROOT"] = str(Path.cwd())
        import ga_paths
        return importlib.reload(ga_paths)

    def test_ensure_runtime_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths = self.reload_ga_paths(Path(d))
            ga_paths.ensure_runtime_dirs()
            for rel in ["memory", "temp", "temp/model_responses", "bbs_files", "assets/tmwd_cdp_bridge"]:
                self.assertTrue((ga_paths.RUNTIME_ROOT / rel).is_dir(), rel)

    def test_cli_launch_uses_app_paths_and_runtime_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths = self.reload_ga_paths(Path(d))
            ga_paths.ensure_runtime_dirs()

            import ga_cli.cli as cli
            importlib.reload(cli)

            captured = {}

            class DummyProcess:
                def wait(self):
                    captured["waited"] = True
                def terminate(self):
                    captured["terminated"] = True

            def fake_popen(cmd, cwd=None):
                captured["cmd"] = cmd
                captured["cwd"] = cwd
                return DummyProcess()

            with mock.patch.object(subprocess, "Popen", fake_popen):
                cli.launch_frontend(["python", "{PROJECT_DIR}/agentmain.py"])
            self.assertEqual(captured["cmd"][1], str(Path.cwd() / "agentmain.py"))
            self.assertEqual(captured["cwd"], str(ga_paths.temp_path()))
            self.assertTrue(captured["waited"])


if __name__ == "__main__":
    unittest.main()
