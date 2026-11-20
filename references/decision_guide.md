# 分析决策指南（语义 → 提问 → 按需输出）

核心原则：**一切以解决用户当前的问题为最高目标。** 先推断每列真实含义，必要时问用户，
再决定分析什么、画什么图、报告里放什么。不跑无关分析，不为凑数画全套图，不把属性日期当时间轴，
不把没用的内容堆进报告。

---

## 〇、第一优先级：用户到底问了什么

在调用任何工具前，先把用户的话归类。分析、图表、报告区块都从这个问题推导，而不是反过来。

| 用户意图 | 关键词示例 | 结论 |
|---|---|---|
| 只要图 | "画个图" "可视化一下" "just chart" | 只做 read + profile + 少数几张图；报告 `sections=["verdict","charts"]` |
| 只要结论 | "结果怎么样" "帮我看看" | 文字结论优先，图只在能证明结论时才画 |
| 时间趋势 | "趋势" "走势" "股票" "销售随时间" | 必须有 event_time，走时间序列 |
| 变量关系 | "关系" "相关" "影响" | 数值变量≥2，走相关分析 + 散点/热力 |
| 类别构成 | "占比" "分布" "哪些类别多" | 分类变量，走构成图 |
| 数据质量 | "质量" "缺失" "干净吗" | quality + missingness，通常不需要图 |
| 全面分析 | "全面分析" "分析一下这个表" | 这是 opt-in，才做较完整的一套 |

**红线**：用户没要图、或文字已能回答时，生成 0 张图也是正确答案；报告同理，能短则短。

---

## 一、先读数据，再逐列推断语义

1. `read_dataset` → 列名、dtype、前 5 行、session_id。
2. `profile` → `suggested_semantic_roles`（启发式，需人工确认）。
3. **对每一列做语义推断**，综合四个线索：
   - **列名**：`date/dt/时间` 可能是时间；`amt/amount/金额` 是度量；`id/no/code` 多是标识符；
     `city/type/state` 多是类别；缩写和编号（`val`、`c1`、`x_2`）置信度低。
   - **取值形态**：日期串、是否为连续数值、唯一值占比（接近行数→可能是 ID）。
   - **单位线索**：字段名或值里含"万/元/%/kg"，注意口径（万元 vs 元）。
   - **业务语境**：用户说"销售数据"时 `sales` 才是营收，否则只认列名不够。
4. 给每列标注角色与置信度：

   - `event_time`：观测/记录时间，可做时间序列；
   - `attribute_date`：出生/入职/毕业/到期，**禁止**做时间轴；
   - `measure` 度量 / `dimension` 分类 / `identifier` 标识（不进统计）。

5. 误判就 `override_types` 修正，`roles={"dt":"attribute_date"}`，然后**重新 `profile`**。

---

## 二、什么时候必须问用户

**只在"拿不准"或"很关键"时问**，不要为无关细节打扰用户。用 AskUserQuestion，给具体选项。

必须问的典型情形：
- 某列含义/单位无法从名字和取值确定，且会影响结果：`amt` 是收入还是成本？单位是元还是万元？
- 日期列角色无法判断（事件时间 vs 属性日期），且决定能否做时间序列。
- 用户请求在两个差异很大的分析之间摇摆："看看这个表" → 要质量审计还是业务洞察？
- 用户点名的指标对应多个候选列："销售额" 指 `sales` 还是 `revenue`？
- 某个选择会实质改变结论，且合理的人会有分歧。

不要问：
- 配色、标题措辞等外观偏好；
- 与当前问题无关的字段；
- 从数据本身能判断清楚的事。

用户不答时：采用最稳妥的假设继续，并在报告「局限与说明」写明假设。

---

## 三、工具与图表的组合决策

### 目标 A：只画图
`read_dataset → profile → visualization(plot_types=[...])`。
图类型由数据和问题决定：看到时间轴且问趋势→`time_series`；看到类别列问占比→`bar`；
看到≥2 数值列问关系→`scatter`。不要因为"能画"就画直方图+箱线图全套。

### 目标 B：了解全貌（用户明确要求"全面分析"时）
`preprocess → statistics + quality + missingness`；有 event_time 加
`time_analysis`/`time_series`；数值列≥2 加 `correlation`。
图表只挑最能说明问题的 3–6 张，例如变量分布 `histogram`、类别构成 `bar`、
时间趋势 `time_series`、强相关 `scatter`。

### 目标 C：时间规律 / 趋势
必须存在 `event_time`：
- `time_analysis`（小时/星期/日分布）、`time_series`（趋势/频率/异常）；
- 图：`["time_series"]`，必要时加 `["time_daily"]` 或 `["time_hourly"]`（当趋势集中在日内时）。
- 报告说明聚合方式与时间粒度。

