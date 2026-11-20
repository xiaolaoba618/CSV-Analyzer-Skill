"""面向 Agent / Skill 的适配层。

这一层不改动既有 tools/ 与报告链路，只提供：
- 持久化的 session（跨进程共享 DataFrame 与中间结果）
- 标准 JSON Schema 工具清单（manifest）
- 每个工具的精简投影（projections）
- 依赖守卫与统一调用入口（runtime）

用法：
    from agent import open_session, call_tool, get_manifest
    sid = open_session("data.csv")
    print(call_tool(sid, "profile", "profile")["summary"])
"""
from .context import open_session, close_session, get_session, list_sessions
from .runtime import call_tool, get_manifest, describe_session

__all__ = [
    "open_session",
    "close_session",
    "get_session",
    "list_sessions",
    "call_tool",
    "get_manifest",
    "describe_session",
]
