---
name: project-stockpool-redesign
description: 选股池改造计划，当前150只候选池太窄、覆盖不足、多为过热标的，待下次专项会话解决
metadata: 
  node_type: memory
  type: project
  originSessionId: cdab86e9-8bdd-46dd-a40c-490fbca13cb1
---

当前选股池问题已明确，需要专项时间改造。

**Why:** 现有方法从 market_status.yaml 的 leader_stocks 手工选，每个子赛道只有1-2只龙头，导致：池子只有~150只、多为已充分发现的过热标的、赛道覆盖不全、挖掘深度不够。

**How to apply:** 下次用户提起"选股池"或"挖掘候选标的"时，优先启动这个改造项目，不要再用老方法凑数。

## 待做：方案A+C结合（优先推荐）

**方案A — 宽库+量化筛选脚本 `stock_screener.py`**
- 从 Tushare 拉取 priority=1/2 子赛道的全行业成分股（每赛道20-50只）
- 自动按轨迹分类（B/C/E）+ MA结构 + 资金流向打分
- 输出"进入六层分析候选名单"，替代手工猜

**方案C — 事件地图驱动**
- 从 event_map_query.py 的 mapping 表提取受益公司类型
- 补全代码映射（目前 mapping 只写了公司类型，不是具体代码）
- 作为当周事件驱动候选，与方案A叠加过滤

**两方案结合逻辑：**
1. 方案A宽筛 → 按赛道/轨迹输出候选
2. 方案C叠加 → 有事件催化的优先上浮
3. 结果进六层分析

## 辅助选项
**方案B（备选）：** 用户在同花顺按赛道建自选股板块（每赛道20-30只），我读列表跑批量技术筛选 — 优点是用户把控标的质量，缺点需要初始维护。

## 实施前提
- 事件地图 mapping 表需要加入具体股票代码列（目前只有"代表公司类型"）
- market_status.yaml 的行业分类需要与 Tushare industry 字段对应表建立映射
