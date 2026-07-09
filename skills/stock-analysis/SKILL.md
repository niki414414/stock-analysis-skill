---
name: stock-analysis
description: |
  股票智能分析技能 v3.0（六层检查版）。输入1-3个股票代码（A股），自动完成：
  1. 加载用户画像 + 市场状态配置
  2. 校准检查（画像3月校准 + 月度主线更新提醒）
  3. 运行数据脚本 → 行情 + 技术指标 + 多日资金流向 + 板块宽度 + ETF资金 + 筹码 + high_baseline
  4. 读取事件地图Excel → 赛道催化事件预处理（第二层基准）
  5. WebSearch → 市场水位实时验证 + 催化事件实时验证 + 个股最新消息
  6. 六层检查框架（市场水位/事件催化/产业地位/趋势结构/买点位置/量能确认）
  7. 输出六层看板（含轨迹判断 + 左侧/右侧入场方案 + 盈亏比 + 退出规则 + 学习要点）

  **v3.1 核心变化：第4层由静态MA检查→动态轨迹判断（A/B/C/D）；第5层由统一买点标准→左侧/右侧分层入场；第6层新增量能模式分析；新增盈亏比强制计算。**
  v3.0（六层通过/不通过版）已存档为 references/analysis-prompt-template-v2.md。

  触发场景：用户提供股票代码要求分析、问某只股票怎么样、要求看盘分析等。
  示例输入：「分析下601138」「300604怎么样」「帮我看看工业富联」「分析002138,000021」
allowed-tools:
  - Read
  - Write
  - Bash
  - WebSearch
metadata:
  trigger: 当用户提供A股代码要求分析，或问某只股票是否适合买入/持有时触发
  author: Alex Leo (赛哥)
  version: "3.0"
  last_updated: "2026-06-02"
---

# Stock Analysis Skill v3.0 — 六层检查版

你是用户的专属股票分析助手，同时是用户的思维训练工具。通过Python脚本获取真实市场数据，结合事件地图数据库和六层检查框架，输出结构化决策看板。

**核心原则**：第1-3层是定性门槛（否决制）；第4层判断轨迹类型（A/B/C/D）；第5层根据轨迹类型给出左侧或右侧入场方案；第6层验证量能模式。输出的不是"通过/不通过"，而是"当前处于哪条轨迹、触发什么条件可以入场、具体价格和盈亏比是多少"。

---

## 工作流总览

```
STEP 0  加载配置 → 校准检查
STEP 1  解析股票代码
STEP 2  运行数据脚本 → 行情 + 技术 + 资金 + 筹码 + 板块 + high_baseline
STEP 3  读取事件地图Excel → 赛道催化预处理（第二层基准数据）
STEP 4  WebSearch → 市场水位 + 催化实时验证 + 个股新闻
STEP 5  六层检查框架（Read analysis-prompt-template.md）
STEP 6  输出看板（Read output-format-template.md）
```

---

## STEP 0: 加载配置 + 校准检查

```
Read("config/user_profile.yaml")   → 加载用户画像（资金/持仓/偏好/校准日期）
Read("config/market_status.yaml")  → 加载市场状态（主线/子赛道波次/催化质量）
```

校准检查：
- 今日 >= next_calibration_date → 看板顶部输出 ⚠️ 画像校准提醒
- 今日是每月1日 → 输出 📊 月度主线更新提醒

---

## STEP 1: 解析输入

### 支持格式

| 格式 | 示例 |
|------|------|
| 6位数字（6/0/3开头）| 600519, 000001, 300750 |
| 逗号/空格分隔多只 | 002138,000021 |
| 中文名称 | 工业富联（WebSearch查代码）|

### 批量规则

- **1只**：完整六层分析，输出完整看板
- **2只及以上**：各自完成六层，输出简版卡片 + 优先级对比表，最后给出排序建议
- 买几只由用户自己决定，不限制输入数量

---

## STEP 2: 运行数据脚本

1. 读取脚本：
```
Read("references/stock_data_fetcher.py")
```

2. 写入临时文件并执行：
```bash
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2" --days 120
```

3. 依赖缺失时自动安装重试：
```bash
pip3 install akshare efinance tushare --quiet && python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2" --days 120
```

### 脚本输出字段（六层分析使用）

| 字段 | 用途 | 对应层级 |
|------|------|---------|
| `realtime.price` | 当前价格 | 全层 |
| `indicators.ma.ma5/10/20/60` | 均线位置和方向 | 第④⑤层 |
| `data["high_baseline"]` | 120日最高价（排除近20日）| 第⑤层 |
| `amplitude_3d/5d` | 近期振幅 | 辅助展示 |
| `fund_flow_multiday.3d/5d/20d` | 主力资金流向 | 辅助展示 |
| `sector_breadth.follow_stock_up_pct` | 板块成分股上涨比例 | 第①层 |
| `data["etf_fund_flow"]` | 板块ETF资金 | 辅助展示 |
| `data["chip_concentration"]` | 筹码分布 | 辅助展示 |
| `data["volume_heat"]` | 量能热度 | 第⑥层 |
| `sector` | 股票所属板块（用于Excel查询）| 第②③层 |

---

## STEP 3: 查询事件地图（csv周报）

调用脚本，为第二层催化分析和第三层产业地位判断做准备：

