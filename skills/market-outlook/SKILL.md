---
name: market-outlook
description: |
  大盘走势、异常波动原因、科技与非科技板块轮动及未来一周操作框架 v2.0。
  当用户问大盘为何大涨大跌、当前该进攻还是防守、科技失血后的资金去向、
  非科技短线机会、主线状态、风格切换或结合持仓制定周度方案时使用。
---

# Market Outlook 2.0

核心判断和固定输出格式见 `references/market-state-machine.md`。本技能负责取数、发现、
核验和编排；个股最终结论继续调用 `stock-analysis`，不得用行业强势替代个股分析。

## 核心原则

1. 价格结构只能说明“发生了什么”，不能自动说明“为什么”。
2. 行业相对强势只进入研究池，不构成买入信号。历史影子回测未支持机械追涨。
3. 非科技事件地图是已知线索库，不是完整事实库；无覆盖必须外部搜索补漏。
4. 任何操作候选必须依次通过：资金、催化、公司质量、价格/盈亏比四道门。
5. 找不到可靠催化，或公司与题材关系无法核实，结论就是“不进入操作池”。

## 工作流

### STEP 0：市场状态原始数据

```bash
python3 "$TZ_CODEX_HOME/repo/skills/stock-analysis/scripts/market_state_fetcher.py" \
  --json > /tmp/market-state.json
```

若为非交易日或盘中，使用最近一个完整收盘日，并在报告第一屏注明数据日期。

### STEP 1：异常结构与非科技机会发现

```bash
python3 "$TZ_CODEX_HOME/repo/skills/market-outlook/scripts/market_opportunity_radar.py" \
  --market-state /tmp/market-state.json --top 10 --json \
  > /tmp/market-opportunity-radar.json
```

该脚本：

- 用指数、广度、成交额识别异常结构；
- 扫描申万一级行业相对沪深300的当日/5日/20日强弱；
- 直接调用非科技事件地图 `window`，不依赖当前缺失的 `sectors_status` 表；
- 将方向分为 `active_catalyst_found` 与
  `coverage_gap_requires_external_search`；
- 输出待核验公司和外部搜索词，但不输出买入信号。

### STEP 2：异常波动原因核验

若 `abnormal_structure.triggered=true`，必须执行联网搜索。优先使用脚本给出的
`cause_search_queries`，并补查：

- 宏观/政策：央行、监管、权威媒体；
- 外盘/跨资产：海外指数、利率、汇率、商品；
- 产业：行业政策、价格、订单、供需；
- 公司：公告、业绩、减持、监管；
- 交易结构：ETF申赎、两融、成交额、拥挤和技术破位。

输出必须分开：

1. **结构诊断**：由行情数据直接支持；
2. **主要原因**：至少一条可靠、时点吻合的外部证据；
3. **次要原因**：有证据但解释力较弱；
4. **已排除说法**：时点或数据不吻合；
5. **置信度与待确认项**。

不得把搜索结果的标题、市场传言或价格反推当成已证实原因。

### STEP 3：非科技轮动二次核验

逐一处理雷达方向：

- `active_catalyst_found`：联网核实事件是否真实、仍在有效期、当天是否新增；
- `coverage_gap_requires_external_search`：搜索政策、订单、涨价、业绩和供需变化；
- 只有单日防守性上涨、无新增催化：标为“资金避险候选”，不称新主线；
- 有催化但没有板块扩散：标为“题材试探”；
- 有资金持续性、有效催化、板块扩散：才标为“轮动候选”；
- 对公司候选调用 `stock-analysis`，核实主营相关度、业绩质量、估值、趋势和风险。

公司关联为 `event_text_fallback` 或 `event_text_code_map_only` 时，必须查公告或公司业务
资料确认，不能直接采用。

### STEP 4：科技主线与事件修正

```bash
python3 "$TZ_CODEX_HOME/repo/skills/stock-analysis/scripts/event_map_query.py" \
  window --source tech
python3 "$TZ_CODEX_HOME/repo/skills/stock-analysis/scripts/event_map_query.py" \
  window --source nonfin
python3 "$TZ_CODEX_HOME/repo/skills/stock-analysis/scripts/event_map_query.py" \
  corrections
```

### STEP 5：组装报告

完整读取 `references/market-state-machine.md`，按 2.0 固定模板输出：

- 一句话结论与四状态；
- 异常结构/原因；
- 非科技轮动机会表；
- 科技主线状态；
- 三种盘面应对和未来一周触发器；
- 若用户提供持仓，再将市场结论逐只映射到持仓动作。

## 降级处理

- 单个数据源失败：标明不可用，继续用其余证据，不补造数字。
- 非科技状态表缺失：属已知限制，雷达不调用 `status --source nonfin`。
- 事件地图无覆盖：生成外部搜索队列；搜索仍无可靠证据则不操作。
- 联网搜索不可用：只给结构诊断和待核验清单，不给确定性原因或机会结论。
