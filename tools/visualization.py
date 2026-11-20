import atexit
import shutil
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

MAX_CATEGORIES = 15
MAX_HEATMAP_COLUMNS = 30
MAX_SCATTER_VARIABLES = 10

# ---- 统一视觉系统：沉稳主色 + 暖色强调 + 浅色层次 ----
_PRIMARY = "#4F6BED"       # 主蓝紫
_PRIMARY_LIGHT = "#C3D2FA"
_ACCENT = "#F2855B"        # 暖橙，用于强调线 / 峰值
_ACCENT_2 = "#17A2B8"      # 青绿，用于次要比对线
_TEXT = "#1E293B"
_TEXT_MUTED = "#64748B"
_AXES_BG = "#F8FAFC"
_GRID = "#E2E8F0"


def _cmap(light, dark):
    """生成两段式渐变色表，用于柱 / 直方图着色。"""
    return mcolors.LinearSegmentedColormap.from_list("grad", [light, dark])


def _stat_box(ax, lines, loc="upper right"):
    """在图内放置半透明统计信息框。"""
    right = "right" in loc
    ax.text(
        0.97 if right else 0.03,
        0.95,
        lines,
        transform=ax.transAxes,
        ha="right" if right else "left",
        va="top",
        fontsize=9,
        color=_TEXT,
        linespacing=1.5,
        bbox=dict(
            boxstyle="round,pad=0.55",
            facecolor="white",
            edgecolor=_GRID,
            linewidth=0.8,
            alpha=0.95,
        ),
    )


def configure_chinese_font():
    font_candidates = [
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS"
    ]
    available_fonts = {font.name for font in plt.matplotlib.font_manager.fontManager.ttflist}
    selected_font = None
    for font_name in font_candidates:
        if font_name in available_fonts:
            selected_font = font_name
            break
    params = {
        "axes.unicode_minus": False,
        "figure.dpi": 120,
        "savefig.dpi": 180,
        "figure.facecolor": "white",
        "axes.facecolor": _AXES_BG,
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
        "axes.titlecolor": _TEXT,
        "axes.labelsize": 10,
        "axes.labelcolor": _TEXT,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "xtick.color": _TEXT_MUTED,
        "ytick.color": _TEXT_MUTED,
        "axes.edgecolor": _GRID,
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "text.color": _TEXT,
        "legend.frameon": True,
        "legend.fontsize": 9,
        "legend.facecolor": "white",
        "legend.edgecolor": _GRID,
        "legend.framealpha": 0.9,
    }
    if selected_font is not None:
        params["font.family"] = selected_font
    plt.rcParams.update(params)
    return selected_font


# 图表生成后只会被报告模块（report/generator.py、report/dashboard_generator.py）
# 读取一次，然后转成 base64 内嵌到 HTML 中。因此图表本身不需要长期驻留在本地。
# 这里统一把图表写入系统临时目录，并在进程退出时自动删除，
# 这样既不影响报告生成，也不会在项目目录里残留 PNG 文件。
#
# 注意：静态报告（report.html）的统计图一律基于**完整数据集**绘制，不再抽样，
# 与「导出全部数据」选项无关——该选项只影响交互式报告的载荷大小。
_FIGURE_DIRECTORY = None

def _remove_figure_directory(directory):
    """删除图表临时目录；失败时静默忽略，不影响主流程。"""
    shutil.rmtree(directory, ignore_errors=True)

def _get_figure_directory():
    """返回本进程独占的图表临时目录，首次调用时创建并注册退出清理。"""
    global _FIGURE_DIRECTORY
    if _FIGURE_DIRECTORY is None:
        _FIGURE_DIRECTORY = tempfile.mkdtemp(prefix="csv_analyzer_figures_")
        atexit.register(_remove_figure_directory, _FIGURE_DIRECTORY)
    return Path(_FIGURE_DIRECTORY)