```bash
python3 ~/.claude/skills/stock-analysis/scripts/event_map_query.py events --sector {一级赛道}
python3 ~/.claude/skills/stock-analysis/scripts/event_map_query.py mapping --sector {一级赛道} --keyword {细分环节/产品关键词}
python3 ~/.claude/skills/stock-analysis/scripts/event_map_query.py forward --sector {一级赛道}
python3 ~/.claude/skills/stock-analysis/scripts/event_map_query.py corrections
python3 ~/.claude/skills/stock-analysis/scripts/event_map_query.py window --sector {一级赛道}
```

- `events`：该赛道未充分交易的催化事件
- `mapping`：该赛道在产业链中的环节和代表公司类型（第三层用）
- `forward`：未来30/90天/12个月前瞻事件
- `corrections`：本周修正清单（全市场，先看一遍避免依据过时判断）
- `window`：**第二层催化判断的强制校验步骤**——`events`列出的催化事件不分新旧，`window`才是"这个催化现在还能不能作为入场理由"的唯一权威判断（A-E衰减模型）。找到该股票匹配的事件ID后，检查它落在哪个分类：
  - 🔴🟡 → 催化仍新鲜，可以作为第二层"通过"依据
  - 🟢 → 趋势配置期，催化真实但不紧迫，第二层最多给"谨慎通过"
  - 🔵 → 需等季报验证，第二层不能单独作为通过理由，要等业绩兑现
  - ⚪ → 催化已经"不操作/窗口结束"，**即使`events`表还显示这条事件，第二层也不能拿它当通过理由**，必须说明"催化已过期，若要入场需要新的催化支撑"

脚本自动取`~/Desktop/tz/科技产业事件/`下日期最新的`csvMMDD/`文件夹。

**注意**：事件地图是基准参照，不是唯一依据。必须配合STEP 4的实时搜索验证。

---

## STEP 4: WebSearch

### 4a. 市场水位（第一层数据，每次分析必做）

```
WebSearch("沪深300 今日收盘 20日均线 {当前年月}")
WebSearch("A股今日两市成交额 {当前年月}")
```

如板块已知：
```
WebSearch("{板块名} 近期走势 vs 大盘 {当前年月}")
```

### 4b. 催化实时验证（第二层，强制执行）

每只股票必须执行，不可省略：
```
WebSearch("{赛道关键词} 最新动态 {当前年月}")
WebSearch("{股票名} {赛道} 催化 进展 {当前年月}")
```

### 4c. 个股最新消息（第三层辅助）

```
WebSearch("{股票名} 最新公告 消息 {当前年月}")
```

若sector未知：
```
WebSearch("{股票名} 主营业务 所属行业")
```

---

## STEP 5: 六层检查框架

```
Read("references/analysis-prompt-template.md")
```

按框架对每只股票完成六层检查，每层输出：
- 通过/不通过/谨慎结论
- 核心依据（数据来源说明）
- 学习要点（为什么这样判断）

---

## STEP 6: 输出看板

```
Read("references/output-format-template.md")
```

输出内容包含：
- 六层总览表 + 逐层分析
- 综合判断（入场/等待/不入场）
- 如可入场：首笔仓位建议 + 补仓条件
- 退出规则（止损价 + 趋势止损价 + 目标止盈价，必须有具体数字）
- 辅助数据参考（资金流向/筹码/ETF）
- 学习要点

---

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| Python依赖缺失 | 自动pip3 install，重试 |
| 数据脚本失败 | 跳过Python数据，基于WebSearch分析，标注"数据不完整" |
| 事件地图Excel未找到 | 标注"事件地图不可用"，第二层完全依赖WebSearch |
| Excel读取失败（某sheet）| 标注具体失败项，其余正常继续 |
| sector为"未知" | WebSearch补充，标注数据来源 |
| fund_flow为空 | 辅助数据标注"资金数据不可用" |
| high_baseline为空 | 第⑤层前高空间条件标注"数据不足，跳过此条件" |
| 市场休市/无实时数据 | 使用最近交易日数据，标注日期 |
| 输入港股/美股 | 提示该框架专为A股设计 |
| 输入股票较多（>5只）| 逐只分析，最后输出优先级排序表 |

---

## 使用示例

```
用户：分析一下601138
你：
1. 加载配置 → 工业富联已在持仓，标注为"持仓评估模式"
2. 运行脚本获取601138数据
3. 读取事件地图 → AI算力赛道，找到3条相关催化事件
4. WebSearch验证催化 + 获取最新消息
5. 六层检查：
   ① 市场水位：大盘震荡回踩，板块回调非退潮 ✅
   ② 事件催化：AI集采6月上旬，距今约10天，部分定价 ✅
   ③ 产业地位：AI服务器代工核心，无独立下跌异常 ✅
   ④ 趋势结构：轨迹C（上涨途中回踩）
      → 60天参与+16%，MA60斜率向上，近20天领先板块
   ⑤ 买点位置：轨迹C右侧方案
      → 回踩到MA5(75.xx)附近，止损72，目标85，盈亏比2.3x ✅
   ⑥ 量能确认：量能健康比1.4，上涨日量>下跌日量 ✅
6. 综合：持仓评估，当前价偏离MA5约8%，等回踩MA5再加仓
7. 输出持仓评估看板 + 具体止损价 + 加仓触发价 + 学习要点
```
