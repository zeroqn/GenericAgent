from __future__ import annotations

import importlib
import json
import os
import sys
import subprocess
import types
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class RuntimePathTests(unittest.TestCase):
    def reload_ga_paths(self, tmpdir: Path):
        app_root = tmpdir / "app"
        app_root.mkdir(exist_ok=True)
        os.environ["GENERICAGENT_HOME"] = str(tmpdir / "runtime")
        os.environ["GENERICAGENT_APP_ROOT"] = str(app_root)
        import ga_paths
        return importlib.reload(ga_paths)

    def reload_runtime_modules(self, tmpdir: Path, names=None):
        ga_paths = self.reload_ga_paths(tmpdir)
        names = names or ("frontends.continue_cmd", "frontends.session_names")
        frontends_dir = str(Path.cwd() / "frontends")
        if frontends_dir not in sys.path:
            sys.path.insert(0, frontends_dir)
        for stale in ("chatapp_common", "continue_cmd", "session_names", "workspace_cmd"):
            sys.modules.pop(stale, None)
        modules = {}
        for name in names:
            sys.modules.pop(name, None)
            modules[name] = importlib.import_module(name)
        return ga_paths, modules

    def write_native_log(self, ga_paths, name="model_responses_123456.txt", text="hello runtime history"):
        log_path = ga_paths.temp_path("model_responses", name)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            '=== Prompt ===\n'
            f'{{"role":"user","content":[{{"type":"text","text":"{text}"}}]}}\n'
            '=== Response ===\n'
            '[{"type":"text","text":"ok"}]\n',
            encoding="utf-8",
        )
        return log_path

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
            self.assertEqual(captured["cmd"][1], str(ga_paths.APP_ROOT / "agentmain.py"))
            self.assertEqual(captured["cwd"], str(ga_paths.temp_path()))
            self.assertTrue(captured["waited"])

    def test_continue_lists_sessions_from_runtime_root(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            log_path = self.write_native_log(ga_paths)

            sessions = continue_cmd.list_sessions()

            self.assertEqual([Path(item[0]) for item in sessions], [log_path])
            self.assertEqual(sessions[0][2], "hello runtime history")
            self.assertEqual(sessions[0][3], 1)

    def test_continue_lists_empty_rewind_sessions_from_runtime_root(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            log_path = ga_paths.temp_path("model_responses", "model_responses_424242.txt")
            log_path.write_text("", encoding="utf-8")
            tree_dir = ga_paths.temp_path(".ga_rewind", "model_responses_424242")
            tree_dir.mkdir(parents=True)
            (tree_dir / "tree.json").write_text(
                json.dumps(
                    {
                        "nodes": {
                            "origin": {"kind": "origin", "title": "会话起点"},
                            "v1": {"kind": "checkpoint", "title": "lost history session"},
                        },
                        "head": "v1",
                        "root": "origin",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            sessions = continue_cmd.list_sessions(rewind_root=str(ga_paths.temp_path(".ga_rewind")))

            self.assertEqual([Path(item[0]) for item in sessions], [log_path])
            self.assertEqual(sessions[0][2], "[世界线] lost history session")
            self.assertEqual(sessions[0][3], 1)

    def test_tui2_uses_runtime_temp_path_authority(self):
        source = Path("frontends/tuiapp_v2.py").read_text(encoding="utf-8")

        self.assertIn("from ga_paths import temp_path", source)
        self.assertIn("temp_path('.ga_rewind')", source)
        self.assertNotIn("FRONTENDS_DIR, '..', 'temp'", source)
        self.assertNotIn('ROOT_DIR, "temp"', source)
        self.assertNotIn("ROOT_DIR, 'temp'", source)


    def test_continue_does_not_hide_non_native_logs_without_summary(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            log_path = ga_paths.temp_path("model_responses", "model_responses_777777.txt")
            log_path.write_text(
                "=== Prompt === 2026-06-27 00:00:00\n"
                "protocol preface\n"
                "=== USER ===\n"
                "please keep this legacy session visible\n"
                "=== ASSISTANT ===\n"
                "\n"
                "=== Response === 2026-06-27 00:00:01 model=legacy\n"
                "plain answer without summary tags\n",
                encoding="utf-8",
            )

            sessions = continue_cmd.list_sessions()

            self.assertEqual([Path(item[0]) for item in sessions], [log_path])
            self.assertEqual(sessions[0][2], "please keep this legacy session visible")
            self.assertEqual(sessions[0][3], 1)

    def test_session_names_sidecar_lives_under_runtime_root(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            session_names = modules["frontends.session_names"]
            ga_paths.ensure_runtime_dirs()
            log_path = ga_paths.temp_path("model_responses", "model_responses_654321.txt")
            log_path.write_text("non-empty session log", encoding="utf-8")

            session_names.set_name(str(log_path), "important")

            reg_path = ga_paths.temp_path("model_responses", "session_names.json")
            self.assertTrue(reg_path.is_file())
            self.assertFalse((ga_paths.APP_ROOT / "temp" / "model_responses" / "session_names.json").exists())
            self.assertEqual(Path(session_names.path_for("important")), log_path)

    def test_restore_finds_runtime_root_logs(self):
        with tempfile.TemporaryDirectory() as d:
            agentmain = types.ModuleType("agentmain")
            class DummyGeneraticAgent:
                def _handle_slash_cmd(self, raw_query, display_queue):
                    return raw_query
            agentmain.GeneraticAgent = DummyGeneraticAgent
            with mock.patch.dict(sys.modules, {"agentmain": agentmain}):
                ga_paths, modules = self.reload_runtime_modules(Path(d), ("chatapp_common",))
            chatapp_common = modules["chatapp_common"]
            ga_paths.ensure_runtime_dirs()
            log_path = ga_paths.temp_path("model_responses", "model_responses_123456.txt")
            log_path.write_text("=== USER ===\nrestore me\n=== Response ===\nok\n", encoding="utf-8")

            restored, err = chatapp_common.format_restore()

            self.assertIsNone(err)
            self.assertEqual(restored[1], log_path.name)
            self.assertEqual(restored[2], 1)
            self.assertTrue(any("restore me" in line for line in restored[0]))

    def test_workspace_temp_paths_live_under_runtime_root(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d), ("frontends.workspace_cmd",))
            workspace_cmd = modules["frontends.workspace_cmd"]
            ga_paths.ensure_runtime_dirs()

            self.assertEqual(Path(workspace_cmd._temp_root()), ga_paths.temp_path())
            self.assertEqual(Path(workspace_cmd._projects_root()), ga_paths.temp_path("projects"))
            self.assertEqual(Path(workspace_cmd._registry_path()), ga_paths.temp_path("workspaces.json"))
            self.assertEqual(Path(workspace_cmd._session_map_path()), ga_paths.temp_path("session_workspaces.json"))

    def test_project_mode_plugin_uses_runtime_temp(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d), ("plugins.project_mode",))
            project_mode = modules["plugins.project_mode"]
            ga_paths.ensure_runtime_dirs()

            self.assertEqual(Path(project_mode._TEMP), ga_paths.temp_path())
            self.assertEqual(Path(project_mode._project_dir("demo")), ga_paths.temp_path("projects", "demo"))
            self.assertEqual(Path(project_mode._mem_path("demo")), ga_paths.temp_path("projects", "demo", "project_memory.md"))

    def test_l4_session_archiver_reads_runtime_logs_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d), ("memory.L4_raw_sessions.compress_session",))
            compress_session = modules["memory.L4_raw_sessions.compress_session"]
            ga_paths.ensure_runtime_dirs()

            self.assertEqual(Path(compress_session.RAW_DIR), ga_paths.temp_path("model_responses"))


if __name__ == "__main__":
    unittest.main()
