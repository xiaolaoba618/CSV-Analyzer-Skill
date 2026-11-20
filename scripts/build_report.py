#!/usr/bin/env python3
"""把收集到的工具信封 JSON 渲染成精良、自包含的 HTML 洞察报告。

用法：
    python build_report.py <collected.json> <output.html> [charts_dir]

collected.json 是一个列表，元素为 call_tool.py 返回的信封 dict。
图表 PNG 以 base64 内嵌，报告可独立分享、无外部依赖。
报告以"用户问题 / 关键发现"为中心组织，而不是罗列工具日志。
"""
from __future__ import annotations

import base64
import html
import json
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "assets" / "report_template.html"

# 图表分区：key -> (中文标题, 角标, 说明)
CHART_GROUPS = [
    ("histograms",      "变量分布",       "分布",   "各数值变量的取值分布，观察集中趋势与形态"),
    ("boxplots",        "离散与异常",     "箱线",   "四分位与离群点，识别异常值"),
    ("categorical_bars","类别构成",       "构成",   "分类变量各类别的数量占比"),
    ("scatter_plots",   "变量关系",       "相关",   "数值变量两两之间的散点关系"),
    ("correlation_heatmap", "相关性热力", "矩阵",   "变量间相关强度一览"),
    ("time_hourly",     "小时分布",       "时间",   "观测在一天 24 小时内的分布"),
    ("time_weekday",    "星期分布",       "时间",   "观测在一周内的分布"),
    ("time_daily",      "每日分布",       "时间",   "观测按日期的分布"),
    ("time_series",     "时间趋势",       "趋势",   "随时间变化的趋势与异常"),
]


def esc(value) -> str:
    return html.escape(str(value if value is not None else "—"))


def fmt(value, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:,.{digits}g}"
    if isinstance(value, int):
        return f"{value:,}"
    return esc(value)


def pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (ValueError, TypeError):
        return esc(value)


def kv_block(pairs: list[tuple[str, str]]) -> str:
    return '<div class="kv">' + "".join(
        f"<div><b>{esc(k)}</b><span>{v}</span></div>" for k, v in pairs
    ) + "</div>"


