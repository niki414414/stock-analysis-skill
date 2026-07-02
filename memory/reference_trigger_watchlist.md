---
name: reference-trigger-watchlist
description: 触发条件追踪清单文件位置和使用方式——动态左侧/右侧入场触发价记录在此
metadata: 
  node_type: memory
  type: reference
  originSessionId: bee2d5dc-c590-4716-a476-5830d3ea84b4
---

每次深度分析（/stock-analysis）产出"等待类型"的具体触发价/量比条件后，写入 `~/stock_trigger_watchlist.md`。

**How to apply**：用户来对话时，如果话题涉及之前分析过的标的，先读取这个文件核对现价是否已碰到触发位，再决定是否需要重新跑六层分析。触发后在文件里标记"已触发"并归档，避免重复提醒。

价格类触发建议用户在同花顺自选股设置原生预警（路径：自选股→长按个股→预警/提醒）；量比类触发同花顺不支持，需对话时用脚本核对。

协作模式由用户2026-06-10确认：**清单制 + 用户来时核对**（而非定时cron任务，因数据源每日稳定性不可靠）。

相关：[[stock-skills-project]] [[trading-framework-checklist]]
