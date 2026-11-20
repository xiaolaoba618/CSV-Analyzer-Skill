"""示例：Agent / Skill 如何按流程使用适配层。

运行：
    python agent/example_flow.py <csv路径>

这个脚本演示三个关键行为：
1. 先 read_dataset 拿表头，再 profile；
2. profile 建议的语义角色里，出生日期会自动标成 attribute_date；
3. 用户只要画图时，profile 之后直接 visualization 即可，无需跑全套统计。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import call_tool, get_manifest  # noqa: E402


def main(csv_path: str) -> None:
    manifest = get_manifest()
    print(f"Available tools ({manifest['tool_count']}):")
    for tool in manifest["tools"]:
        meta = tool["agent_meta"]
        print(f"  - {tool['function']['name']:16} requires={meta['requires']}")

    result = call_tool("", "read_dataset", file_path=csv_path)
    print(f"\n[read_dataset] {result['status']}: {result['summary']}")
    session_id = result["session_id"]
    if result["status"] != "ok":
        return

    profile = call_tool(session_id, "profile")
    print(f"[profile] {profile['status']}: {profile['summary']}")
    roles = profile["data"].get("suggested_semantic_roles", {})
    print("  suggested roles:", json.dumps(roles, ensure_ascii=False))

    attribute_dates = [c for c, role in roles.items() if role == "attribute_date"]
    event_times = [c for c, role in roles.items() if role == "event_time"]
    print(f"  attribute dates: {attribute_dates}")
    print(f"  event times:     {event_times}")

    if event_times:
        series = call_tool(session_id, "time_series", datetime_column=event_times[0])
        print(f"[time_series] {series['status']}: {series['summary']}")
    else:
        blocked = call_tool(session_id, "time_series", datetime_column=(attribute_dates or [None])[0])
        print(f"[time_series] {blocked['status']}: {blocked['summary']}")

    viz = call_tool(session_id, "visualization")
    print(f"[visualization] {viz['status']}: {viz['summary']}")
    print("  next suggested:", viz["next_suggested_tools"])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