def _ensure_output_dir(output_dir=None):
    """返回图表输出目录。

    保留原有函数名与参数以便兼容既有调用，但不再使用传入的 output_dir：
    图表统一写入临时目录，避免在本地留下 PNG 文件。
    """
    return _get_figure_directory()

def _safe_filename(name):
    invalid_characters = '<>:"/\\|?*'
    result = str(name)
    for character in invalid_characters:
        result = result.replace(character, "_")
    result = result.strip()
    if not result:
        result = "unnamed"
    return result

def _get_columns_by_type(df, variable_types, allowed_types):
    if variable_types is None:
        return []
    return [column for column, info in variable_types.items() if (column in df.columns and info["detected_type"] in allowed_types)]

def _get_numeric_columns(df, variable_types=None):
    if variable_types is None:
        return df.select_dtypes(include=np.number).columns.tolist()
    return _get_columns_by_type(df, variable_types, {"numeric"})

def _get_categorical_columns(df, variable_types=None):
    if variable_types is None:
        return df.select_dtypes(include=["object", "category", "bool"]).columns.tolist()
    return _get_columns_by_type(df, variable_types, {"categorical", "categorical_numeric", "binary", "boolean"})

def _add_axis_grid(ax, axis="y"):
    ax.set_facecolor(_AXES_BG)
    ax.grid(axis=axis, color=_GRID, linestyle="--", linewidth=0.8, alpha=1.0)
    ax.set_axisbelow(True)

def _add_value_labels(ax, values, decimals=0):
    for index, value in enumerate(values):
        if pd.isna(value):
            continue
        if decimals == 0:
            label = f"{value:,.0f}"
        else:
            label = f"{value:,.{decimals}f}"
        ax.text(index, value, label, ha="center", va="bottom", fontsize=9,
                color=_TEXT_MUTED, fontweight="bold")

def _add_horizontal_value_labels(ax, values):
    for index, value in enumerate(values):
        if pd.isna(value):
            continue
        ax.text(
            value,
            index,
            f"{value:,.0f}",
            va="center",
            ha="left",
            fontsize=9,
            color=_TEXT_MUTED,
            fontweight="bold",
        )

def create_histogram(df, column, output_dir, bins=30):
    output_dir = _ensure_output_dir(output_dir)
    series = (df[column].replace([np.inf, -np.inf], np.nan).dropna())
    if len(series) == 0:
        return None
    if series.nunique() <= 1:
        return None
    mean_value = series.mean()
    median_value = series.median()
    fig, ax = plt.subplots(figsize=(9, 5.5))
    cmap = _cmap(_PRIMARY_LIGHT, _PRIMARY)
    counts, edges, patches = ax.hist(series, bins=bins, edgecolor="white", linewidth=0.8)
    if patches:
        for index, patch in enumerate(patches):
            patch.set_facecolor(cmap(index / max(len(patches) - 1, 1)))
    ax.axvline(
        mean_value,
        color=_ACCENT,
        linestyle="--",
        linewidth=2.2,
        label=f"均值 {mean_value:,.2f}",
    )
    ax.axvline(
        median_value,
        color=_ACCENT_2,
        linestyle=":",
        linewidth=2.2,
        label=f"中位数 {median_value:,.2f}",
    )
    ax.set_xlabel(column)
    ax.set_ylabel("频数")
    ax.set_title(f"{column} 分布", pad=12)
    _add_axis_grid(ax)
    stats = (f"样本数  {len(series):,}\n"
             f"标准差  {series.std():,.2f}\n"
             f"范围  {series.min():,.2f} ~ {series.max():,.2f}")
    _stat_box(ax, stats, loc="upper right")
    ax.legend(loc="upper left")
    fig.tight_layout()
    output_path = (
        output_dir
        / f"{_safe_filename(column)}_hist.png"
    )
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return str(output_path)

