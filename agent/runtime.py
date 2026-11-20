"""Agent 工具统一调用入口：依赖守卫 + 执行 + 投影 + 统一信封。

call_tool(session_id, name, **kwargs) -> dict

新工具（read_dataset / override_types / get_manifest）在这里实现；
原有 9 个工具通过 tool_registry.resolve_tool 调用，原始结果存 session，
再交给 projections 里的对应函数投影成精简 JSON。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from . import context
from .manifest import _DEPENDENCY_POLICY, _SEMANTIC_GATES, get_manifest
from .projections import PROJECTORS


def _project_root() -> str:
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def _envelope(session_id: str, tool: str, status: str, summary: str,
              data: Any, warnings: list[str] | None = None,
              next_suggested_tools: list[str] | None = None) -> dict[str, Any]:
    return {
        "tool": tool,
        "status": status,
        "session_id": session_id,
        "summary": summary,
        "data": data,
        "warnings": warnings or [],
        "next_suggested_tools": next_suggested_tools or [],
    }


def _missing_prerequisites(session_id: str, tool: str) -> list[str]:
    policy = _DEPENDENCY_POLICY.get(tool, {"requires": [], "enhances_with": []})
    completed = set(context.get_meta(session_id).get("completed_tools", []))
    # read_dataset 是 session 建立动作本身，视为天然完成。
    completed.add("read_dataset")
    return [dep for dep in policy["requires"] if dep not in completed]


def _tool_kwargs(session_id: str, name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """把 session 里的 DataFrame / 中间结果注入原工具的参数。"""
    call_kwargs = dict(kwargs)
    call_kwargs.pop("session_id", None)
    call_kwargs.pop("file_path", None)
    call_kwargs.pop("output_dir", None)

    if name == "visualization":
        call_kwargs["df"] = context.load_analysis_df(session_id)
        call_kwargs["output_dir"] = kwargs.get("output_dir") or tempfile.mkdtemp(prefix="agent_viz_")
        call_kwargs["variable_types"] = context.get_meta(session_id).get("variable_types")
        for source, target in (
            ("correlation", "correlation_result"),
            ("time_analysis", "time_analysis"),
            ("time_series", "time_series_analysis"),
        ):
            if context.has_result(session_id, source):
                call_kwargs[target] = context.load_result(session_id, source)
        return call_kwargs

    call_kwargs["df"] = context.load_analysis_df(session_id)
    if name != "profile":
        variable_types = context.get_meta(session_id).get("variable_types")
        if variable_types is None and context.has_result(session_id, "profile"):
            variable_types = context.load_result(session_id, "profile").get("variable_types")
        if variable_types is not None:
            call_kwargs["variable_types"] = variable_types
    return call_kwargs


def _check_semantic_gate(session_id: str, name: str, kwargs: dict[str, Any]) -> str | None:
    if name not in _SEMANTIC_GATES:
        return None
    meta = context.get_meta(session_id)
    roles = meta.get("semantic_roles", {})
    requested = kwargs.get("datetime_column")
    if requested:
        role = roles.get(requested)
        if role and role != "event_time":
            return (
                f"Column '{requested}' has semantic role '{role}', not 'event_time'. "
                "Time-series analysis is only valid for observation timestamps. "
                "Use override_types to set the correct role, or choose another column."
            )
        return None
    if roles:
        event_columns = [column for column, role in roles.items() if role == "event_time"]
        if not event_columns:
            return (
                "No column has semantic role 'event_time'. Auto-detected datetime columns may be "
                "attribute dates (e.g. birth dates). Set an explicit event_time role with override_types."
            )
    return None


def _next_suggestions(session_id: str, name: str) -> list[str]:
    chain = {
        "read_dataset": ["profile"],
        "profile": ["override_types", "preprocess"],
        "override_types": ["preprocess"],
        "preprocess": ["statistics", "quality", "missingness", "time_analysis", "time_series", "correlation", "visualization"],
        "statistics": ["visualization"],
        "quality": ["preprocess"],
        "missingness": ["preprocess"],
        "time_analysis": ["visualization"],
        "time_series": ["visualization"],
        "correlation": ["visualization"],
        "visualization": [],
    }
    suggestions = list(chain.get(name, []))
    # 只建议未完成、且硬依赖已满足的工具。
    completed = set(context.get_meta(session_id).get("completed_tools", [])) | {"read_dataset"}
    result = []
    for tool in suggestions:
        if tool in completed:
            continue
        policy = _DEPENDENCY_POLICY.get(tool, {"requires": []})
        if all(dep in completed for dep in policy["requires"]):
            result.append(tool)
    return result


def call_tool(session_id: str, name: str, **kwargs: Any) -> dict[str, Any]:
    """统一调用入口。永不抛原始异常，错误信息放进信封的 status/data。"""
    _project_root()
    if name == "get_manifest":
        return _envelope(
            session_id, name, "ok", "Returned tool manifest.",
            get_manifest(), next_suggested_tools=["read_dataset"],
        )

    if name == "read_dataset":
        file_path = kwargs.get("file_path")
        if not file_path:
            return _envelope("", name, "error", "file_path is required.", {"error": "missing file_path"})
        try:
            new_id = context.open_session(file_path)
            import pandas as pd

            df = context.load_raw_df(new_id)
            preview = df.head(5)
            from analysis_result import _to_agent_safe

            data = {
                "source_file": file_path,
                "rows": int(df.shape[0]),
                "columns": [str(c) for c in df.columns],
                "dtypes": {str(c): str(t) for c, t in df.dtypes.items()},
                "first_5_rows": _to_agent_safe(preview.to_dict(orient="records")),
            }
            _next = ["profile"]
            return _envelope(new_id, name, "ok", f"Loaded {data['rows']} rows x {len(data['columns'])} columns.", data, next_suggested_tools=_next)
        except Exception as exc:
            return _envelope("", name, "error", f"Failed to read dataset: {exc}", {"error": str(exc)})

    try:
        meta = context.get_meta(session_id)
    except (KeyError, FileNotFoundError):
        return _envelope(session_id, name, "error", "Unknown session.", {"error": f"Unknown session: {session_id}"})

    if name == "override_types":
        roles = kwargs.get("roles", {}) or {}
        detected = kwargs.get("detected_types", {}) or {}
        variable_types = meta.get("variable_types")
        if variable_types is None and context.has_result(session_id, "profile"):
            variable_types = context.load_result(session_id, "profile").get("variable_types")
        if variable_types is None:
            return _envelope(
                session_id, name, "error", "profile not ready.",
                {"error": "Call profile before override_types."},
            )
        variable_types = {column: dict(info) for column, info in variable_types.items()}
        applied = []
        for column, role in roles.items():
            if column not in variable_types:
                continue
            variable_types[column]["semantic_role"] = role
            if role == "attribute_date":
                variable_types[column]["detected_type"] = "categorical"
                variable_types[column]["reason"] = "agent_override_attribute_date"
            elif role == "event_time":
                variable_types[column]["detected_type"] = "datetime"
                variable_types[column]["reason"] = "agent_override_event_time"
            elif role == "identifier":
                variable_types[column]["detected_type"] = "identifier"
                variable_types[column]["reason"] = "agent_override_identifier"
            applied.append({"column": column, "role": role})
        for column, selected in detected.items():
            if column not in variable_types:
                continue
            variable_types[column]["detected_type"] = selected
            variable_types[column]["reason"] = "agent_override_detected_type"
            applied.append({"column": column, "detected_type": selected})

        semantic_roles = dict(meta.get("semantic_roles", {}))
        semantic_roles.update({c: r for c, r in roles.items() if c in variable_types})
        context.update_meta(session_id, variable_types=variable_types, semantic_roles=semantic_roles)
        from analysis_result import _to_agent_safe

        return _envelope(
            session_id, name, "ok", f"Applied {len(applied)} type/role override(s).",
            {"applied": _to_agent_safe(applied), "variable_types": _to_agent_safe(variable_types)},
            next_suggested_tools=_next_suggestions(session_id, name),
        )

    if name not in PROJECTORS:
        return _envelope(session_id, name, "error", "Unknown tool.", {"error": f"Unknown tool: {name}"})

    missing = _missing_prerequisites(session_id, name)
    if missing:
        return _envelope(
            session_id, name, "blocked",
            f"Missing prerequisites: {missing}",
            {"missing_prerequisites": missing,
             "hint": "Call the listed tools first; enhances_with inputs are optional."},
        )

    gate_error = _check_semantic_gate(session_id, name, kwargs)
    if gate_error:
        return _envelope(session_id, name, "blocked", gate_error, {"reason": gate_error})

    try:
        if name == "profile":
            from tools.data_profiler import analyze_profile

            raw = analyze_profile(context.load_raw_df(session_id))
        else:
            from tool_registry import resolve_tool

            raw = resolve_tool(name)(**_tool_kwargs(session_id, name, kwargs))

        context.save_result(session_id, name, raw)
        if name == "visualization":
            # 原工具把 PNG 写进进程临时目录并注册 atexit 清理；跨进程调用后
            # 进程退出图片就没了。这里立刻复制到 session 目录持久化，并改写返回路径。
            from .context import figures_dir as _figures_dir
            target_dir = _figures_dir(session_id)
            rewritten = {}
            for key, value in raw.items():
                if isinstance(value, list) and value and all(
                    isinstance(item, str) and item.lower().endswith(".png") for item in value
                ):
                    copied = []
                    for item in value:
                        source = Path(item)
                        if source.exists():
                            destination = target_dir / source.name
                            destination.write_bytes(source.read_bytes())
                            copied.append(str(destination))
                        else:
                            copied.append(item)
                    rewritten[key] = copied
                elif isinstance(value, str) and value.lower().endswith(".png"):
                    source = Path(value)
                    if source.exists():
                        destination = target_dir / source.name
                        destination.write_bytes(source.read_bytes())
                        rewritten[key] = str(destination)
                    else:
                        rewritten[key] = value
                else:
                    rewritten[key] = value
            raw = rewritten
            context.save_result(session_id, name, raw)
        if name == "preprocess":
            _, transformations = raw
            context.update_meta(
                session_id,
                variable_types=meta.get("variable_types"),
                transformations=transformations,
            )
            context.save_processed_df(session_id, raw[0])
        if name == "profile" and not meta.get("variable_types"):
            context.update_meta(session_id, variable_types=raw.get("variable_types"))

        projected = PROJECTORS[name](raw, session_id=session_id)
        if name == "profile" and "suggested_semantic_roles" in projected.get("data", {}):
            context.update_meta(
                session_id,
                semantic_roles=dict(projected["data"]["suggested_semantic_roles"]),
            )
        context.mark_completed(session_id, name)
        return _envelope(
            session_id, name, "ok", projected["summary"], projected["data"],
            warnings=projected.get("warnings", []),
            next_suggested_tools=_next_suggestions(session_id, name),
        )
    except Exception as exc:
        return _envelope(
            session_id, name, "error", f"{type(exc).__name__}: {exc}",
            {"error": str(exc), "error_type": type(exc).__name__},
        )


def describe_session(session_id: str) -> dict[str, Any]:
    view = context.get_session(session_id)
    meta = context.get_meta(session_id)
    view["variable_types_ready"] = meta.get("variable_types") is not None
    view["notes"] = meta.get("notes", [])
    return view
