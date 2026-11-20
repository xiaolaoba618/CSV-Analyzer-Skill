"""把工具的原始返回投影成给 Agent 看的精简 JSON。

设计原则：
- 纯投影，不重算；原始结果仍完整存在 session 里供别的工具使用；
- 复用 analysis_result._to_agent_safe 做 pandas/numpy 序列化基座；
- 每个工具只保留 Agent 决策所需的信息，长数组一律截断并标注；
- 统一信封由 runtime 套上，这里只返回 summary / data / warnings。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

DEFAULT_TOP_N = 10
DEFAULT_SERIES_PREVIEW = 5


def _safe(value: Any) -> Any:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from analysis_result import _to_agent_safe

    return _to_agent_safe(value)


def _cap(items: list[Any], limit: int) -> tuple[list[Any], bool]:
    if len(items) <= limit:
        return items, False
    return items[:limit], True


def _truncate_view(view: dict[str, Any], top_n: int = DEFAULT_TOP_N) -> dict[str, Any]:
    """递归截断已知的长数组字段，保留结构。"""
    warnings: list[str] = []

    def walk(node: Any, path: str = "") -> Any:
        if isinstance(node, dict):
            result = {}
            for key, value in node.items():
                result[key] = walk(value, f"{path}.{key}" if path else key)
            return result
        if isinstance(node, list):
            capped, truncated = _cap(node, top_n)
            if truncated:
                warnings.append(f"{path}: {len(node)} items -> {top_n} shown")
                return capped + [{"__truncated__": True, "total": len(node), "shown": top_n}]
            return [walk(item, path) for item in capped]
        return node

    return {"data": walk(view), "warnings": warnings}


# 列名里出现这些词，即使被识别成日期也通常是「实体属性」而非「观测时间轴」。
_ATTRIBUTE_DATE_HINTS = (
    "birth", "dob", "born", "出生", "生辰", "hire", "入职", "入职日期",
    "graduat", "毕业", "expiry", "expire", "到期", "有效期",
)


def project_profile(raw: dict[str, Any], **_: Any) -> dict[str, Any]:
    dataset = raw.get("dataset", {})
    columns = raw.get("columns", [])
    types = raw.get("variable_types", {})
    type_counts = raw.get("type_counts", {})

    roles: dict[str, str] = {}
    for name, info in types.items():
        detected = info.get("detected_type", "")
        lowered = str(name).lower()
        if detected in {"datetime", "time_index"}:
            if any(hint in lowered for hint in _ATTRIBUTE_DATE_HINTS):
                roles[name] = "attribute_date"
            else:
                roles[name] = "event_time"
        elif detected in {"numeric", "categorical_numeric", "binary", "boolean"}:
            roles[name] = "measure" if detected in {"numeric", "categorical_numeric"} else "dimension"
        elif detected == "identifier":
            roles[name] = "identifier"
        else:
            roles[name] = "dimension"

    top_columns, truncated = _cap(columns, 30)
    return {
        "summary": (
            f"{dataset.get('rows', 0)} rows x {dataset.get('columns', 0)} columns; "
            f"types: {type_counts}"
        ),
        "data": {
            "dataset": dataset,
            "columns": _safe(top_columns),
            "variable_types": _safe(types),
            "type_counts": type_counts,
            "suggested_semantic_roles": roles,
        },
        "warnings": (["columns truncated to 30 shown"] if truncated else [])
        + ["Semantic roles are heuristics; confirm with override_types, especially for date columns."],
    }


def project_preprocess(raw: tuple[Any, dict[str, Any]], **_: Any) -> dict[str, Any]:
    _, transformations = raw
    numeric = transformations.get("numeric", {})
    datetime = transformations.get("datetime", {})
    time = transformations.get("time", {})
    combined = transformations.get("combined_datetime")

    changed = []
    skipped = []
    for group in (numeric, datetime, time):
        for column, result in group.items():
            if result.get("status") == "ok":
                changed.append({"column": column, "kind": group is numeric and "numeric" or group is datetime and "datetime" or "time"})
            else:
                skipped.append({"column": column, "reason": result.get("reason")})
    if isinstance(combined, dict) and combined.get("status") != "ok":
        skipped.append({"column": "combined_datetime", "reason": combined.get("reason")})

    return {
        "summary": f"Preprocessed {len(changed)} columns, skipped {len(skipped)} operations.",
        "data": {"changed": _safe(changed), "skipped": _safe(skipped), "combined_datetime": _safe(combined)},
        "warnings": [],
    }


def project_statistics(raw: dict[str, Any], **_: Any) -> dict[str, Any]:
    numeric = raw.get("numeric", {})
    categorical = raw.get("categorical", {})
    view = {"numeric": numeric, "categorical": categorical}
    result = _truncate_view(_safe(view), top_n=DEFAULT_TOP_N)
    return {
        "summary": f"Statistics computed for {len(numeric)} numeric and {len(categorical)} categorical variables.",
        "data": result["data"],
        "warnings": result["warnings"],
    }


def project_quality(raw: dict[str, Any], **_: Any) -> dict[str, Any]:
    result = _truncate_view(_safe(raw), top_n=DEFAULT_TOP_N)
    summary = raw.get("summary", {})
    return {
        "summary": f"Quality checked: {summary.get('rows', 0)} rows, {summary.get('columns', 0)} columns.",
        "data": result["data"],
        "warnings": result["warnings"],
    }


def project_missingness(raw: dict[str, Any], **_: Any) -> dict[str, Any]:
    summary = raw.get("summary", {})
    result = _truncate_view(_safe(raw), top_n=DEFAULT_TOP_N)
    return {
        "summary": (
            f"Missing: {summary.get('total_missing', 0)} cells / {summary.get('total_cells', 0)}, "
            f"complete rows {summary.get('complete_row_ratio', 0):.1%}."
        ),
        "data": result["data"],
        "warnings": result["warnings"],
    }


def project_time_analysis(raw: dict[str, Any], **_: Any) -> dict[str, Any]:
    result = _truncate_view(_safe(raw), top_n=DEFAULT_TOP_N)
    columns = raw.get("datetime_columns", [])
    return {
        "summary": f"Time analysis for {len(columns)} datetime column(s): {columns}.",
        "data": result["data"],
        "warnings": result["warnings"],
    }


def project_time_series(raw: dict[str, Any], **_: Any) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    warnings: list[str] = []
    if raw.get("status") != "ok":
        return {
            "summary": f"Time series skipped: {raw.get('reason', 'unknown')}",
            "data": _safe(raw),
            "warnings": [],
        }
    preview["status"] = raw.get("status")
    preview["datetime_column"] = raw.get("datetime_column")
    preview["numeric_columns"] = raw.get("numeric_columns")
    preview["time_range"] = _safe(raw.get("time_range"))
    preview["frequency"] = _safe(raw.get("frequency"))

    original = raw.get("original_series", {}).get("variables", {})
    series_view = {}
    for column, info in original.items():
        series_view[column] = {
            "status": info.get("status"),
            "count": info.get("count"),
            "mean": info.get("mean"),
            "trend": _safe(info.get("trend")),
            "anomalies": {
                "anomaly_count": info.get("anomalies", {}).get("anomaly_count", 0),
                "anomalies": _safe(info.get("anomalies", {}).get("anomalies", [])[:DEFAULT_SERIES_PREVIEW]),
            },
        }
    preview["original_series"] = {"variables": series_view}

    aggregation = raw.get("aggregation", {}).get("variables", {})
    agg_view = {}
    for column, info in aggregation.items():
        results = info.get("results", [])
        shown, truncated = _cap(results, DEFAULT_SERIES_PREVIEW)
        agg_view[column] = {
            "frequency": info.get("frequency"),
            "total_points": len(results),
            "preview": _safe(shown),
        }
        if truncated:
            warnings.append(f"aggregation.{column}: {len(results)} points -> {DEFAULT_SERIES_PREVIEW} preview")
    preview["aggregation"] = {"variables": agg_view}

    return {
        "summary": (
            f"Time series on {raw.get('datetime_column')}: "
            f"{len(raw.get('numeric_columns', []))} variables, "
            f"frequency {raw.get('frequency', {}).get('detected')}."
        ),
        "data": preview,
        "warnings": warnings + ["Full series kept in session; use raw results for plotting."],
    }


def _pair_rows(section: dict[str, Any]) -> list[dict[str, Any]]:
    return section.get("skipped_pairs", [])[:DEFAULT_TOP_N]


def project_correlation(raw: dict[str, Any], **_: Any) -> dict[str, Any]:
    important = raw.get("important_pairs", {})
    methods = {}
    for method in ("pearson", "spearman", "kendall"):
        section = raw.get(method, {})
        methods[method] = {
            "status": section.get("status"),
            "reason": section.get("reason"),
            "matrix_shape": _safe(section.get("correlation")),
            "skipped_pairs_count": len(section.get("skipped_pairs", [])),
        }
    pairs = {method: _safe(items) for method, items in important.items()}
    return {
        "summary": f"Correlation computed for {len(raw.get('numeric_columns', []))} numeric variables; status {raw.get('status')}.",
        "data": {"methods": methods, "important_pairs": pairs, "numeric_columns": raw.get("numeric_columns", [])},
        "warnings": ["Full correlation/p-value matrices kept in session (raw results), not in this payload."],
    }


def project_visualization(raw: dict[str, Any], **_: Any) -> dict[str, Any]:
    counts = {}
    for key, value in raw.items():
        if isinstance(value, list):
            counts[key] = len(value)
        elif isinstance(value, str) and value:
            counts[key] = 1
    return {
        "summary": "Figures generated: " + ", ".join(f"{k}={v}" for k, v in counts.items()),
        "data": _safe(raw),
        "warnings": [],
    }


PROJECTORS = {
    "profile": project_profile,
    "preprocess": project_preprocess,
    "statistics": project_statistics,
    "quality": project_quality,
    "missingness": project_missingness,
    "time_analysis": project_time_analysis,
    "time_series": project_time_series,
    "correlation": project_correlation,
    "visualization": project_visualization,
}
