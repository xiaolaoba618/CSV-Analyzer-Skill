"""Agent session 上下文：把 DataFrame 与中间结果存在磁盘，供多次独立调用共享。

Skill 场景下，每次工具调用往往是一个全新的 Python 进程，内存字典无法跨进程。
这里把 session 落盘到系统临时目录，用 session_id 做键：

    <temp>/csv_agent_sessions/<session_id>/
        meta.json          来源文件、创建时间、已完成工具、语义角色
        raw_df.pkl         原始 DataFrame
        processed_df.pkl   预处理后的 DataFrame（可选）
        results/<tool>.pkl 各工具的原始返回（含 DataFrame，供 visualization 使用）

session 只保存数据，不负责并发锁；单 agent 串行调用即可。
"""
from __future__ import annotations

import json
import pickle
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SESSION_ROOT = Path(tempfile.gettempdir()) / "csv_agent_sessions"


def _root() -> Path:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    return SESSION_ROOT


def _session_dir(session_id: str) -> Path:
    path = _root() / session_id
    if not path.exists():
        raise KeyError(f"Unknown session: {session_id}")
    return path


def _meta_path(session_id: str) -> Path:
    return _session_dir(session_id) / "meta.json"


def new_session_id() -> str:
    return f"s{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"


def create_session(source_file: str | Path, raw_df: Any, meta: dict[str, Any] | None = None) -> str:
    session_id = new_session_id()
    path = _root() / session_id
    path.mkdir(parents=True)
    (path / "results").mkdir()
    with open(path / "raw_df.pkl", "wb") as handle:
        pickle.dump(raw_df, handle, protocol=pickle.HIGHEST_PROTOCOL)
    payload = {
        "session_id": session_id,
        "source_file": str(source_file),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_tools": [],
        "semantic_roles": {},
        "variable_types": None,
        "notes": [],
    }
    if meta:
        payload.update(meta)
    _write_meta(session_id, payload)
    return session_id


def _write_meta(session_id: str, meta: dict[str, Any]) -> None:
    with open(_meta_path(session_id), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2, default=str)


def get_meta(session_id: str) -> dict[str, Any]:
    with open(_meta_path(session_id), "r", encoding="utf-8") as handle:
        return json.load(handle)


def update_meta(session_id: str, **changes: Any) -> dict[str, Any]:
    meta = get_meta(session_id)
    meta.update(changes)
    _write_meta(session_id, meta)
    return meta


def mark_completed(session_id: str, tool_name: str) -> dict[str, Any]:
    meta = get_meta(session_id)
    if tool_name not in meta["completed_tools"]:
        meta["completed_tools"].append(tool_name)
    _write_meta(session_id, meta)
    return meta


def open_session(source_file: str | Path) -> str:
    """读取 CSV 并建立 session。使用项目既有的加载器，保证列名清洗规则一致。"""
    import sys

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from tools.data_loader import load_and_prepare_csv

    raw_df, loading_info = load_and_prepare_csv(source_file)
    return create_session(source_file, raw_df, {"loading_info": loading_info})


def get_session(session_id: str) -> dict[str, Any]:
    """返回 session 的轻量视图（不含 DataFrame 本体）。"""
    meta = get_meta(session_id)
    return {
        "session_id": session_id,
        "source_file": meta["source_file"],
        "created_at": meta["created_at"],
        "completed_tools": list(meta["completed_tools"]),
        "has_processed_df": (_session_dir(session_id) / "processed_df.pkl").exists(),
        "semantic_roles": dict(meta.get("semantic_roles", {})),
    }


def list_sessions() -> list[dict[str, Any]]:
    if not _root().exists():
        return []
    result = []
    for path in sorted(_root().iterdir()):
        if path.is_dir() and (path / "meta.json").exists():
            result.append(get_session(path.name))
    return result


def load_raw_df(session_id: str) -> Any:
    with open(_session_dir(session_id) / "raw_df.pkl", "rb") as handle:
        return pickle.load(handle)


def figures_dir(session_id: str) -> Path:
    """session 内持久化图表的目录。"""
    path = _session_dir(session_id) / "figures"
    path.mkdir(exist_ok=True)
    return path


def save_processed_df(session_id: str, df: Any) -> None:
    with open(_session_dir(session_id) / "processed_df.pkl", "wb") as handle:
        pickle.dump(df, handle, protocol=pickle.HIGHEST_PROTOCOL)


def load_analysis_df(session_id: str) -> Any:
    processed = _session_dir(session_id) / "processed_df.pkl"
    if processed.exists():
        with open(processed, "rb") as handle:
            return pickle.load(handle)
    return load_raw_df(session_id)


def save_result(session_id: str, tool_name: str, result: Any) -> None:
    path = _session_dir(session_id) / "results" / f"{tool_name}.pkl"
    with open(path, "wb") as handle:
        pickle.dump(result, handle, protocol=pickle.HIGHEST_PROTOCOL)


def load_result(session_id: str, tool_name: str) -> Any:
    path = _session_dir(session_id) / "results" / f"{tool_name}.pkl"
    if not path.exists():
        raise KeyError(f"Tool result not found in session: {tool_name}")
    with open(path, "rb") as handle:
        return pickle.load(handle)


def has_result(session_id: str, tool_name: str) -> bool:
    return (_session_dir(session_id) / "results" / f"{tool_name}.pkl").exists()


def close_session(session_id: str) -> bool:
    import shutil

    path = _session_dir(session_id)
    shutil.rmtree(path, ignore_errors=True)
    return True