def table_block(headers: list[str], rows: list[list[str]], numeric_cols: set[int] | None = None) -> str:
    numeric_cols = numeric_cols or set()
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(
            f'<td class="{"num" if i in numeric_cols else ""}">{c}</td>' for i, c in enumerate(row)
        ) + "</tr>" for row in rows
    )
    return (f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def insight(icon: str, text: str, kind: str = "") -> str:
    cls = f" {kind}" if kind else ""
    return f'<div class="insight{cls}"><div class="ico">{icon}</div><div class="txt">{text}</div></div>'


# app 式章节头：小标签 + 大标题 + 说明
def sec_head(anchor: str, eyebrow: str, title: str, desc: str = "") -> str:
    parts = [f'<section id="{anchor}">',
             f'<p class="sec-eyebrow">{esc(eyebrow)}</p>',
             f'<h2 class="sec-title">{esc(title)}</h2>']
    if desc:
        parts.append(f'<p class="sec-desc">{esc(desc)}</p>')
    return "".join(parts)


# ---------------------------------------------------------------- 数据索引
def index_envelopes(envelopes: list[dict]) -> dict:
    idx = {"read": None, "profile": None, "overrides": [], "tools": {}, "viz": None,
           "statuses": [], "context": None}
    for env in envelopes:
        name = env.get("tool", "")
        status = env.get("status", "")
        idx["statuses"].append((name, status, env.get("summary", "")))
        data = env.get("data") or {}
        # __context__ 不影响工具依赖判断，先单独取走
        if name == "__context__":
            idx["context"] = data
            continue
        if status != "ok":
            continue
        if name == "read_dataset":
            idx["read"] = data
        elif name == "profile":
            idx["profile"] = data
        elif name == "override_types":
            idx["overrides"].append(data)
        elif name == "visualization":
            idx["viz"] = env
        else:
            idx["tools"][name] = data
    return idx


# ---------------------------------------------------------------- 章节渲染
def _auto_verdict(idx: dict) -> tuple[str, list[str]]:
    """没有 agent 显式结论时，从工具结果拼一句面向普通人看的结论 + 要点。"""
    read = idx["read"] or {}
    profile = idx["profile"] or {}
    dataset = profile.get("dataset", {})
    rows = dataset.get("rows") or read.get("rows")
    cols = dataset.get("columns") or len(read.get("columns", []))
    headline = f"这份表共有 {fmt(rows)} 行、{fmt(cols)} 个字段。"
    points: list[str] = []

    # 时间趋势：说人话
    ts = idx["tools"].get("time_series")
    if ts:
        freq = (ts.get("frequency") or {}).get("detected")
        trend_map = {"increasing": "上升", "decreasing": "下降"}
        bits = []
        for col, info in (ts.get("original_series") or {}).get("variables", {}).items():
            d = (info.get("trend") or {}).get("direction")
            if d in trend_map:
                bits.append(f"{esc(col)}呈{trend_map[d]}趋势")
        if bits:
            headline += "从时间上看，" + "、".join(bits[:3]) + "。"
            points.append(f"数据按 <b>{esc(freq or '时间')}</b> 节奏记录，"
                          f"其中 {'、'.join(bits[:3])}，可据此判断走势。")

    # 相关性：说人话
    corr = idx["tools"].get("correlation")
    if corr:
        pairs = corr.get("important_pairs", {})
        top = []
        for method, items in pairs.items():
            if isinstance(items, list) and items:
                it = items[0]
                try:
                    top.append((abs(float(it.get("correlation") or 0)), it, method))
                except (ValueError, TypeError):
                    pass
        if top:
            _, it, method = max(top, key=lambda x: x[0])
            strength = esc(it.get("strength", ""))
            headline += f"{esc(it.get('variable_1'))} 与 {esc(it.get('variable_2'))} 存在明显关联。"
            points.append(f"关系最密切的是 <b>{esc(it.get('variable_1'))}</b> 与 "
                          f"<b>{esc(it.get('variable_2'))}</b>（{esc(method)} 相关系数 "
                          f"<b>{fmt(it.get('correlation'))}</b>{'，'+strength if strength else ''}）。")

    # 数据质量 / 缺失：说人话
    miss = idx["tools"].get("missingness")
    if miss:
        s = miss.get("summary", {}) or {}
        ratio = s.get("overall_missing_ratio")
        if ratio is not None:
            try:
                ok = float(ratio) < 0.05
                txt = ("数据比较完整，可直接用于分析。" if ok else
                       f"存在约 <b>{pct(ratio)}</b> 的缺失，使用时需注意。")
                points.append(txt)
            except (ValueError, TypeError):
                pass
    qual = idx["tools"].get("quality")
    if qual:
        cc = qual.get("constant_columns", {})
        if isinstance(cc, dict) and cc.get("count"):
            points.append(f"有 <b>{fmt(cc.get('count'))}</b> 个字段取值始终不变，对分析帮助不大。")

    # 只有统计、没有其它结论时
    if "statistics" in idx["tools"] and not points:
        points.append("已完成各字段的描述统计，重点数值见「分析发现」。")

    if not points:
        points.append("已按本次需求完成分析，细节见下方各章节。")
    # 收尾一句：把语义修正的注意点带出来
    if idx["overrides"]:
        points.append("分析前已根据字段含义修正了个别数据类型，详见「数据理解」。")
    return headline, points[:4]


def render_verdict(idx: dict) -> str:
    """报告开头：面向大众的一句话结论。优先用 agent 写的 __context__。"""
    ctx = idx.get("context") or {}
    question = ctx.get("user_question")
    headline = ctx.get("plain_conclusion")
    points = ctx.get("points")

    if not headline or not points:
        auto_head, auto_points = _auto_verdict(idx)
        headline = headline or auto_head
        if not points:
            points = auto_points
    if isinstance(points, str):
        points = [points]
    points = [p for p in (points or []) if p][:5]

    def rich(s) -> str:
        """要点/结论来自自动生成或 agent 的 __context__，均含受控的 <b> 加粗，允许原样输出。"""
        s = str(s)
        return s if ("<" in s and ">" in s) else esc(s)

    parts = ['<section class="verdict" id="verdict">']
    parts.append('<span class="v-tag">✦ 结论速览</span>')
    parts.append('<div class="v-badge">💡</div>')
    if question:
        parts.append(f'<p class="v-q"><b>你关心的是：</b>{esc(question)}</p>')
    parts.append(f'<h2 class="v-head">{rich(headline)}</h2>')
    if points:
        parts.append('<ul class="v-points">' + "".join(
            f"<li>{rich(p)}</li>" for p in points
        ) + '</ul>')
    parts.append('</section>')
    return "".join(parts)


def render_overview(idx: dict) -> str:
    """执行摘要：KPI + 自动提炼的关键发现。"""
    read = idx["read"] or {}
    profile = idx["profile"] or {}
    dataset = profile.get("dataset", {})
    rows = dataset.get("rows") or read.get("rows")
    cols = dataset.get("columns") or len(read.get("columns", []))
    type_counts = profile.get("type_counts", {})
    numeric_n = type_counts.get("numeric", 0)
    cat_n = type_counts.get("categorical", 0)
    missing = dataset.get("missing_values")
    if missing is None:
        missing = (read.get("loading_info") or {}).get("missing_values")
    dup = dataset.get("duplicate_rows")

    kpis = [
        ("数据规模", f"{fmt(rows)} <span style='font-size:.5em;color:var(--text-soft)'>行</span>", f"{fmt(cols)} 个字段"),
        ("数值变量", fmt(numeric_n), "可做统计与相关性分析"),
        ("分类变量", fmt(cat_n), "可做构成与分组分析"),
        ("缺失单元格", fmt(missing), f"重复行 {fmt(dup)}" if dup is not None else "完整性概览"),
    ]

    findings: list[str] = []
    # 从实际工具结果里提炼，不虚构
    miss_data = idx["tools"].get("missingness")
    if miss_data:
        s = miss_data.get("summary", {})
        ratio = s.get("overall_missing_ratio")
        complete = s.get("complete_row_ratio")
        if ratio is not None:
            complete_enough = float(ratio) < 0.05
            level = "数据较完整" if complete_enough else "存在一定缺失"
            kind = "ok" if complete_enough else "warn"
            findings.append(insight("✓",
                f"<b>数据完整性：</b>整体缺失率 <b>{pct(ratio)}</b>，完整行占 <b>{pct(complete)}</b>。{level}。",
                kind))
    qual = idx["tools"].get("quality")
    if qual:
        cc = qual.get("constant_columns", {})
        if isinstance(cc, dict) and cc.get("count"):
            findings.append(insight("!",
                f"<b>常量列：</b>发现 <b>{fmt(cc.get('count'))}</b> 个取值恒定的列"
                f"（{esc(', '.join(map(str, cc.get('columns', []))[:5]))}），通常不提供区分信息。", "warn"))
        dups = qual.get("duplicates", {})
        if isinstance(dups, dict) and dups.get("count"):
            findings.append(insight("!",
                f"<b>重复行：</b>检测到 <b>{fmt(dups.get('count'))}</b> 行重复记录，建议核对。", "warn"))
    corr = idx["tools"].get("correlation")
    if corr:
        pairs = corr.get("important_pairs", {})
        top = []
        for method, items in pairs.items():
            if isinstance(items, list) and items:
                it = items[0]
                top.append((abs(float(it.get("correlation") or 0)), it, method))
        if top:
            _, it, method = max(top, key=lambda x: x[0])
            findings.append(insight("∿",
                f"<b>最强关联：</b>{esc(it.get('variable_1'))} 与 {esc(it.get('variable_2'))} 的 "
                f"{esc(method)} 相关系数为 <b>{fmt(it.get('correlation'))}</b>"
                f"（{esc(it.get('strength', ''))}，{esc(it.get('direction', ''))}）。"))
    ts = idx["tools"].get("time_series")
    if ts:
        freq = (ts.get("frequency") or {}).get("detected")
        variables = (ts.get("original_series") or {}).get("variables", {})
        trend_bits = []
        for col, info in variables.items():
            d = (info.get("trend") or {}).get("direction")
            if d and d not in ("stable", "unknown"):
                trend_bits.append(f"{esc(col)} 呈{esc({'increasing':'上升','decreasing':'下降'}.get(d, d))}趋势")
        msg = f"<b>时间规律：</b>检测到 <b>{esc(freq or '未知')}</b> 频率"
        if trend_bits:
            msg += "；" + "，".join(trend_bits[:3])
        findings.append(insight("↗", msg + "。"))

    if not findings:
        findings.append(insight("i", "已按本次需求完成分析，详细结果见下方各章节。"))

    body = [sec_head("overview", "OVERVIEW", "数据概览",
                     "数据规模与本次分析中最值得关注的结果")]
    body.append('<div class="kpi-grid">' + "".join(
        f'<div class="kpi"><div class="label">{esc(k)}</div><div class="value">{v}</div>'
        f'<div class="hint">{esc(h)}</div></div>' for k, v, h in kpis
    ) + '</div>')
    body.append('<div class="insights" style="margin-top:16px">' + "".join(findings) + '</div>')
    body.append('</section>')
    return "".join(body)


def render_understanding(idx: dict) -> str:
    read = idx["read"] or {}
    profile = idx["profile"] or {}
    roles = profile.get("suggested_semantic_roles", {})
    type_counts = profile.get("type_counts", {})
    label_map = {"event_time": "事件时间", "attribute_date": "属性日期", "measure": "度量",
                 "dimension": "维度", "identifier": "标识符"}
    css_map = {"event_time": "event", "attribute_date": "attr", "measure": "measure",
               "dimension": "dimension", "identifier": "identifier"}
    tags = "".join(
        f'<span class="tag {css_map.get(role, "other")}"><span class="dot"></span>'
        f'{esc(col)} · {esc(label_map.get(role, role))}</span>' for col, role in roles.items()
    )
    type_str = "、".join(f"{esc(k)} {fmt(v)} 个" for k, v in type_counts.items())

    body = [sec_head("understand", "VARIABLES", "数据中的变量",
                     "字段语义角色的判断，决定后续能否做时间序列等分析")]
    body.append('<div class="card">')
    if tags:
        body.append('<h3 style="margin:0 0 6px;font-size:14px;color:var(--text-soft)">语义角色</h3>')
        body.append(f'<div class="tags">{tags}</div>')
    body.append(f'<p class="note">类型分布：{type_str}</p>')

    # 列清单
    columns = read.get("columns", [])
    dtypes = read.get("dtypes", {})
    first = read.get("first_5_rows") or []
    if columns:
        rows = [[esc(c), esc(dtypes.get(c, "—"))] for c in columns]
        body.append(table_block(["字段", "数据类型"], rows))
    # 前 5 行样例
    if first:
        keys = list(first[0].keys()) if isinstance(first[0], dict) else []
        if keys:
            srows = [[esc(str(r.get(k, ""))[:40]) for k in keys] for r in first[:5]]
            body.append('<h3 style="font-size:14px;margin:16px 0 4px">样例数据（前 5 行）</h3>')
            body.append(table_block(keys, srows))

    # 语义修正说明
    if idx["overrides"]:
        body.append('<h3 style="font-size:14px;margin:16px 0 4px">语义修正</h3><ul>')
        for item in idx["overrides"]:
            for a in item.get("applied", []):
                body.append(f'<li>{esc(a.get("column"))} → {esc(a.get("role") or a.get("detected_type"))}</li>')
        body.append('</ul>')
    body.append('</div></section>')
    return "".join(body)


def render_findings(idx: dict) -> str:
    tools = idx["tools"]
    body = [sec_head("findings", "FINDINGS", "分析发现", "本次实际执行的分析结果")]
    any_section = False

    # 统计
    if "statistics" in tools:
        any_section = True
        data = tools["statistics"]
        body.append('<div class="card"><h3 style="margin-top:0">描述统计</h3>')
        numeric = data.get("numeric", {})
        if numeric:
            rows = []
            for col, s in numeric.items():
                rows.append([esc(col), fmt(s.get("count")), fmt(s.get("mean")), fmt(s.get("median")),
                             fmt(s.get("std")), fmt(s.get("min")), fmt(s.get("max"))])
            body.append(table_block(["变量", "计数", "均值", "中位数", "标准差", "最小", "最大"],
                                    rows, numeric_cols={1, 2, 3, 4, 5, 6}))
        cat = data.get("categorical", {})
        if cat:
            body.append('<h3 style="font-size:14px;margin:16px 0 4px">分类变量</h3>')
            crows = []
            for col, s in cat.items():
                top = (s.get("frequencies") or [])[:5]
                freq = "、".join(f"{esc(f.get('category'))} {fmt(f.get('count'))}" for f in top)
                crows.append([esc(col), fmt(s.get("unique")), freq])
            body.append(table_block(["变量", "唯一值数", "高频取值"], crows, numeric_cols={1}))
        body.append('</div>')

    # 相关性
    if "correlation" in tools:
        any_section = True
        data = tools["correlation"]
        body.append('<div class="card"><h3 style="margin-top:0">变量相关性</h3>')
        rows = []
        for method, items in (data.get("important_pairs") or {}).items():
            for it in (items or [])[:6]:
                rows.append([esc(method), esc(it.get("variable_1")), esc(it.get("variable_2")),
                             fmt(it.get("correlation")), esc(it.get("strength", "")),
                             esc(it.get("direction", ""))])
        body.append(table_block(["方法", "变量 1", "变量 2", "相关系数", "强度", "方向"], rows,
                                numeric_cols={3}) if rows else '<p class="note">无显著相关对</p>')
        body.append('<p class="note">完整相关矩阵保留在 session 中；此处展示关键相关对。</p></div>')

    # 时间序列
    if "time_series" in tools:
        any_section = True
        data = tools["time_series"]
        freq = (data.get("frequency") or {}).get("detected", "—")
        tr = data.get("time_range") or {}
        body.append('<div class="card"><h3 style="margin-top:0">时间序列分析</h3>')
        body.append(kv_block([
            ("时间轴", esc(data.get("datetime_column"))),
            ("检测频率", esc(freq)),
            ("起始", esc(tr.get("min_datetime"))),
            ("结束", esc(tr.get("max_datetime"))),
        ]))
        variables = (data.get("original_series") or {}).get("variables", {})
        if variables:
            rows = []
            for col, info in variables.items():
                trend = info.get("trend") or {}
                anom = info.get("anomalies") or {}
                rows.append([esc(col), fmt(info.get("count")), fmt(info.get("mean")),
                             esc(trend.get("direction", "—")), fmt(trend.get("slope")),
                             fmt((anom.get("anomaly_count", 0)))])
            body.append(table_block(["变量", "点数", "均值", "趋势", "斜率", "异常数"], rows,
                                    numeric_cols={1, 2, 4, 5}))
        body.append('</div>')

    # 质量 / 缺失
    for name, title in (("quality", "数据质量"), ("missingness", "缺失分析")):
        if name in tools:
            any_section = True
            data = tools[name]
            body.append(f'<div class="card"><h3 style="margin-top:0">{title}</h3>')
            summary = data.get("summary", {})
            if isinstance(summary, dict) and summary:
                pairs = [(esc(k), fmt(v)) for k, v in summary.items()
                         if isinstance(v, (str, int, float, bool, type(None)))]
                if pairs:
                    body.append(kv_block(pairs))
            if name == "quality":
                for key, label in (("constant_columns", "常量列"), ("high_cardinality", "高基数列"),
                                   ("outliers", "异常值")):
                    val = data.get(key)
                    if isinstance(val, dict) and val:
                        cnt = val.get("count")
                        if cnt is not None:
                            body.append(f'<p class="note">{label}：{fmt(cnt)} 项</p>')
            else:
                patterns = data.get("missing_patterns") or []
                if patterns:
                    prows = [[esc("、".join(p.get("missing_columns", []))), fmt(p.get("missing_count")),
                              pct(p.get("missing_ratio"))] for p in patterns[:8]]
                    body.append('<h3 style="font-size:14px;margin:14px 0 4px">缺失模式</h3>')
                    body.append(table_block(["缺失字段组合", "行数", "占比"], prows, numeric_cols={1, 2}))
            body.append('</div>')

    # 预处理
    if "preprocess" in tools:
        any_section = True
        data = tools["preprocess"]
        trans = data.get("transformations", {})
        if trans:
            body.append('<div class="card"><h3 style="margin-top:0">预处理</h3>')
            rows = []
            for group, items in trans.items():
                if isinstance(items, dict):
                    for col, info in items.items():
                        if isinstance(info, dict):
                            rows.append([esc(group), esc(col), esc(info.get("status", "—")),
                                         esc(info.get("new_dtype", info.get("reason", "—")))])
            if rows:
                body.append(table_block(["类别", "字段", "状态", "结果"], rows))
            body.append('</div>')

    if not any_section:
        # 没跑统计就不渲染这一节，不塞空话
        return ""
    body.append('</section>')
    return "".join(body)


def _collect_png_paths(data: dict) -> list[Path]:
    found: list[Path] = []
    def walk(node):
        if isinstance(node, str) and node.lower().endswith(".png"):
            p = Path(node)
            if p.exists():
                found.append(p)
        elif isinstance(node, list):
            for it in node:
                walk(it)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
    walk(data)
    seen, unique = set(), []
    for p in found:
        if str(p) not in seen:
            seen.add(str(p))
            unique.append(p)
    return unique


def render_charts(idx: dict, charts_dir: Path | None) -> str:
    viz = idx["viz"]
    data = (viz.get("data") if viz else {}) or {}
    png_paths = _collect_png_paths(data)
    if not png_paths and charts_dir and charts_dir.exists():
        png_paths = sorted(charts_dir.rglob("*.png"))
    if not png_paths:
        return ""

    # 按 key 归类（用文件名特征匹配）
    def classify(path: Path) -> str:
        n = path.stem.lower()
        if n.endswith("_hist"):
            return "histograms"
        if n.endswith("_box"):
            return "boxplots"
        if n.endswith("_bar"):
            return "categorical_bars"
        if "_vs_" in n and n.endswith("_scatter"):
            return "scatter_plots"
        if "heatmap" in n or "correlation" in n:
            return "correlation_heatmap"
        if "_hourly" in n:
            return "time_hourly"
        if "_weekday" in n:
            return "time_weekday"
        if "_daily" in n:
            return "time_daily"
        if n.startswith("time_series") or "_trend" in n:
            return "time_series"
        return "other"

    grouped: dict[str, list[Path]] = {}
    for p in png_paths:
        grouped.setdefault(classify(p), []).append(p)

    def friendly(path: Path) -> str:
        n = path.stem
        repl = {"_hist": " 分布", "_box": " 箱线图", "_bar": " 构成",
                "_scatter": "", "_vs_": " × ", "_hourly": " 小时分布",
                "_weekday": " 星期分布", "_daily": " 每日分布",
                "time_series_": "趋势 · ", "spearman_correlation_heatmap": "Spearman 相关热力图"}
        for k, v in repl.items():
            n = n.replace(k, v)
        return n.strip() or path.stem

    parts = []
    shown_groups = [g for g in CHART_GROUPS if grouped.get(g[0])]
    if grouped.get("other"):
        shown_groups.append(("other", "其他图表", "图", "按需生成的其它图表"))
    part_count = sum(len(grouped[g[0]]) for g in shown_groups)
    parts.append(sec_head("charts", "VISUALIZATION", "数据可视化",
                          f"本次按需生成 {part_count} 张图表"))

    for key, title, badge, desc in shown_groups:
        items = grouped.get(key, [])
        parts.append(f'<div class="group-head"><b>{esc(title)}</b> '
                     f'<span class="badge">{len(items)}</span>'
                     f'<div class="note">{esc(desc)}</div></div>')
        one = " one" if len(items) == 1 else ""
        parts.append(f'<div class="chart-grid{one}">')
        for png in items:
            encoded = base64.b64encode(png.read_bytes()).decode("ascii")
            cap = friendly(png)
            parts.append(
                f'<div class="chart-card"><div class="ct">{esc(cap)}</div>'
                f'<figure><img src="data:image/png;base64,{encoded}" alt="{esc(cap)}">'
                f'<figcaption>{esc(png.stem)}</figcaption></figure></div>'
            )
        parts.append('</div>')

    parts.append('</section>')
    return "".join(parts)


def render_limitations(idx: dict) -> str:
    lines = [
        "语义角色基于列名与取值启发式判断，已在「数据理解」中标注；如有修正请核对。",
        "完整相关矩阵与原始时间序列保留在 session 中，报告仅展示关键摘要与预览。",
        "统计与图表结果用于辅助理解数据，不替代严谨的统计推断与业务判断。",
    ]
    # 把被拦截/失败的工具如实列出
    problems = [(n, s, sm) for n, s, sm in idx["statuses"] if s in ("blocked", "error")]
    body = [sec_head("limits", "NOTES", "局限与说明"),
            '<div class="card"><ul>']
    body.extend(f'<li>{esc(t)}</li>' for t in lines)
    body.append('</ul>')
    if problems:
        body.append('<ul>')
        for n, s, sm in problems:
            body.append(f'<li><span class="pill {esc(s)}">{esc(s)}</span> '
                        f'{esc(n)}：{esc(sm)}</li>')
        body.append('</ul>')
    body.append('</div></section>')
    return "".join(body)


# 区块顺序与导航标签；sections 白名单按这些 key 过滤
ALL_SECTIONS = [
    ("verdict", "结论速览"), ("overview", "数据概览"), ("understand", "数据中的变量"),
    ("findings", "分析发现"), ("charts", "可视化"), ("limits", "局限与说明"),
]


def allowed_sections(idx: dict) -> set[str]:
    """__context__.sections 可显式限定渲染哪些区块；缺省为全部。"""
    ctx = idx.get("context") or {}
    raw = ctx.get("sections")
    if not raw:
        return {k for k, _ in ALL_SECTIONS}
    if isinstance(raw, str):
        raw = [s.strip() for s in raw.split(",") if s.strip()]
    aliases = {"understand": "understand", "understanding": "understand", "variables": "understand",
               "overview": "overview", "findings": "findings", "charts": "charts",
               "verdict": "verdict", "conclusion": "verdict", "limits": "limits"}
    return {aliases.get(str(s).lower(), str(s).lower()) for s in raw}


def render_report(envelopes: list[dict], charts_dir: Path | None) -> tuple[str, str]:
    idx = index_envelopes(envelopes)
    allow = allowed_sections(idx)

    # 只计算被允许的区块；空结果的区块（如无统计的 findings / 无图的 charts）自动消失
    rendered: dict[str, str] = {}
    if "verdict" in allow:
        rendered["verdict"] = render_verdict(idx)
    if "overview" in allow:
        rendered["overview"] = render_overview(idx)
    if "understand" in allow:
        rendered["understand"] = render_understanding(idx)
    if "findings" in allow:
        rendered["findings"] = render_findings(idx)
    if "charts" in allow:
        rendered["charts"] = render_charts(idx, charts_dir)
    if "limits" in allow:
        rendered["limits"] = render_limitations(idx)

    body = "".join(rendered[k] for k, _ in ALL_SECTIONS if rendered.get(k))
    nav = "".join(f'<a href="#{key}">{esc(label)}</a>'
                  for key, label in ALL_SECTIONS if rendered.get(key))
    return body, nav


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: build_report.py <collected.json> <output.html> [charts_dir]", file=sys.stderr)
        return 2
    collected_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    charts_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    envelopes = json.loads(collected_path.read_text(encoding="utf-8"))
    if isinstance(envelopes, dict):
        envelopes = [envelopes]
    body, nav = render_report(envelopes, charts_dir)
    template = TEMPLATE.read_text(encoding="utf-8")
    title = "CSV 数据分析报告"
    for env in envelopes:
        if env.get("tool") == "read_dataset" and env.get("data", {}).get("source_file"):
            title = Path(env["data"]["source_file"]).stem + " · 分析报告"
            break
    rendered = (template.replace("__TITLE__", esc(title))
                        .replace("__TIME__", esc(datetime.now().strftime("%Y-%m-%d %H:%M")))
                        .replace("__NAV__", nav)
                        .replace("__BODY__", body))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"WROTE {output_path} ({len(rendered)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
