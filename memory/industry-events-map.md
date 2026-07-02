---
name: industry-events-map
description: 桌面tz文件夹里的科技产业事件地图，最新版本csv0701（2026-07-01），event_map_query.py已接入框架第二/三层结构化查询
metadata:
  node_type: memory
  type: reference
  originSessionId: 61f80f56-c928-4e6a-81ad-d8f0b97c5025
---

# 科技产业事件地图

## 文件位置（2026-06-15起新格式）
`/Users/niki/Desktop/tz/科技产业事件/csvMMDD/`（每周一个新文件夹，如`csv615`=6月15日更新）

文件夹内7个csv，文件名均带`_YYYYMMDD`后缀：
- `events_YYYYMMDD.csv`：事件总库，**AI读取优先级最高**（README原话）
- `mapping_YYYYMMDD.csv`：事件-产业链映射树
- `forward_YYYYMMDD.csv`：未来30天/90天/12个月前瞻事件
- `corrections_YYYYMMDD.csv`：本周动态修正清单（哪些事件已兑现/超预期/低于预期）
- `reviews_YYYYMMDD.csv`：已兑现/已交易事件复盘（扩散路径：第一阶段→第二阶段→第三阶段/影子方向）
- `weekly_YYYYMMDD.csv`：周报摘要（本周主线、市场交易状态、历史相似案例）
- `sources_YYYYMMDD.csv`：来源留痕（含URL，可追溯）
- `README_YYYYMMDD.txt`：使用说明

旧的xlsx周报（`2026-2027科技产业事件地图周报_YYYYMMDD_动态更新版.xlsx`）仍会更新，但其sheet结构已变化（02_事件总库表头位置变动，原`header=1`不再适用），且csv是专为AI读取设计的格式——**已不再使用xlsx作为数据源**。

## 列结构（csv格式，2026-06-15）

- `events`：事件ID/一级赛道/二级事件/事件名称/事件时间/当前状态/重要程度/影响周期/市场关注度/信息来源类型/来源URL/**是否已被交易**（值可为组合状态如"充分交易/高波动"）/是否存在预期差/主要影响方向/后续观察指标/最新更新时间/备注
- `mapping`：映射ID/事件ID/事件名称/一级赛道/一级产业/二级环节/三级零部件材料设备/代表公司公司类型/当前市场关注度/是否已炒作/预期差/验证指标/风险点/最新更新时间/来源
- `forward`：前瞻ID/事件/时间/发生概率/重要程度/一级赛道/可能受益方向/可能受损方向/是否已被交易/预期差/后续观察指标/事件窗口/更新时间/来源
- `corrections`：更新日期/问题/判断/对应事件ID/后续动作/优先级/状态/备注（全市场维度，不分赛道，约7条）

## 与框架的关系
- **第二层催化**：作为基准参照，但必须配合实时搜索双重验证
  1. 先查`events`（排除"充分交易"），了解已知催化和当前交易状态
  2. 查`corrections`，确认本周是否有判断修正（避免依据已被修正的旧结论）
  3. 查`forward`，判断该赛道未来30-90天是否有"在路上"的催化
  4. 再强制实时搜索该赛道最新动态，验证以上判断是否仍成立
  - 数据每周更新一次，不能作为唯一依据
- **第三层产业地位**：查`mapping`，确认个股在产业链中的环节和地位

## 代码（2026-06-15已更新为csv格式）
`~/.claude/skills/stock-analysis/scripts/event_map_query.py`已接入框架第二层(2a)和遗漏风险扫描类型二：
```
python3 event_map_query.py events --sector {一级赛道}       # 默认排除含"充分交易"的事件
python3 event_map_query.py mapping --sector {一级赛道} --keyword {环节/产品关键词}
python3 event_map_query.py forward --sector {一级赛道}      # 未来30/90天/12个月前瞻
python3 event_map_query.py corrections                       # 本周修正清单，全市场
```
脚本在`~/Desktop/tz/科技产业事件/`下找`csv*`文件夹，按文件名中的`_YYYYMMDD`日期取最新一周（不依赖文件夹命名规律本身，因此下周即使命名为`csv622`等格式也能正确识别）。

**2026-06-18 格式变化**：csv618文件名后缀从 `_early_signals` 更新为 `_ai_software_services`（如 `events_20260618_ai_software_services.csv`），另有 `signals_early_YYYYMMDD_ai_software_services.csv`。脚本正则已从 `_(\d{8})\.csv$` 放宽为 `_(\d{8})`，兼容所有命名变体。

**2026-06-18 赛道扩充**：新增AI应用/算力服务6个子赛道（此前事件地图偏硬件侧，软件/服务层基本空白）：
- AI应用基础设施：算力租赁/GPU云服务、Token工厂/算力金融化
- AI应用：企业级AI SaaS、AI办公/内容生产、Agent基础设施、RAG/知识工程、AI数据服务/合规
- 消费电子与端侧AI：AI PC/AI手机/NPU、AI眼镜/XR
- 新增一级赛道关键词：`AI应用`、`AI应用基础设施`、`AI算力`、`消费电子与端侧AI`
- 轮动分析报告已输出至 `~/Desktop/tz/轮动分析_20260618_硬件vs软件全景.md`（.html版同目录），含1-6月全景+验证清单

**2026-06-18 架构变更：sectors_status.csv 替代 market_status.yaml**
- 新增 `sectors_status_YYYYMMDD.csv`，76个子赛道，包含stage/wave/信号股/事件地图状态
- 查询命令：`python3 event_map_query.py status --keyword {关键词}` 或 `--stage {阶段}`
- 这个CSV是赛道阶段的**唯一真实来源**（替代原 market_status.yaml 的赛道部分）
- market_status.yaml 不再维护赛道阶段字段（stage/wave等），仅保留用户画像等静态配置
- 与events/mapping/forward同步维护：用户更新事件地图时，同步更新sectors_status中对应赛道的stage和event_map_status
- 已修正的错位：大硅片(整理→主升)、液冷(主升→调整)、AI应用(主升→调整)、精密零部件(启动→主升)、量测设备(启动→主升)
