---
name: project-market-status-overlap-review
description: 待办——core_temperature（yaml手动温度字段）是否与Layer1 corrections表+事件地图活跃催化数的市场regime判断重叠，需专项时间核对
metadata: 
  node_type: memory
  type: project
  originSessionId: 2da5b9ee-d84f-41f9-b8ba-6110ef04ff3e
---

2026-07-12 清理 market_status.yaml 死字段时，保留了 `current_temperature`（"偏热"手动字段），
因为它是当前 `analysis-prompt-template.md` 第一层"市场水位"检查条件1/条件4的**备用来源**
（WebSearch为主，market_status.current_temperature为辅）。

**Why:** 用户提出怀疑——这个手动维护的全市场温度判断，会不会跟框架里另一套"判断赛道/大盘是否
过热"的逻辑（corrections表显式过热警告 PE分位>90%+累计涨幅>60% + 事件地图🔴/🟡活跃催化数量，
见 `analysis-prompt-template.md` 第733行一带，2026-07-05校准研究后确立为主要依据）在回答同一个
问题，只是一个人工一个数据驱动，两边都留着可能是重复判断源，违反 /update-framework STEP 0
的奥卡姆剃刀原则（"发现有重复计算源，先合并成一个"）。

同一批清理还发现了 `macro_heat` 字段（九转计数+MACD柱+成交额+轮动判断大盘动能三态）——
这个已经确认是"写了但从没被任何框架逻辑读取过"，已迁移进 [[research_log]]
（`~/.claude/skills/stock-analysis/references/research_log.md`）作为待评估条目，
不在此重复。这条 memory 只关注 `current_temperature` 这一个字段的重叠问题，因为它目前还是
活跃消费的（不是死字段），删不得，只能等专项时间理清分工。

**How to apply:** 用户主动提起"市场状态判断"、"要不要清理market_status.yaml剩余部分"、
或者做一次系统性的"框架里有哪些地方在判断市场温度/是否过热"梳理时，优先处理这条。
核对方法参考07-05赛道校准研究的方法（IC值验证），判断 current_temperature 这个人工字段
对预测有没有增量价值，还是纯粹和 corrections+事件地图 判断同一件事、可以合并成一个。
