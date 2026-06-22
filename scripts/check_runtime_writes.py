#!/usr/bin/env python3
"""Static policy guard for GenericAgent runtime write callsites.

ast-grep provides a broad structural first pass.  This verifier enforces the
repo-specific rule that maintenance writes must not derive from immutable app
roots; runtime state writes should flow through ga_paths helpers.  User-directed
agent file operations are intentionally allowed because their target is supplied
by the user/tool context, not by app-root maintenance paths.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = [
    "ga_paths.py",
    "ga.py",
    "agentmain.py",
    "llmcore.py",
    "assets/configure_mykey.py",
    "assets/agent_bbs.py",
    "ga_cli/__init__.py",
    "ga_cli/__main__.py",
    "ga_cli/cli.py",
]
RUNTIME_HELPERS = {
    "runtime_path",
    "memory_path",
    "temp_path",
    "runtime_asset_path",
    "extension_overlay_path",
    "mykey_py_path",
    "mykey_json_path",
    "boards_path",
    "bbs_files_path",
}
APP_ROOT_NAMES = {"script_dir", "PROJECT_DIR", "SCRIPT_DIR", "APP_ROOT", "app_dir", "_ROOT"}
WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "touch"}
COPY_FUNCTIONS = {"copy", "copy2", "copyfile"}

# User-directed file tools write arbitrary user paths relative to handler cwd.
USER_DIRECTED_ALLOW = {
    ("ga.py", "file_patch"),
    ("ga.py", "do_file_write"),
    ("ga.py", "do_web_execute_js"),
    ("ga.py", "code_run"),
}

# Reads or process launches are outside this write policy; specific safe writes
# that are not runtime maintenance writes are listed here with reason.
EXPLICIT_ALLOW = {
    ("ga.py", 5, "stdio fallback to os.devnull"),
    ("ga.py", 6, "stdio fallback to os.devnull"),
    ("agentmain.py", 3, "stdio fallback to os.devnull"),
    ("agentmain.py", 5, "stdio fallback to os.devnull"),
}


def rel(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def is_write_mode(node: ast.AST | None) -> bool:
    if node is None:
        return False
    value = string_value(node)
    return bool(value and any(ch in value for ch in "wax+"))


def contains_name(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(child, ast.Name) and child.id in names for child in ast.walk(node))


def contains_file_anchor(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Name) and child.id == "__file__" for child in ast.walk(node))


def uses_runtime_helper(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and call_name(child.func) in RUNTIME_HELPERS:
            return True
    return False


def suspicious_app_path(node: ast.AST) -> bool:
    return contains_name(node, APP_ROOT_NAMES) or contains_file_anchor(node)


def enclosing_function(parents: dict[ast.AST, ast.AST], node: ast.AST) -> str | None:
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
    return None


def check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    problems: list[str] = []
    relpath = rel(path)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line = getattr(node, "lineno", 0)
        fn = enclosing_function(parents, node)
        if (relpath, fn) in USER_DIRECTED_ALLOW or (relpath, line, "stdio fallback to os.devnull") in EXPLICIT_ALLOW:
            continue

        name = call_name(node.func)
        if name == "open":
            mode_node = node.args[1] if len(node.args) > 1 else None
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode_node = kw.value
            if is_write_mode(mode_node):
                target = node.args[0] if node.args else None
                if target is not None and suspicious_app_path(target) and not uses_runtime_helper(target):
                    problems.append(f"{relpath}:{line}: app-root-derived open(..., write mode) must use ga_paths runtime helpers")
        elif isinstance(node.func, ast.Attribute) and node.func.attr in WRITE_METHODS:
            target = node.func.value
            if suspicious_app_path(target) and not uses_runtime_helper(target):
                problems.append(f"{relpath}:{line}: app-root-derived {node.func.attr} call must use ga_paths runtime helpers")
        elif name in COPY_FUNCTIONS and len(node.args) >= 2:
            target = node.args[1]
            if suspicious_app_path(target) and not uses_runtime_helper(target):
                problems.append(f"{relpath}:{line}: app-root-derived {name} destination must use ga_paths runtime helpers")

    return problems


def run_ast_grep(ast_grep: str | None, targets: list[Path]) -> int:
    if not ast_grep:
        return 0
    rule = REPO_ROOT / "tools/static-checks/runtime-write-callsite.yml"
    cmd = [ast_grep, "scan", "--rule", str(rule), *[str(p) for p in targets]]
    result = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True)
    # ast-grep returns 1 when findings exist. Findings are expected because this
    # is a broad first pass; the Python verifier below is authoritative.
    if result.stdout.strip():
        print(result.stdout, end="")
    if result.stderr.strip():
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode not in (0, 1):
        return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ast-grep", default=None, help="optional ast-grep executable for first-pass structural scan")
    parser.add_argument("--self-test", action="store_true", help="also check positive/negative fixtures")
    parser.add_argument("paths", nargs="*", help="files to check; defaults to core runtime path files")
    args = parser.parse_args()

    target_paths = [REPO_ROOT / p for p in (args.paths or DEFAULT_TARGETS)]
    missing = [str(p) for p in target_paths if not p.exists()]
    if missing:
        print("Missing static-check targets:\n" + "\n".join(missing), file=sys.stderr)
        return 2

    ast_status = run_ast_grep(args.ast_grep, target_paths)
    if ast_status:
        return ast_status

    problems: list[str] = []
    for path in target_paths:
        problems.extend(check_file(path))

    if args.self_test:
        bad = REPO_ROOT / "tools/static-checks/fixtures/bad/app_root_write.py"
        good = REPO_ROOT / "tools/static-checks/fixtures/good/runtime_write.py"
        bad_problems = check_file(bad)
        good_problems = check_file(good)
        if not bad_problems:
            problems.append("negative fixture did not fail: bad/app_root_write.py")
        if good_problems:
            problems.extend(f"positive fixture failed: {p}" for p in good_problems)

    if problems:
        print("Runtime write policy violations:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("runtime write policy: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
