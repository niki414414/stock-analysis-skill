---
name: feedback-company-pool-missing
description: 分析时公司表查不到的股票，WebSearch补充同时提醒用户更新公司表
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 1be98b76-ad3c-4a54-932f-34a6b4b44c25
---

分析股票时先查 `~/Desktop/tz/公司.xlsx` 的公司表（`event_map_query.py company {名称}`）。如果查不到：
1. 不阻断分析，用WebSearch查主营业务确定赛道归属
2. 在分析输出末尾提醒用户："XX不在公司表中，建议下次更新事件地图时补录"

**Why:** 用户希望公司表保持完整，作为股票→赛道的唯一对照源，漏录会导致后续分析时赛道判断不准。
**How to apply:** 每次 `/stock-analysis` 开头查company，查不到时标记提醒，不跳过。
