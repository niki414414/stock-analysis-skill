---
name: reference_tz_folder_structure
description: ~/Desktop/tz/文件夹结构，2026-07-21重新整理过，机器管理的数据全部挪进了技能数据/子文件夹
metadata: 
  node_type: memory
  type: reference
  originSessionId: dff83b5c-04b0-4fbd-bcaa-c6049d3f530d
---

2026-07-21把`~/Desktop/tz/`重新整理过（用户主动要求"不断更新的事件地图不用户平时不看，按层次管理"）。

**顶层现在只留用户会自己看的东西**：
- `holdings.csv`（持仓，个人小文件）
- `repo/`（GitHub备份仓库，niki414414/stock-analysis-skill，见[[project-backup-repo-20260702]]）
- `研究材料/`（用户丢研究材料进来，我处理）
- `分析记录/`（分析输出记录）
- `半导体研究报告/`、`电力产业研究报告/`（专项研究报告）
- `量化框架思路/`（2026-07-21新增，小红书量化技术清单）

**`技能数据/`是新建的子文件夹，装所有机器管理、用户不常看的东西**：
- `科技产业事件/`（原来在顶层，装csvMMDD版本文件夹）
- `非科技产业事件地图/`（新建，把原来散在顶层的`非科技主线产业事件地图_CSV包_*`全部挪进来，包括旧的散装xlsx快照）
- `公司.xlsx`、`company_code_map.csv`（原来在顶层）
- `market_daily_snapshot/`（daily_snapshot.py输出）
- `迁移包_latest/`（原来在顶层）
- `claude-memory-旧备份-20260608/`（原名`claude-memory`，2026-06-08的旧memory备份，早已被当前memory系统取代，仅存档不用）

**对应的代码路径已全部同步更新**（`catalyst_left_side_scanner.py`/`event_map_query.py`/`event_map_updater.py`/`daily_snapshot.py`/`framework_snapshot.py`里的常量，以及所有SKILL.md里的文档引用），2026-07-21当天逐个跑过验证（tech/nonfin两个来源的`status`/`events`命令、公司池路径、迁移包`status`），确认都正常。

**Why：** 之前非科技事件地图的版本文件夹是直接散落在`~/Desktop/tz/`顶层的（`NONFIN_DIR`常量原来就是`~/Desktop/tz`本身，用glob匹配"非科技主线产业事件地图_*"前缀），跟科技事件地图（本来就有自己的`科技产业事件/`子文件夹）不对称，是顶层混乱的主因。

**How to apply：** 以后任何新脚本/技能如果要引用公司池、事件地图、迁移包、每日快照这些路径，一律指向`~/Desktop/tz/技能数据/`下面，不要再假设它们在`~/Desktop/tz/`顶层。