def create_boxplot(df, column, output_dir):
    output_dir = _ensure_output_dir(output_dir)
    series = (df[column].replace([np.inf, -np.inf], np.nan).dropna())
    if len(series) == 0:
        return None
    if series.nunique() <= 1:
        return None
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    boxplot = ax.boxplot(
        series,
        patch_artist=True,
        showmeans=True,
        meanline=False,
        flierprops={
            "marker": "o", "markersize": 5, "alpha": 0.65,
            "markerfacecolor": _ACCENT, "markeredgecolor": "none",
        },
        medianprops={"linewidth": 2.5, "color": _ACCENT},
        meanprops={
            "marker": "D", "markersize": 7,
            "markerfacecolor": _PRIMARY, "markeredgecolor": "white",
            "markeredgewidth": 1.2,
        },
    )
    for patch in boxplot["boxes"]:
        patch.set_facecolor(_PRIMARY_LIGHT)
        patch.set_edgecolor(_PRIMARY)
        patch.set_linewidth(1.8)
    for whisker in boxplot["whiskers"]:
        whisker.set_color(_PRIMARY)
        whisker.set_linewidth(1.5)
    for cap in boxplot["caps"]:
        cap.set_color(_PRIMARY)
        cap.set_linewidth(1.5)
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    ax.set_ylabel(column)
    ax.set_title(f"{column} 箱线图", pad=12)
    ax.set_xticks([1])
    ax.set_xticklabels([column])
    stats = (f"Q1  {q1:,.2f}\n"
             f"中位数  {series.median():,.2f}\n"
             f"Q3  {q3:,.2f}\n"
             f"IQR  {q3 - q1:,.2f}")
    _stat_box(ax, stats, loc="upper right")
    _add_axis_grid(ax)
    fig.tight_layout()
    output_path = (
        output_dir
        / f"{_safe_filename(column)}_box.png"
    )
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return str(output_path)

def create_categorical_bar(df, column, output_dir, max_categories=MAX_CATEGORIES):
    output_dir = _ensure_output_dir(output_dir)
    series = df[column].dropna()
    if len(series) == 0:
        return None
    value_counts = (series.astype(str).value_counts().head(max_categories))
    if len(value_counts) == 0:
        return None
    value_counts = (value_counts.sort_values())
    fig_height = max(5, len(value_counts) * 0.42)
    fig, ax = plt.subplots(figsize=(9, fig_height))
    cmap = _cmap(_PRIMARY_LIGHT, _PRIMARY)
    colors = [cmap(index / max(len(value_counts) - 1, 1)) for index in range(len(value_counts))]
    bars = ax.barh(
        range(len(value_counts)), value_counts.values,
        color=colors, edgecolor="none", height=0.68,
    )
    ax.set_yticks(range(len(value_counts)))
    ax.set_yticklabels(value_counts.index)
    ax.set_xlabel("数量")
    ax.set_ylabel(column)
    ax.set_title(f"{column} 类别分布", pad=12)
    _add_axis_grid(ax, axis="x")
    total = int(value_counts.values.sum())
    for bar, value in zip(bars, value_counts.values):
        ratio = value / total * 100 if total else 0
        ax.text(
            bar.get_width(),
            bar.get_y() + bar.get_height() / 2,
            f"{value:,}  ({ratio:.1f}%)",
            va="center",
            ha="left",
            fontsize=9,
            color=_TEXT_MUTED,
            fontweight="bold",
        )
    ax.margins(x=0.16)
    fig.tight_layout()
    output_path = (
        output_dir
        / f"{_safe_filename(column)}_bar.png"
    )
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return str(output_path)