**红线**：`attribute_date` 不做时间序列；用户坚持时先 `override_types` 改 event_time 并在报告注明。

### 目标 D：变量关系
数值变量≥2 → `correlation`，看 `important_pairs`。
图：`["scatter"]`（两两关系）和/或 `["heatmap"]`（整体相关强度）。
两者只需其一即可时，优先散点（更直观）；列很多时用热力图。

### 目标 E：数据质量审计
`quality + missingness`，关注 `missing_patterns`、重复行、常量列、高基数、异常、偏度。
**通常不出图**；只有当用户要看缺失分布、且图能说明时才考虑。

---

## 四、`plot_types` 可选值

传给 `visualization` 的 `plot_types` 是一个数组，只生成列出的类型：

| 值 | 含义 | 何时用 |
|---|---|---|
| `histogram` | 数值分布直方图 | 关心某数值变量的分布形态 |
| `boxplot` | 箱线图 | 关心离散程度 / 异常点 |
| `bar` | 分类构成柱状图 | 关心各类别数量占比 |
| `scatter` | 数值两两散点 | 关心变量关系（≥2 数值列） |
| `heatmap` | 相关性热力图 | 关心整体相关结构（需先跑 correlation） |
| `time_hourly` | 小时分布 | 事件集中在一天内哪些时段 |
| `time_weekday` | 星期分布 | 事件集中在一周内哪几天 |
| `time_daily` | 每日分布 | 事件按日期的分布 |
| `time_series` | 时间趋势 | 数值随时间的走势（需先跑 time_series） |

支持中文别名（如 `"分布"`、`"散点"`、`"趋势"`）。省略 `plot_types` 才表示全量——
但**应始终显式传入一个列表**，让图的来历在调用里一目了然。

---

## 五、报告区块也要按需（`__context__.sections`）

报告不是越全越好。用 `sections` 明确只渲染承载答案的区块：

| 问题类型 | 建议 sections |
|---|---|
| 只要图 | `["verdict","charts"]` |
| 只要趋势结论 | `["verdict","charts"]` 或 `["verdict","findings","charts"]` |
| 变量关系 | `["verdict","findings","charts"]` |
| 数据质量 | `["verdict","understand","findings","limits"]` |
| 全面分析 | 全部（省略 sections 或列全） |

可用 key：`verdict` / `overview` / `understand` / `findings` / `charts` / `limits`。
省略时渲染所有非空区块（无统计的 findings、无图的 charts 会自动隐藏）。

---

## 六、调用规则（硬性）

- 除 `read_dataset`/`get_manifest` 外，所有工具都要 `session_id`。
- `requires` 是硬前置：缺失返回 `status="blocked"` 与 `missing_prerequisites`；
  见 blocked 先补调，**不要**把 blocked 当结论写进报告。
- `enhances_with` 是可选增强，缺了也能跑，只是结果少一维。
- 统一信封：`tool / status / session_id / summary / data / warnings / next_suggested_tools`。
  `next_suggested_tools` 仅供参考，以用户需求为准。

---

## 七、常见误用

| 误用 | 正确做法 |
|---|---|
| 看到 datetime 列就跑 time_series | 先分清 event_time 还是 attribute_date |
| 含义拿不准却硬猜 | 关键歧义先用 AskUserQuestion 问用户 |
| 用户只要图却跑全套统计 | 只走 read → profile → 选图 |
| 用户没要图却生成一堆图 | 文字能回答就 0 张图 |
| 把全套章节都塞进报告 | 用 sections 只留承载答案的区块 |
| visualization 不传 plot_types | 默认全量，易出无关图；应显式列出 |
| 对常量列 / 标识符画图 | 无信息量，跳过 |
| 把 blocked 当结论写进报告 | 先补前置再重试 |
| 不做 profile 直接统计 | profile 提供类型与语义，是后续工具输入 |

---

## 八、参数传递示例

```bash
python <skill>/scripts/run.py scripts/call_tool.py read /path/data.csv
python <skill>/scripts/run.py scripts/call_tool.py call <sid> profile
python <skill>/scripts/run.py scripts/call_tool.py call <sid> override_types 'roles={"birth":"attribute_date"}'
python <skill>/scripts/run.py scripts/call_tool.py call <sid> time_series 'datetime_column="date"'
python <skill>/scripts/run.py scripts/call_tool.py call <sid> correlation
# 只要关系类的两张图
python <skill>/scripts/run.py scripts/call_tool.py call <sid> visualization 'plot_types=["scatter","heatmap"]'
# 只看趋势
python <skill>/scripts/run.py scripts/call_tool.py call <sid> visualization 'plot_types=["time_series"]'
```

键=值中的值用 JSON 语法：字符串带引号，数字/布尔直接写，对象用花括号。
