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

    class FakeBackend:
        def __init__(self):
            self.history = ["stale"]

    class FakeClient:
        def __init__(self):
            self.backend = RuntimePathTests.FakeBackend()
            self.log_path = ""
            self.last_tools = "stale"

    class FakeAgent:
        def __init__(self):
            self.llmclient = RuntimePathTests.FakeClient()
            self.llmclients = [self.llmclient]
            self.history = ["stale"]
            self.log_path = ""
            self.aborted = False

        def abort(self):
            self.aborted = True

    def write_native_blocks(self, ga_paths, name, blocks):
        log_path = ga_paths.temp_path("model_responses", name)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        parts = []
        for label, body in blocks:
            parts.append(f"=== {label} ===\n{body}\n")
        log_path.write_text("".join(parts), encoding="utf-8")
        return log_path

    def native_prompt(self, text):
        return json.dumps(
            {"role": "user", "content": [{"type": "text", "text": text}]},
            ensure_ascii=False,
        )

    def native_tool_result_prompt(self):
        return json.dumps(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"},
                    {"type": "text", "text": "tool continuation should not draft"},
                ],
            },
            ensure_ascii=False,
        )

    def native_response(self, text="ok"):
        return repr([{"type": "text", "text": text}])

    def test_continue_restores_completed_history_and_trailing_prompt_as_draft(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            log_path = self.write_native_blocks(
                ga_paths,
                "model_responses_111111.txt",
                [
                    ("Prompt", self.native_prompt("first")),
                    ("Response", self.native_response("answer")),
                    ("Prompt", self.native_prompt("unfinished")),
                ],
            )
            original = log_path.read_text(encoding="utf-8")
            agent = self.FakeAgent()

            msg, ok = continue_cmd.continue_inplace(agent, str(log_path), allow_empty=True)

            self.assertTrue(ok, msg)
            self.assertEqual(agent.llmclient.backend.history[0]["content"][0]["text"], "first")
            self.assertEqual(agent.llmclient.backend.history[1]["content"][0]["text"], "answer")
            self.assertEqual(getattr(agent, "_continue_draft", ""), "unfinished")
            self.assertTrue(getattr(agent, "_continue_draft_trimmed", False))
            trimmed = log_path.read_text(encoding="utf-8")
            self.assertIn('"first"', trimmed)
            self.assertNotIn('"unfinished"', trimmed)
            self.assertLess(len(trimmed), len(original))

    def test_continue_restores_prompt_only_log_as_draft_only(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            log_path = self.write_native_blocks(
                ga_paths,
                "model_responses_222222.txt",
                [("Prompt", self.native_prompt("Say gm"))],
            )

            sessions = continue_cmd.list_sessions()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0][2], "Say gm")
            self.assertEqual(sessions[0][3], 0)
            self.assertEqual(continue_cmd.session_draft_tail(str(log_path)), "Say gm")
            self.assertEqual(continue_cmd.session_round_label(str(log_path), 0), "draft")

            agent = self.FakeAgent()
            msg, ok = continue_cmd.continue_inplace(agent, str(log_path), allow_empty=True)

            self.assertTrue(ok, msg)
            self.assertEqual(agent.llmclient.backend.history, [])
            self.assertEqual(getattr(agent, "_continue_draft", ""), "Say gm")
            self.assertTrue(getattr(agent, "_continue_draft_trimmed", False))
            self.assertNotIn("Say gm", log_path.read_text(encoding="utf-8"))

    def test_continue_ignores_tool_result_tail_as_draft(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            log_path = self.write_native_blocks(
                ga_paths,
                "model_responses_333333.txt",
                [
                    ("Prompt", self.native_prompt("first")),
                    ("Response", self.native_response("answer")),
                    ("Prompt", self.native_tool_result_prompt()),
                ],
            )
            before = log_path.read_text(encoding="utf-8")
            agent = self.FakeAgent()

            msg, ok = continue_cmd.continue_inplace(agent, str(log_path), allow_empty=True)

            self.assertTrue(ok, msg)
            self.assertEqual(getattr(agent, "_continue_draft", ""), "")
            self.assertFalse(getattr(agent, "_continue_draft_trimmed", False))
            self.assertEqual(log_path.read_text(encoding="utf-8"), before)

    def test_continue_copy_trims_only_copied_target(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            source = self.write_native_blocks(
                ga_paths,
                "model_responses_444444.txt",
                [
                    ("Prompt", self.native_prompt("first")),
                    ("Response", self.native_response("answer")),
                    ("Prompt", self.native_prompt("copy draft")),
                ],
            )
            before = source.read_text(encoding="utf-8")
            agent = self.FakeAgent()

            msg, ok = continue_cmd.continue_copy(agent, str(source), allow_empty=True)

            self.assertTrue(ok, msg)
            self.assertEqual(source.read_text(encoding="utf-8"), before)
            copied = Path(agent.log_path)
            self.assertNotEqual(copied, source)
            self.assertNotIn("copy draft", copied.read_text(encoding="utf-8"))
            self.assertEqual(getattr(agent, "_continue_draft", ""), "copy draft")
            self.assertEqual(continue_cmd.session_round_label(str(source), 1), "1轮 + draft")

    def test_continue_restore_prompt_only_succeeds_without_trimming(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            log_path = self.write_native_blocks(
                ga_paths,
                "model_responses_555555.txt",
                [("Prompt", self.native_prompt("restore draft"))],
            )
            before = log_path.read_text(encoding="utf-8")
            agent = self.FakeAgent()

            msg, full = continue_cmd.restore(agent, str(log_path))

            self.assertTrue(full, msg)
            self.assertEqual(agent.llmclient.backend.history, [])
            self.assertEqual(getattr(agent, "_continue_draft", ""), "restore draft")
            self.assertFalse(getattr(agent, "_continue_draft_trimmed", False))
            self.assertEqual(log_path.read_text(encoding="utf-8"), before)

    def test_continue_complete_native_log_has_no_draft_and_is_not_trimmed(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            log_path = self.write_native_log(ga_paths, name="model_responses_666666.txt", text="complete only")
            before = log_path.read_text(encoding="utf-8")
            agent = self.FakeAgent()

            msg, ok = continue_cmd.continue_inplace(agent, str(log_path), allow_empty=True)

            self.assertTrue(ok, msg)
            self.assertEqual(getattr(agent, "_continue_draft", ""), "")
            self.assertFalse(getattr(agent, "_continue_draft_trimmed", False))
            self.assertEqual(log_path.read_text(encoding="utf-8"), before)
            self.assertEqual(continue_cmd.session_round_label(str(log_path), 1), "1轮")

    def test_continue_list_shape_and_format_labels_include_draft_state(self):
        with tempfile.TemporaryDirectory() as d:
            ga_paths, modules = self.reload_runtime_modules(Path(d))
            continue_cmd = modules["frontends.continue_cmd"]
            ga_paths.ensure_runtime_dirs()
            self.write_native_blocks(
                ga_paths,
                "model_responses_777001.txt",
                [("Prompt", self.native_prompt("draft only"))],
            )
            self.write_native_blocks(
                ga_paths,
                "model_responses_777002.txt",
                [
                    ("Prompt", self.native_prompt("done")),
                    ("Response", self.native_response("ok")),
                    ("Prompt", self.native_prompt("tail draft")),
                ],
            )

            sessions = continue_cmd.list_sessions()
            self.assertTrue(all(len(item) == 4 for item in sessions))
            labels = {Path(path).name: continue_cmd.session_round_label(path, rounds)
                      for path, _mtime, _preview, rounds in sessions}
            self.assertEqual(labels["model_responses_777001.txt"], "draft")
            self.assertEqual(labels["model_responses_777002.txt"], "1轮 + draft")
            formatted = continue_cmd.format_list(sessions)
            self.assertIn("**draft**", formatted)
            self.assertIn("**1轮 + draft**", formatted)

    def test_tui2_continue_uses_shared_draft_label_and_prefill(self):
        source = Path("frontends/tuiapp_v2.py").read_text(encoding="utf-8")

        self.assertIn("session_round_label(path, n)", source)
        self.assertIn('getattr(sess.agent, "_continue_draft"', source)
        self.assertIn("self._rw_prefill_input(draft)", source)


if __name__ == "__main__":
    unittest.main()