def create_scatter(df, column_x, column_y, output_dir):
    output_dir = _ensure_output_dir(output_dir)
    pair = df[[column_x, column_y]].copy()
    pair = pair.replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 2:
        return None
    if (pair[column_x].nunique() <= 1 or pair[column_y].nunique() <= 1):
        return None
    fig, ax = plt.subplots(figsize=(8.5, 6))
    ax.scatter(
        pair[column_x], pair[column_y],
        alpha=0.55, s=42,
        color=_PRIMARY, edgecolors="white", linewidths=0.6,
    )
    try:
        x = pair[column_x].to_numpy()
        y = pair[column_y].to_numpy()
        slope, intercept = np.polyfit(x, y, 1)
        x_line = np.linspace(x.min(), x.max(), 100)
        y_line = (slope * x_line + intercept)
        ax.plot(x_line, y_line, color=_ACCENT, linestyle="--", linewidth=2.2, label="线性趋势")
        correlation = float(np.corrcoef(x, y)[0, 1])
        _stat_box(ax, f"Pearson r  {correlation:+.3f}\n样本数  {len(pair):,}", loc="upper left")
        ax.legend(loc="lower right")
    except Exception:
        pass
    ax.set_xlabel(column_x)
    ax.set_ylabel(column_y)
    ax.set_title(f"{column_x} 与 {column_y} 的关系", pad=12)
    _add_axis_grid(ax)
    ax.grid(axis="x", color=_GRID, linestyle="--", linewidth=0.8)
    fig.tight_layout()
    filename = (
        f"{_safe_filename(column_x)}"
        f"_vs_"
        f"{_safe_filename(column_y)}"
        f"_scatter.png"
    )
    output_path = (output_dir / filename)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return str(output_path)

def create_correlation_heatmap(correlation_result, output_dir, method="spearman", max_columns=MAX_HEATMAP_COLUMNS):
    output_dir = _ensure_output_dir(output_dir)
    if not correlation_result:
        return None
    if method not in correlation_result:
        return None
    method_result = correlation_result[method]
    if not method_result:
        return None
    correlation_matrix = method_result.get("correlation")
    if correlation_matrix is None:
        return None
    if correlation_matrix.empty:
        return None
    if len(correlation_matrix.columns) == 0:
        return None
    columns = correlation_matrix.columns
    if len(columns) > max_columns:
        columns = columns[:max_columns]
        correlation_matrix = (correlation_matrix.loc[columns, columns])
    n_columns = len(columns)
    figure_size = max(8, n_columns * 0.62)
    fig, ax = plt.subplots(figsize=(figure_size, figure_size))
    image = ax.imshow(correlation_matrix, aspect="auto", cmap="RdBu_r", vmin=-1, vmax=1)
    colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    colorbar.set_label("Correlation", color=_TEXT)
    colorbar.ax.tick_params(colors=_TEXT_MUTED)
    ax.set_xticks(range(n_columns))
    ax.set_xticklabels(columns, rotation=45, ha="right", color=_TEXT_MUTED)
    ax.set_yticks(range(n_columns))
    ax.set_yticklabels(columns, color=_TEXT_MUTED)
    for i in range(n_columns):
        for j in range(n_columns):
            value = correlation_matrix.iloc[i, j]
            if pd.isna(value):
                continue
            text_color = ("white" if abs(value) >= 0.55 else _TEXT)
            ax.text(
                j,
                i,
                f"{value:.2f}",
                ha="center",
                va="center",
                fontsize=8,
                fontweight="bold",
                color=text_color
            )
    for spine in ax.spines.values():
        spine.set_color(_GRID)
        spine.set_linewidth(0.8)
    ax.set_title(f"{method.title()} 相关系数热力图", pad=14)
    fig.tight_layout()
    output_path = (
        output_dir
        / f"{method}_correlation_heatmap.png"
    )
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return str(output_path)

def plot_hourly_distribution(time_analysis, output_dir=None):
    output_dir = _ensure_output_dir()
    paths = []
    analyses = time_analysis.get("analyses", {})
    for column, analysis in analyses.items():
        hourly = analysis.get("hourly_distribution", {})
        if hourly.get("status") != "ok":
            continue
        results = hourly.get("results", [])
        if not results:
            continue
        hours = [item["hour"] for item in results]
        counts = [item["count"] for item in results]
        fig, ax = plt.subplots(figsize=(10, 5))
        cmap = _cmap(_PRIMARY_LIGHT, _PRIMARY)
        colors = [cmap(index / max(len(counts) - 1, 1)) for index in range(len(counts))]
        bars = ax.bar(hours, counts, color=colors, edgecolor="none", width=0.72)
        peak = int(np.argmax(counts))
        bars[peak].set_color(_ACCENT)
        ax.set_xticks(range(24))
        ax.set_xlabel("小时")
        ax.set_ylabel("观测数量")
        ax.set_title(f"{column} 小时分布", pad=12)
        _add_axis_grid(ax)
        _stat_box(ax, f"峰值时段  {hours[peak]:02d}:00\n观测数  {counts[peak]:,}", loc="upper right")
        fig.tight_layout()
        output_path = (
            output_dir
            / f"{_safe_filename(column)}_hourly.png"
        )
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)
        paths.append(str(output_path))
    return paths

