---
name: project-infrastructure-20260621
description: 2026-06-21基础设施大升级：公司池代码映射(677只)、scanner双维度(量价+催化预警)、/update-event-map技能(替代ChatGPT维护)、框架v4.6(C/D左侧试探+回测)、版本快照+迁移包自动化
metadata: 
  node_type: memory
  type: project
  originSessionId: 7dc148e7-6a14-447a-b2f3-82be15bdfbc9
---

## 2026-06-21 完成的基础设施改动

### 1. 公司.xlsx → 股票代码全链路
- `company_code_map.csv`（5536条tushare映射）+ 别名（改名/ST股）
- `event_map_query.py pool` 命令输出677只A股代码
- stock_universe.yaml的236只全部并入公司.xlsx（123只补入）
- **链路**：事件地图 → 公司.xlsx → pool命令 → 代码 → scanner

### 2. signal_scanner 双源合并 + 催化预警
- `load_pool()` 改为 yaml精选(271条) + 公司.xlsx扩展(465条) = 736条
- 扩展池股票 tier=3（无metadata加分，纯靠量价冒出）
- 新增 `catalyst_watchlist` 输出段（催化预警，event_map forward表驱动）
- 非交易日自动回退最近交易日（`_resolve_trade_date()`，tushare交易日历）
- 输出新增 `pool_source` 字段区分 curated/extended

### 3. /update-event-map 技能
- `event_map_updater.py`：status/apply/export-excel/migrate/validate/next-ids
- 替代ChatGPT维护事件地图，规则固化在代码中不依赖对话记忆
- 支持科技+非科技双源
- STEP 5.5 公司池同步（每次更新自动补入新公司）
- 迁移包含科技+非科技+公司池+框架快照+技能代码

### 4. 框架v4.6 + 版本快照
- C/D + 强催化 → 左侧试探方案（催化≥15 + 量能≥1.1 + 空间≥20%优选）
- 回测验证：440信号C/D子集，PASS+量能+空间组合48%胜率/3.3x赔率
- `framework_snapshot.py`：每次框架实质修改自动快照到 ~/Desktop/tz/framework_versions/
- 迁移包自动包含框架快照，新账号可完整恢复

### 5. prelaunch_scanner 蓄力预警扫描器 (2026-06-22新增)
- `prelaunch_scanner.py`：扫描677只池子，识别处于"蓄力压缩"阶段的标的
- 两档预警：档1·蓄力观察(量缩+幅窄，提前1-3周) + 档2·蓄力临界(+逼近前高+均线粘合，提前1-5天)
- 回测7只案例验证：3/6有涨停的股票提前21-31天预警
- 每只附带同花顺预警建议（alert_price + alert_vol_ratio），用户设到同花顺后盘中实时推送
- /top-picks 升级为v4.0三维度版：催化预警 → 蓄力预警 → 量价异动，signal_scanner和prelaunch_scanner并行运行
- 设计理念：蓄力扫描告诉你"盯哪几只"，同花顺告诉你"今天启动了"，不需要定时cron推送

**Why**: 用户从ChatGPT迁移事件地图维护到Claude Code，同时优化选股流程（扩池+催化预警+蓄力预警）和框架（左侧试探），建立容灾机制。
**How to apply**: 事件地图更新用 /update-event-map，选股用 /top-picks（三维度扫描） + /stock-analysis，框架修改后跑 framework_snapshot.py。
