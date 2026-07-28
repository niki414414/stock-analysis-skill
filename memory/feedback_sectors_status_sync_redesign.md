---
name: feedback_sectors_status_sync_redesign
description: "sectors_status.csv与事件地图的同步机制2026-07-22从\"prompt提醒\"重新设计为代码强制的关键词自动匹配"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: dff83b5c-04b0-4fbd-bcaa-c6049d3f530d
---

`sectors_status.csv`（76个子赛道阶段表）2026-06-18建档时设计成"用户更新事件地图时，同步更新sectors_status中对应赛道的stage和event_map_status"（见[[industry-events-map]]），但这句话只写进了文档，从没写进代码强制执行。核对发现76行里62行的`last_updated`还停在建档当天或更早，`signal_stock_code`/`event_map_status`两个字段实际上从没被真正同步过——跟[[feedback_tool_integration_standard]]是同一类失败模式："新逻辑接上、旧流程没有代码强制"。

**Why：** 用户2026-07-22追问"sectors_status.csv怎么还跑数据缺口"时，明确回忆起这条之前讨论过、且已经定过调子——"6月1日维护的这个后面就不维护了，要重新设计对齐的机制"，不是继续手动打补丁。

**How to apply：** 已在`event_map_updater.py`里新增`sync_sectors_status()`，跟公司池同步（STEP 5.5）走同一模式，`apply()`内自动调用（仅tech source，sectors_status无nonfin对应表）：
- 靠`config/sectors_status_crosswalk.csv`（sector_id→关键词列表，人工一次性维护）做关键词匹配，只对**可判定字段**自动写：`signal_stock_code`（并集追加，绝不覆盖已有内容）、`event_map_status`（记录最新关联事件ID）、`last_updated`。
- `stage`/`wave_number`/`wave_position`/`priority`这几个**赛道阶段判断字段，机器不下判断，只打印提醒清单**，人工决定要不要改——这是和公司池同步的关键区别：公司池"是否已收录"是事实判断，可以全自动；赛道处于什么阶段是主观研判，不能全自动。
- 2026-07-22首次跑通：backfill一次性把`signal_stock_code`覆盖率从66/76填到（部分未覆盖，主要卡在mapping表"代表公司/公司类型"字段是长描述句而非逗号分隔名单，同[[feedback_tool_integration_standard]]提过的"公司池skipped_no_code"同一类局限），`event_map_status`从14/76填到45/76。
- **关键词踩坑记录**（已修）：过泛关键词会跨赛道误撞——"清洗"(equipment_cmp_clean) 撞上"数据清洗"(AI-DATA事件)、"叶片"(wind_power_equipment) 撞上"燃机叶片"(GAS-TURBINE事件，非风电)。凡是通用制造业/工序名词（清洗/整机/组件/叶片/主轴/铸件/电缆/电机/服务器 单独出现）都必须收窄成复合词（"清洗设备"/"风电叶片"等），新增赛道关键词时要留意这一类。

crosswalk配置文件已加入`migrate`的07_技能代码复制清单，此前漏了这一步（同[[project-backup-repo-20260702]]提过的"新脚本必须加进migrate复制清单"教训）。

**2026-07-22后续：系统性核查后确认`top_picks_screener.py`是死代码，进一步精简了schema。** 用户追问"这个字段还有多少价值/是否过时/能否被别的工具替代"，逐一排查全部消费方后发现：
- `top_picks_screener.py`（当初分析stage/priority怎么起作用的文件）**已经不被任何SKILL.md调用**——top-picks早改用`catalyst_left_side_scanner.py`（催化窗口模型+量价三维，2026-07-21改的），完全不读sectors_status.csv。之前判断"stage/priority还在被消费"是分析了死代码，是个错误结论，已在对话里当面纠正。
- `wave_number`/`wave_position`/`catalyst_quality`/`ai_correlation`/`priority`/`style_position`六个字段：现行框架零消费方（只有已归档v2模板+已下线的screener引用过），2026-07-22已从schema里物理删除（`event_map_updater.py`的TECH_TABLES、`event_map_query.py`的STATUS_COLS、以及当前csv0721实际文件都同步改了）。
- `stage`：唯一还有真实决策依赖的字段——`analysis-prompt-template.md`第704行"破位升级路径①"直接读它做决策分支。但同一份文件里v4.5的Pocket Pivot路径（路径③）设计时就已经改用corrections表过热警告+事件地图🔴/🟡活跃催化判断"赛道是否主升"，明确说明是因为这个字段"校准频率不足(IC=0.12)"。2026-07-22把路径①删除、"晚进场候选"行和"强势板块漏单提醒"都统一改成同一口径后，`stage`字段不再是任何决策输入，只保留`output-format-template.md`的展示+过时性自查用途（这个自查本身有价值：对比stage声明值与实际20日涨跌幅，能捕捉staleness，正是这次发现`ai_infra`/`optical_module_cpo`顶着过期"主升期"标签走完-18~19%下跌的同款检查逻辑）。

**Why这次要往下深挖，不能停在"清理6个死字段"就收工：** 用户明确要求"系统化看，避免退役了其他又跑不动"——如果只删6个字段就以为完事，会漏掉`stage`在704行的真实活跃依赖（会让升级破位⚠️路径①静默失效，不报错但再也不触发）；而系统化排查下去又意外发现更大的问题（top-picks整条消费链已经不存在了），比一开始设想的"给76个子赛道接个动量计算"的优化方向更彻底——没有消费方的字段不需要优化，需要的是先确认清楚谁在用、谁已经不在用了。

**How to apply：** 以后遇到"这个字段/这个机制是不是该退役"的问题，标准流程是：①穷举所有SKILL.md+脚本对该字段/文件的引用 ②对每个引用点确认它是否真的被当前live workflow调用（不能只看grep命中，要往上追一层看调用方是否还在用）③区分"决策输入"vs"展示/自查用途"，前者要先有替代方案验证过才能改，后者影响小可以直接改。

**2026-07-22最终清理：`top_picks_screener.py`和它专属的`references/output-format-template.md`（v2.0主线分区看板格式，同一批四大主线架构遗留）已物理删除**——确认全仓库里除了我自己刚写的说明性注释外，没有任何代码/SKILL.md在调用它们，`top-picks/references/`目录现在只剩`catalyst_left_side_scanner.py`一个活跃脚本。注意区分：`analysis-prompt-template-v2.md`保留不删——`stock-analysis/SKILL.md`里明确写了"v3.0已存档为references/analysis-prompt-template-v2.md"，这是**有意保留的版本历史快照**，不是意外遗留的死代码，两者判断标准不同：前者是"没人删、也没人说要留着"，后者是"明确写了这是归档记录"。