def plot_weekday_distribution(time_analysis, output_dir=None):
    output_dir = _ensure_output_dir()
    paths = []
    analyses = time_analysis.get("analyses", {})
    for column, analysis in analyses.items():
        weekday = analysis.get("weekday_distribution", {})
        if weekday.get("status") != "ok":
            continue
        results = weekday.get("results", [])
        if not results:
            continue
        labels = [item["weekday"] for item in results]
        counts = [item["count"] for item in results]
        fig, ax = plt.subplots(figsize=(9, 5))
        cmap = _cmap(_PRIMARY_LIGHT, _PRIMARY)
        colors = [cmap(index / max(len(counts) - 1, 1)) for index in range(len(counts))]
        ax.bar(labels, counts, color=colors, edgecolor="none", width=0.68)
        ax.set_xlabel("星期")
        ax.set_ylabel("观测数量")
        ax.set_title(f"{column} 星期分布", pad=12)
        ax.tick_params(axis="x", rotation=20, colors=_TEXT_MUTED)
        _add_axis_grid(ax)
        _add_value_labels(ax, counts)
        fig.tight_layout()
        output_path = (
            output_dir
            / f"{_safe_filename(column)}_weekday.png"
        )
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)
        paths.append(str(output_path))
    return paths

def plot_daily_distribution(time_analysis, output_dir=None):
    output_dir = _ensure_output_dir()
    paths = []
    analyses = time_analysis.get("analyses", {})
    for column, analysis in analyses.items():
        daily = analysis.get("daily_distribution", {})
        if daily.get("status") != "ok":
            continue
        results = daily.get("results", [])
        if not results:
            continue
        dates = pd.to_datetime([item["date"] for item in results], errors="coerce")
        counts = [item["count"] for item in results]
        valid_mask = ~dates.isna()
        dates = dates[valid_mask]
        counts = np.array(counts)[valid_mask]
        if len(dates) == 0:
            continue
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(dates, counts, color=_PRIMARY, marker="o", markersize=3.5,
                linewidth=2.2, markerfacecolor="white", markeredgewidth=1.2)
        ax.fill_between(dates, counts, color=_PRIMARY, alpha=0.12)
        ax.set_xlabel("日期")
        ax.set_ylabel("观测数量")
        ax.set_title(f"{column} 每日观测趋势", pad=12)
        ax.tick_params(axis="x", rotation=30, colors=_TEXT_MUTED)
        _add_axis_grid(ax)
        fig.autofmt_xdate()
        fig.tight_layout()
        output_path = (
            output_dir
            / f"{_safe_filename(column)}_daily.png"
        )
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)
        paths.append(str(output_path))
    return paths

