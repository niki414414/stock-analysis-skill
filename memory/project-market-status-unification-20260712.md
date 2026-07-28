---
name: project-market-status-unification-20260712
description: market_status.yaml从1205行精简到24行；赛道数据源统一为事件地图sectors_status.csv；top-picks screener已改读；research_log.md新建
metadata: 
  node_type: memory
  type: project
  originSessionId: 2da5b9ee-d84f-41f9-b8ba-6110ef04ff3e
---

2026-07-12完成一次数据源统一+死代码清理，起因是排查商业航天赛道在 `sectors_status.csv`
（事件地图）和 `market_status.yaml`（两个都声称记录"赛道处于什么阶段"）之间的表述不一致。

**Why:** 查证发现 `market_status.yaml` 的赛道阶段/优先级字段自 2026-07-05 赛道校准研究
（IC=0.12，见 [[feedback_calibration_method]]）后就已经被 `analysis-prompt-template.md`
弃用——框架早就改用 corrections表过热警告+事件地图活跃催化数 判断市场regime，但当时升级
只换了逻辑，没有清理旧脚手架，导致 `top_picks_screener.py` 和两处 SKILL.md 指令还在读一份
早就不该是权威源的yaml，而 `event_map_query.py` 其实已经建好了替代查询命令（status子命令）
只是没人接上。这是"新逻辑接上、旧的没拆"这个失败模式的第二次出现（第一次见
[[feedback_tool_integration_standard]]）。

**做完的事：**
1. `top_picks_screener.py` 的 `load_static_universe()`/`build_universe()` 改为调用新增的
   `load_sector_status()`，从 `sectors_status.csv` 读取赛道元数据（label/theme/priority/
   stage/style_position），不再读 market_status.yaml。67个重合赛道逐字段验证，只有
   priority/style_position完全一致，5个赛道stage不同——差异方向正是"事件地图更新了但
   yaml没跟上"，证明迁移前数据确实滞后。commit: `6a494d8`
2. `market_status.yaml` 从1205行精简到24行：删除 theme_framework（66个子赛道的死字段：
   wave_number/wave_position/leader_stocks/sector_20d_pct/catalyst_quality/ai_correlation，
   sectors_status.csv已验证是超集）、weekly_calibration、watch_pool、defense_pool、
   sh300_weekly_change、recent_catalysts——全部零消费方（只有已归档的
   analysis-prompt-template-v2.md引用）。只保留 `current_temperature`（Layer1条件1/4
   仍在用的WebSearch备用来源）。commit: `e092661`
3. 同步清理三处消费方指令：stock-analysis/SKILL.md、top-picks/SKILL.md（指向已删字段的
   Read指令）、两个技能各自的output-format-template.md（wave_number/wave_position提醒、
   yaml stage健康检查提示）。同一commit。
4. 新建 `~/.claude/skills/stock-analysis/references/research_log.md`——技术方法论沉淀
   笔记本，跟 market_thesis.md（叙事底稿）、事件地图（产业数据）三分工。只有验证通过
   （状态=已验证-采纳）才走 /update-framework 正式接入框架文件。首条记录：`macro_heat`
   （九转计数+MACD柱+成交额+轮动的大盘动能三态判断）——清理时发现last_updated只有4天前
   （全yaml唯一近期维护过的字段），但从未被任何框架逻辑读取，属于"写了但没有归宿"，
   已作为待评估条目迁移进去，不是直接删掉。

**未做完，留了两个待办：**
- [[project-market-status-overlap-review]]：`current_temperature` 这个人工字段是否
  和 corrections表+事件地图活跃催化数 的市场regime判断重叠，待专项核对
- `research_log.md` 里 macro_heat 条目本身：需要判断这套方法是否有独立预测力，
  值不值得正式接入框架，还是明确记录"测过不采纳"

**How to apply:** 以后查赛道阶段/优先级，一律用
`python3 ~/.claude/skills/stock-analysis/scripts/event_map_query.py status --keyword {赛道}`，
不要再读market_status.yaml（除了current_temperature）。用户丢技术方法论/回测复盘材料
过来时，先记进research_log.md，不要直接口头讨论完就忘，也不要不经验证直接写进正式框架文件。
