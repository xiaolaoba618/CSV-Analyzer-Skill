# CSV Analyzer Skill

把一份 CSV 变成**风格统一、按需裁剪的 HTML 数据分析报告**。这是 CSV Analyzer 的 Agent Skill 版本：自包含、无需安装本体，拖入 Agent 的 skill 目录即可用。

它会先推断每个字段的真实业务含义，必要时向用户提问，然后只做能回答用户问题的分析、只画有意义的图、只渲染承载答案的报告章节——而不是把所有统计和图表一股脑塞进报告。

## 特性

- **语义优先**：结合列名、取值、单位、业务语境推断字段含义与置信度；`attribute_date`（出生/入职日期）不会被误当时间轴。
- **该问才问**：字段含义或用户目标存在关键歧义时才提问，其余自行判断。
- **按需输出**：分析、图表、报告章节都由用户问题驱动，最小化、不堆砌。
- **报告风格统一**：输出与 CSV Analyzer app 一致的浅色 / 靛蓝报告，自包含（图表 base64 内嵌），可独立分享。
- **自包含**：分析工具、Agent 适配层全在本目录内，不依赖外部项目。

## 目录结构

```
csv-analyzer-report/
├── SKILL.md                  # Agent 使用说明（工作流 + 决策规则）
├── README.md                 # 本文件
├── requirements.txt          # 运行依赖
├── scripts/
│   ├── run.py                # 自适应入口：自动找/建带依赖的 Python 再执行脚本
│   ├── call_tool.py          # 工具调用 CLI（manifest / read / call / describe）
│   └── build_report.py       # 把工具结果渲染为 HTML 报告
├── agent/                    # Agent 适配层（上下文、manifest、投影、运行时）
├── tools/                    # 分析工具实现（统计、相关性、时间序列、可视化等）
├── references/
│   └── decision_guide.md     # 工具与图表选择、语义推断、提问规则
└── assets/
    └── report_template.html  # 报告模板（CSV Analyzer 风格）
```

## 环境依赖（自动适配，无需手动配置）

所有命令经 `scripts/run.py` 引导，它会**自动寻找一个装齐依赖的 Python**：

1. 环境变量 `CSV_ANALYZER_PYTHON` 指定的解释器（可强制指定）；
2. 上次探测并缓存的选择（`.python_env.json`）；
3. 自动扫描：当前 python → PATH → conda 环境 → `py` 启动器 → 常见安装路径；
4. 若都没有，自动在 skill 目录创建 `.venv` 并按 `requirements.txt` 安装依赖（首次稍候，之后走缓存）。

依赖（`requirements.txt`）：`numpy>=2.0` `pandas>=2.2` `scipy>=1.14` `matplotlib>=3.8`。

- 启动 `run.py` 的 `python` 可以是任意能运行的解释器（它本身只用标准库）。
- 想换解释器或重新探测：设置 `CSV_ANALYZER_PYTHON`，或删除 `.python_env.json`。
- 不想要自动生成的 `.venv` / 缓存？它们是运行产物，可随时删除，不影响 skill。

## 安装

把本目录（或 `csv-analyzer-report.zip` 解压后的 `csv-analyzer-report/`）整个放进 Agent 的 skill 目录，例如：

```
~/.workbuddy/skills/csv-analyzer-report/
```

确保 `SKILL.md` 位于该目录根层即可。拖进去后即可使用——首次调用会自动探测/准备 Python 环境，无需手动安装依赖。

## 使用流程

Agent 的完整规则见 `SKILL.md`。所有命令统一用 `python <skill>/scripts/run.py scripts/<脚本> ...` 的形式（`<skill>` 指本目录路径）；开头的 `python` 任意可用即可，run.py 会挑正确的解释器执行。手动调用示例：

**1. 查看可用工具**

```bash
python <skill>/scripts/run.py scripts/call_tool.py manifest
```

**2. 读取数据**（记下返回的 `session_id`，后续都要用）

```bash
python <skill>/scripts/run.py scripts/call_tool.py read "/path/to/data.csv"
```

**3. 画像 + 判断语义**

```bash
python <skill>/scripts/run.py scripts/call_tool.py call <session_id> profile
```

**4. 按需调用分析工具**（示例：时间序列、相关性）

```bash
python <skill>/scripts/run.py scripts/call_tool.py call <session_id> time_series 'datetime_column="date"'
python <skill>/scripts/run.py scripts/call_tool.py call <session_id> correlation
```

**5. 只生成需要的图**

```bash
python <skill>/scripts/run.py scripts/call_tool.py call <session_id> visualization 'plot_types=["time_series"]'
```

`plot_types` 可选：`histogram` `boxplot` `bar` `scatter` `heatmap` `time_hourly` `time_weekday` `time_daily` `time_series`。**务必显式传入**，省略则会生成全部图。

**6. 生成报告**

把上述调用的信封 JSON 收集成一个列表（可首置一个 `__context__` 对象，见下），写入 `collected.json`：

```bash
python <skill>/scripts/run.py scripts/build_report.py collected.json report.html [charts_dir]
```

产出的 `report.html` 自包含、无外部依赖，直接用浏览器打开。

## 可用工具

`read_dataset`、`profile`、`override_types`、`preprocess`、`statistics`、`quality`、`missingness`、`time_analysis`、`time_series`、`correlation`、`visualization`、`get_manifest`。

每个工具返回统一信封：`tool` / `status` / `session_id` / `summary` / `data` / `warnings` / `next_suggested_tools`。`status=blocked` 表示缺前置工具（看 `data.missing_prerequisites` 补齐后重试），**不是**分析结论。

## 用 `__context__` 定制报告

在 `collected.json` 列表最前面加一个 `__context__` 对象，可以写面向普通人的结论、指定报告只显示哪些章节：

```json
{
  "tool": "__context__",
  "status": "ok",
  "data": {
    "user_question": "这几个月的销售走势怎么样？",
    "plain_conclusion": "整体生意在稳步向好：销售额、利润和评分都在持续上升。",
    "points": ["销售额与利润同步增长。", "评分一路上扬，表现稳定。"],
    "sections": ["verdict", "charts"]
  }
}
```

- `plain_conclusion` / `user_question` / `points`：报告开头的「结论速览」，用大白话回答用户问题，数字须来自工具结果。
- `sections`：只渲染指定章节，可选 `verdict` `overview` `understand` `findings` `charts` `limits`。省略则渲染所有非空章节（无统计的 findings、无图的 charts 会自动隐藏）。
  - 只要图 → `["verdict", "charts"]`
  - 变量关系 → `["verdict", "findings", "charts"]`
  - 数据质量 → `["verdict", "understand", "findings", "limits"]`

## 核心红线

1. 一切以满足用户当前问题为准，分析 / 图表 / 章节都最小化。
2. `attribute_date`（出生、入职、到期等）禁止做时间序列。
3. 标识符列不进统计、不画图；没跑相关分析不要画热力图。
4. 不虚构数字——报告里每个数值都来自工具结果。
5. 用户没要图时，0 张图是正确答案。

详细决策规则见 `references/decision_guide.md`，Agent 工作流见 `SKILL.md`。