def plot_time_series(time_series_analysis, output_dir, max_variables=10):
    if not time_series_analysis:
        return []
    if (time_series_analysis.get("status") != "ok"):
        return []
    aggregation = (time_series_analysis.get("aggregation"))
    if not aggregation:
        return []
    if aggregation.get("status") != "ok":
        return []
    variables = aggregation.get("variables", {})
    if not variables:
        return []
    output_dir = _ensure_output_dir(output_dir)
    configure_chinese_font()
    frequency = aggregation.get("frequency", "unknown")
    paths = []
    valid_variables = [column for column, result in variables.items() if result.get("status") == "ok"]
    for column in valid_variables[:max_variables]:
        try:
            result = variables[column]
            records = result.get("results", [])
            if not records:
                continue
            data = pd.DataFrame(records)
            fig, ax = plt.subplots(figsize=(11, 5.5))
            if "time_index" in data.columns:
                data["time_index"] = pd.to_numeric(data["time_index"], errors="coerce")
                data = data.dropna(subset=["time_index"])
                value_key = "value" if frequency == "irregular" else "mean"
                if value_key not in data.columns or data.empty:
                    plt.close(fig)
                    continue
                data[value_key] = pd.to_numeric(data[value_key], errors="coerce")
                data = data.dropna(subset=[value_key])
                if data.empty:
                    plt.close(fig)
                    continue
                ax.plot(data["time_index"], data[value_key], color=_PRIMARY,
                        marker="o", markersize=3.2, linewidth=2.2,
                        markerfacecolor="white", markeredgewidth=1)
                ax.fill_between(data["time_index"], data[value_key], color=_PRIMARY, alpha=0.10)
                ax.set_title(f"{column} 时间序列", pad=12)
                ax.set_xlabel("时间序号")
                ax.set_ylabel(str(column))
                _add_axis_grid(ax)
                filename = _safe_filename(f"time_series_{column}.png")
                output_path = output_dir / filename
                fig.tight_layout()
                fig.savefig(output_path, bbox_inches="tight")
                plt.close(fig)
                paths.append(str(output_path))
                continue
            if "datetime" not in data.columns:
                plt.close(fig)
                continue
            data["datetime"] = (pd.to_datetime(data["datetime"], errors="coerce"))
            data = data.dropna(subset=["datetime"])
            if data.empty:
                continue
            if frequency == "irregular":
                if "value" not in data.columns:
                    plt.close(fig)
                    continue
                data["value"] = pd.to_numeric(data["value"], errors="coerce")
                data = data.dropna(subset=["value"])
                if data.empty:
                    plt.close(fig)
                    continue
                ax.plot(data["datetime"], data["value"], color=_PRIMARY,
                        marker="o", markersize=3.2, linewidth=2.0,
                        markerfacecolor="white", markeredgewidth=1, alpha=0.9)
            else:
                if "mean" not in data.columns:
                    plt.close(fig)
                    continue
                data["mean"] = pd.to_numeric(data["mean"], errors="coerce")
                data = data.dropna(subset=["mean"])
                if data.empty:
                    plt.close(fig)
                    continue
                ax.plot(data["datetime"], data["mean"], color=_PRIMARY, linewidth=2.4)
                ax.fill_between(data["datetime"], data["mean"], color=_PRIMARY, alpha=0.10)
                overall_mean = data["mean"].mean()
                ax.axhline(
                    overall_mean,
                    color=_ACCENT,
                    linestyle="--",
                    linewidth=1.8,
                    label=f"均值 {overall_mean:,.2f}",
                )
                ax.legend(loc="upper right")
            ax.set_title(f"{column} 时间序列", pad=12)
            ax.set_xlabel("时间")
            ax.set_ylabel(str(column))
            _add_axis_grid(ax)
            fig.autofmt_xdate()
            filename = _safe_filename(
                f"time_series_{column}.png"
            )
            output_path = (output_dir / filename)
            fig.tight_layout()
            fig.savefig(output_path, bbox_inches="tight")
            plt.close(fig)
            paths.append(str(output_path))
        except Exception:
            plt.close("all")
            continue
    return paths

# 可单独请求的图表类型；“all” 表示全量生成（向后兼容）。
_PLOT_TYPE_KEYS = (
    "histogram", "boxplot", "bar", "scatter", "heatmap",
    "time_hourly", "time_weekday", "time_daily", "time_series",
)


