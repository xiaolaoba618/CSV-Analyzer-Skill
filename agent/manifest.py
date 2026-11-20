"""生成给 Agent / Skill 使用的标准工具清单（JSON Schema）。

区别于 tool_registry 的原始元数据：
- 每个工具有完整的 parameters JSON Schema，可直接喂给 function calling；
- 依赖拆成 requires（硬前置，缺了不能跑）与 enhances_with（有则更好）；
- 带上语义门控说明，例如 time_series 只接受 event_time 角色。

原始 tool_registry 不改动，这里做增量适配。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.2.0"

_SESSION_ID = {
    "type": "string",
    "description": "session_id，由 read_dataset 返回；后续所有工具靠它共享数据",
}


def _load_registry() -> dict[str, dict[str, Any]]:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from tool_registry import get_tool_registry

    return get_tool_registry()


# 硬依赖：缺了就没法执行；增强输入：有则结果更完整，没有也能跑。
_DEPENDENCY_POLICY: dict[str, dict[str, list[str]]] = {
    "profile": {"requires": ["read_dataset"], "enhances_with": []},
    "preprocess": {"requires": ["profile"], "enhances_with": []},
    "statistics": {"requires": ["profile"], "enhances_with": ["preprocess"]},
    "quality": {"requires": ["profile"], "enhances_with": ["preprocess"]},
    "missingness": {"requires": ["profile"], "enhances_with": ["preprocess"]},
    "time_analysis": {"requires": ["profile"], "enhances_with": ["preprocess"]},
    "time_series": {"requires": ["profile"], "enhances_with": ["preprocess"]},
    "correlation": {"requires": ["profile"], "enhances_with": ["preprocess"]},
    "visualization": {
        "requires": ["profile"],
        "enhances_with": ["preprocess", "statistics", "correlation", "time_analysis", "time_series"],
    },
}

_SEMANTIC_GATES: dict[str, str] = {
    "time_series": (
        "Only columns with semantic role 'event_time' may serve as the time axis. "
        "'attribute_date' (e.g. birth date, hire date) must be rejected."
    ),
    "time_analysis": "Same event_time rule as time_series.",
}

_AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_dataset",
        "description": "Read a CSV file, clean column names, and create a session. Returns header names, dtypes, first 5 rows, and a session_id. Use this FIRST for any dataset task.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the CSV file"},
            },
            "required": ["file_path"],
        },
        "requires": [],
        "enhances_with": [],
        "when_to_use": "Always call first before any other analysis tool.",
    },
    {
        "name": "override_types",
        "description": "Correct semantic types of columns after inspecting headers. Set role to 'event_time' for observation timestamps, 'attribute_date' for birth/hire dates, or override detected_type to numeric/categorical/identifier/datetime/time_index.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": _SESSION_ID,
                "roles": {
                    "type": "object",
                    "description": "Map column name to semantic role: event_time | attribute_date | measure | dimension | identifier | none",
                    "additionalProperties": {
                        "type": "string",
                        "enum": ["event_time", "attribute_date", "measure", "dimension", "identifier", "none"],
                    },
                },
                "detected_types": {
                    "type": "object",
                    "description": "Optional: map column name to detected_type for downstream tools",
                    "additionalProperties": {
                        "type": "string",
                        "enum": ["numeric", "categorical", "categorical_numeric", "binary", "boolean", "identifier", "datetime", "time_index", "text"],
                    },
                },
            },
            "required": ["session_id"],
        },
        "requires": ["read_dataset"],
        "enhances_with": ["profile"],
        "when_to_use": "Use when header names imply a different semantic role than auto-detection, especially to prevent birth dates being treated as time-series axes.",
    },
    {
        "name": "get_manifest",
        "description": "Return this tool manifest itself. Lists all available tools, their JSON Schemas, dependencies, and semantic gates.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires": [],
        "enhances_with": [],
        "when_to_use": "Use to discover available tools and their prerequisites.",
    },
]


def _schema_tool(spec: dict[str, Any], name: str) -> dict[str, Any]:
    policy = _DEPENDENCY_POLICY.get(name, {"requires": [], "enhances_with": []})
    properties: dict[str, Any] = {"session_id": _SESSION_ID}
    if name == "visualization":
        properties["output_dir"] = {
            "type": "string",
            "description": "Optional directory for PNG files; defaults to a temp dir",
        }
        properties["plot_types"] = {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["histogram", "boxplot", "bar", "scatter", "heatmap",
                         "time_hourly", "time_weekday", "time_daily", "time_series"],
            },
            "description": (
                "Only generate these chart types. Omit or pass all for every chart. "
                "Choose based on data and user request: e.g. ['scatter'] when asked about "
                "relationships, ['time_series'] for trends, ['bar'] for category composition."
            ),
        }
    if name == "time_series":
        properties["datetime_column"] = {
            "type": "string",
            "description": "Optional; must have semantic role event_time",
        }
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": spec.get("description", ""),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["session_id"],
            },
        },
        "agent_meta": {
            "category": spec.get("category"),
            "when_to_use": spec.get("when_to_use", ""),
            "requires": policy["requires"],
            "enhances_with": policy["enhances_with"],
            "changes_data": spec.get("changes_data", False),
            "outputs": spec.get("outputs", []),
            "semantic_gate": _SEMANTIC_GATES.get(name),
        },
    }


def get_manifest() -> dict[str, Any]:
    registry = _load_registry()
    tools = []
    for spec in _AGENT_TOOLS:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
                "agent_meta": {
                    "when_to_use": spec["when_to_use"],
                    "requires": spec["requires"],
                    "enhances_with": spec["enhances_with"],
                    "changes_data": spec["name"] in {"read_dataset", "override_types"},
                    "outputs": [],
                    "semantic_gate": None,
                },
            }
        )
    for name, spec in registry.items():
        tools.append(_schema_tool(spec, name))
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_count": len(tools),
        "tools": tools,
        "notes": [
            "All tools except read_dataset/get_manifest take session_id.",
            "requires = hard prerequisites; enhances_with = optional inputs that enrich results.",
            "Call read_dataset first, then profile, then override_types if semantics look wrong.",
            "time_series/time_analysis only accept event_time columns as the time axis.",
        ],
    }
