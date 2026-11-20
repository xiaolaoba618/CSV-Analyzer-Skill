---
name: csv-analyzer-report
description: Analyze a CSV dataset and generate a polished HTML report in the CSV Analyzer visual style. This skill should be used when a user provides a CSV file (or asks to analyze tabular data) and wants statistical findings, data-quality assessment, time-series or correlation analysis, and/or charts. The agent infers each column's real business meaning, asks the user when a meaning or the goal is genuinely ambiguous or decision-critical, then runs only the analyses and generates only the charts and report sections that actually serve the user's question. Never treat birth dates as time-series axes.
agent_created: true
---

# CSV Analyzer Report

## Overview

Turn a CSV into a **decision-ready HTML report in the CSV Analyzer style** — not a tool log and
not a dumping ground for every statistic. The agent infers what each column *means*, resolves
genuine ambiguity by **asking the user**, then runs the **minimum** analyses, charts and report
sections that answer the actual question. Every tool call goes through a local adapter returning
compact JSON envelopes.

Three rules override everything else:
1. **Understand semantics before computing.** A column's name, sample values and unit decide
   what analysis is valid. A "date" column is not automatically a time axis.
2. **Ask when it matters.** If a column's meaning is unclear, or the choice would materially
   change the answer (e.g. event_time vs attribute_date, which metric is "sales"), ask the user
   instead of guessing. Do not pepper them with questions — only the decisive ones.
3. **Output is on-demand.** Serve the narrowest interpretation of the request. Only run the
   needed tools, draw only informative charts, and render only the report sections that carry
   the answer. A short report that answers the question beats a long one that buries it.

## When to use

- A user provides a `.csv` path or asks to analyze a tabular dataset.
- They want descriptive statistics, quality checks, time trends, variable relationships,
  charts, or an overall report.

## Environment

- **No hard-coded Python path.** Every command goes through `scripts/run.py`, a lightweight
  bootstrap that finds a Python with numpy/pandas/scipy/matplotlib and uses it to run the real
  scripts. Launch it with any available `python` (or `python3`) — the bootstrap itself needs
  only the standard library.
- `run.py` resolution order: env var `CSV_ANALYZER_PYTHON` → cached choice (`.python_env.json`)
  → auto-scan (current python, PATH, conda envs, `py` launcher, common install paths) →
  auto-create `.venv` and `pip install -r requirements.txt`. The chosen interpreter is cached
  so later calls skip scanning. To force re-detection, delete `.python_env.json`.
- Wrapper: `scripts/call_tool.py` (tool CLI); `scripts/build_report.py` (HTML report).
- Fully self-contained: `tools/`, `agent/`, `tool_registry.py`, `analysis_result.py` live
  inside this folder. Drop it anywhere; no external CSV-Analyzer installation needed.
- **Read `references/decision_guide.md` before choosing tools and charts.**

**Command pattern** (replace `<skill>` with this skill's directory path):
```bash
python <skill>/scripts/run.py scripts/call_tool.py <command> [args...]
python <skill>/scripts/run.py scripts/build_report.py <collected.json> <output.html> [charts_dir]
```
Here `python` can be any working interpreter — `run.py` will pick the right one for the
actual analysis. If the agent's shell has no `python` but has `python3`, use `python3`.

## Workflow

### Step 1 — Read the dataset
```bash
python <skill>/scripts/run.py scripts/call_tool.py read "<csv_path>"
```
Extract `session_id`, `columns`, `dtypes`, `first_5_rows`. Keep `session_id` for all later calls.

### Step 2 — Profile, then INFER each column's real meaning
```bash
... run.py scripts/call_tool.py call <sid> profile
```
`profile` gives `suggested_semantic_roles` — a heuristic, not the truth. For **every** column,
combine its name + sample values + dtype + unit cues and write down a plain-language meaning
and a confidence:

| Column | Inferred meaning | Role | Confidence |
|---|---|---|---|
| `date` | 每日观测日期 | `event_time` | high |
| `birth_date` | 员工出生日期 | `attribute_date` | high |
| `amt` | 订单金额? 需确认单位 | `measure` | low → ask |

Role rules:
- `event_time`: real observation/recording timestamps → time-series is allowed.
- `attribute_date`: birth / hire / expiry / graduation dates → **never** a time axis.
- `measure`: a metric; `dimension`: a category; `identifier`: an ID (never enter statistics).
- Watch for coded names (`amt`, `val`, `c1`), units (万元 vs 元), and dates whose role is unclear.

### Step 2b — Ask the user ONLY when it is genuinely ambiguous or decision-critical
Use `AskUserQuestion` **before** running analysis when any of these hold:
- A column's meaning/unit can't be inferred confidently and it affects the result (e.g. is
  `amt` revenue or cost? is it 元 or 万元?).
- A date column's role is undecidable (event time vs attribute date) and the answer changes
  whether time-series is valid.
- The user's goal is unclear between two very different analyses (e.g. "看看这个表" could mean
  quality audit or business insight — ask which they want).
- A metric named in the request maps to multiple candidate columns.
- A choice would materially change the conclusion and a reasonable person would disagree.

Do **not** ask about: cosmetic preferences, columns irrelevant to the question, or things you can
settle from the data itself. Max a few focused questions, each with concrete options. If the user
doesn't answer, proceed with the most defensible assumption and state it in the report's 局限说明.