def _normalize_plot_types(plot_types):
    """把 plot_types 归一化成集合；None/'all' 表示全部。"""
    if plot_types is None:
        return None
    if isinstance(plot_types, str):
        if plot_types.strip().lower() in ("all", "*", ""):
            return None
        plot_types = [plot_types]
    selected = {str(p).strip().lower() for p in plot_types}
    # 接受常见别名，方便 agent 用更自然的名字
    aliases = {
        "hist": "histogram", "histograms": "histogram", "分布": "histogram",
        "box": "boxplot", "boxplots": "boxplot", "箱线": "boxplot",
        "categorical_bars": "bar", "bars": "bar", "bar_chart": "bar", "柱状": "bar",
        "scatter_plots": "scatter", "散点": "scatter",
        "correlation_heatmap": "heatmap", "heatmap": "heatmap", "热力": "heatmap",
        "hourly": "time_hourly", "weekday": "time_weekday", "daily": "time_daily",
        "ts": "time_series", "时间序列": "time_series", "趋势": "time_series",
    }
    normalized = set()
    for p in selected:
        normalized.add(aliases.get(p, p))
    if not normalized or normalized >= set(_PLOT_TYPE_KEYS):
        return None
    return normalized


def create_all_visualizations(
    df,
    output_dir,
    correlation_result=None,
    correlation_method="spearman",
    variable_types=None,
    time_analysis=None,
    time_series_analysis=None,
    plot_types=None
):
    """生成图表。plot_types 可指定只生成哪些类型（见 _PLOT_TYPE_KEYS），
    None / 'all' 保持全量生成的旧行为。"""
    configure_chinese_font()
    output_dir = _ensure_output_dir(output_dir)
    want = _normalize_plot_types(plot_types)  # None 表示全选

    def enabled(kind):
        return want is None or kind in want

    numeric_columns = (_get_numeric_columns(df, variable_types))
    categorical_columns = (_get_categorical_columns(df, variable_types))
    histograms = []
    boxplots = []
    categorical_bars = []
    scatter_plots = []
    heatmap = None
    hourly_paths = []
    weekday_paths = []
    daily_paths = []
    time_series_paths = []

    if enabled("histogram") or enabled("boxplot"):
        for column in numeric_columns:
            if enabled("histogram"):
                histogram = create_histogram(df, column, output_dir)
                if histogram is not None:
                    histograms.append(histogram)
            if enabled("boxplot"):
                boxplot = create_boxplot(df, column, output_dir)
                if boxplot is not None:
                    boxplots.append(boxplot)
    if enabled("bar"):
        for column in categorical_columns:
            chart = create_categorical_bar(df, column, output_dir)
            if chart is not None:
                categorical_bars.append(chart)
    if enabled("scatter"):
        scatter_columns = numeric_columns[:MAX_SCATTER_VARIABLES]
        for i in range(len(scatter_columns)):
            for j in range(i + 1, len(scatter_columns)):
                scatter = create_scatter(df, scatter_columns[i], scatter_columns[j], output_dir)
                if scatter is not None:
                    scatter_plots.append(scatter)
    if enabled("heatmap") and correlation_result is not None:
        heatmap = (create_correlation_heatmap(correlation_result, output_dir, method=correlation_method))
    if time_analysis:
        if enabled("time_hourly"):
            hourly_paths = (plot_hourly_distribution(time_analysis, output_dir))
        if enabled("time_weekday"):
            weekday_paths = (plot_weekday_distribution(time_analysis, output_dir))
        if enabled("time_daily"):
            daily_paths = (plot_daily_distribution(time_analysis, output_dir))
    if time_series_analysis and enabled("time_series"):
        time_series_paths = (plot_time_series(time_series_analysis, output_dir))
    return {
        "histograms": histograms,
        "boxplots": boxplots,
        "categorical_bars": categorical_bars,
        "scatter_plots": scatter_plots,
        "correlation_heatmap": heatmap,
        "numeric_columns": numeric_columns,
        "categorical_columns": categorical_columns,
        "time_hourly": hourly_paths,
        "time_weekday": weekday_paths,
        "time_daily": daily_paths,
        "time_series": time_series_paths
    }
