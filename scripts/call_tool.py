#!/usr/bin/env python3
"""调用 CSV Analyzer 的 agent 适配层，把统一信封 JSON 打到 stdout。

用法：
    python call_tool.py manifest
    python call_tool.py read <csv路径>
    python call_tool.py call <session_id> <tool_name> [key=value ...]
    python call_tool.py describe <session_id>

自包含设计：tools/、agent/、tool_registry.py 都随 skill 一起分发，
项目根目录就是本脚本所在 skill 目录的父目录，拖入任意位置即可用，
不依赖外部的 CSV-Analyzer 本体项目。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def find_project_root() -> Path:
    # skill 根目录 = scripts/ 的父目录，内含 agent/ 与 tool_registry.py
    root = Path(__file__).resolve().parent.parent
    if (root / "agent" / "runtime.py").exists() and (root / "tool_registry.py").exists():
        return root
    raise RuntimeError(
        "Self-contained skill is incomplete: agent/runtime.py or tool_registry.py "
        "missing next to scripts/. Reinstall the full skill package."
    )


def parse_kv(items: list[str]) -> dict:
    result = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected key=value, got: {item}")
        key, value = item.split("=", 1)
        try:
            result[key] = json.loads(value)
        except json.JSONDecodeError:
            result[key] = value
    return result


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "missing command"}, ensure_ascii=False))
        return 2
    command = sys.argv[1]
    try:
        root = find_project_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from agent import call_tool, describe_session, get_manifest

        if command == "manifest":
            payload = get_manifest()
        elif command == "read":
            payload = call_tool("", "read_dataset", file_path=sys.argv[2])
        elif command == "describe":
            payload = describe_session(sys.argv[2])
        elif command == "call":
            session_id, name = sys.argv[2], sys.argv[3]
            kwargs = parse_kv(sys.argv[4:])
            payload = call_tool(session_id, name, **kwargs)
        else:
            payload = {"error": f"unknown command: {command}"}
    except Exception as exc:
        payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