### Step 3 — Correct types (only if needed)
```bash
... run.py scripts/call_tool.py call <sid> override_types 'roles={"birth_date":"attribute_date"}'
... run.py scripts/call_tool.py call <sid> profile        # re-profile so downstream tools see new types
```
Only override with a concrete reason (often prompted by Step 2b). Record what and why — the
report shows it.

### Step 4 — Choose analyses from the question + inferred semantics
Map the user's question to the **fewest** tools that can answer it (full matrix in
`references/decision_guide.md`):

| User asked for | Analysis tools | Charts (`plot_types`) |
|---|---|---|
| "画个图 / just charts" | none beyond profile | only the chart(s) implied by the data/question |
| "看趋势 / 股票 / 销售随时间" | `time_analysis`, `time_series` | `["time_series"]` (+ `time_daily` if useful) |
| "看两个变量关系 / 相关性" | `statistics`, `correlation` | `["scatter"]` and/or `["heatmap"]` |
| "各类别占比 / 构成" | `statistics` | `["bar"]` |
| "数据质量 / 有没有缺失" | `quality`, `missingness` | usually **none** |
| "全面分析 / 分析一下" | `statistics`, `quality`, `missingness`, + time/corr if applicable | only the most informative few |
| specific column mentioned | tailor to that column | only charts of that column |

Call each chosen tool:
```bash
... run.py scripts/call_tool.py call <sid> <tool_name> [key=value ...]
```
Handle envelope status: `ok` → use it; `blocked` → call `data.missing_prerequisites` then
retry (never treat blocked as a finding); `error` → note in limitations, don't fabricate.

**Analysis red lines:**
- If the user didn't ask for it, don't run it. No reflex quality audits for a trend question.
- `attribute_date` → never `time_series` / time-distribution charts.
- Correlation only with ≥2 numeric variables and correlation actually computed.
- Never analyze an `identifier` column as a metric.

### Step 5 — Generate ONLY the chosen charts
```bash
... run.py scripts/call_tool.py call <sid> visualization 'plot_types=["scatter","heatmap"]'
```
`plot_types`: `histogram`, `boxplot`, `bar`, `scatter`, `heatmap`, `time_hourly`,
`time_weekday`, `time_daily`, `time_series`. Always pass an explicit list — omitting it means
"all" and produces irrelevant charts. If no chart is needed for the question, skip this step.

Chart red lines: text answers the question → **0 charts**; a constant/identifier column → skip;
prefer 1–4 well-chosen charts over a wall.

### Step 6 — Build the report with an explicit conclusion and section choice
Prepend a `__context__` envelope to the collected JSON. Write a plain-language conclusion for
the actual question, and — importantly — **choose which report sections to include**. Only list
sections that carry the answer; a narrow question deserves a narrow report:
```json
{"tool":"__context__","status":"ok","data":{
  "user_question":"这几个月的销售走势怎么样？",
  "plain_conclusion":"整体生意在稳步向好：销售额、利润和评分都在持续上升。",
  "points":["销售额与利润同步增长。","评分一路上扬，表现稳定。"],
  "sections":["verdict","charts"]
}}
```
- `plain_conclusion` / `user_question` / `points`: the 结论速览 block at the top. Every number
  must trace to a tool result; no jargon, no tool names.
- `sections`: allowed keys `verdict`, `overview`, `understand`, `findings`, `charts`, `limits`.
  Omit to include all (with empty ones auto-hidden). Examples: a "just draw" request →
  `["verdict","charts"]`; a quality audit → `["verdict","understand","findings","limits"]`.
- If you omit `__context__`, the script auto-generates a conclusion and includes all
  non-empty sections — acceptable, but a question-specific conclusion and a trimmed section
  set are always better.

### Step 7 — Build the HTML report
```bash
python <skill>/scripts/run.py scripts/build_report.py <collected.json> "<output>/report.html" "<output>/charts"
```
The script produces a self-contained HTML report in the CSV Analyzer light/indigo style: the
结论速览 block, only the chosen sections, and base64-embedded charts. No external assets.

### Step 8 — Present
Call `present_files` on `report.html`. In chat, briefly answer the user's actual question,
state any semantic assumption or correction, and say which analyses/charts you chose and why.
Keep the chat answer short — the report holds the detail.

## Rules

- Never invent statistics — every number comes from a tool result.
- Infer semantics first; ask the user on genuine, decision-critical ambiguity; proceed with a
  stated assumption only when they don't answer.
- Serve the narrowest interpretation of the request; expand only when asked.
- Keep tools, charts and report sections minimal. "Full analysis" is opt-in, not default.
- Always mention semantic overrides and why. When a date is ambiguous, prefer
  `attribute_date` and explain.
- Report style must match the CSV Analyzer app (light background, white cards, indigo #4f46e5).
- This skill is self-contained; never depend on an external CSV-Analyzer project.

## Resources

- `scripts/call_tool.py` — adapter CLI: `manifest`, `read`, `call`, `describe`.
- `scripts/build_report.py` — renders collected envelopes into the styled HTML report.
- `references/decision_guide.md` — semantic inference, when-to-ask, and tool/chart selection rules.
- `assets/report_template.html` — CSV Analyzer style report template.
