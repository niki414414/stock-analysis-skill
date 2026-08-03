# SQLite产业事件影子库（2026-08-03）

## 目标

把事件、公司、产业角色、材料和催化从Excel/CSV运行时查询迁移到SQLite；影子期CSV仍是
写入主源，SQLite由最新数据确定性重建。Excel仅作为兼容和人工查看产物。

核心关系粒度为：`公司 × 正式事件 × 产业角色`。申万行业只保留为公司背景，不作为事件
选股的主要召回来源。

事件库只回答“为什么召回这家公司”，不回答“这家公司现在能不能买”。公司业务占比、
商业化兑现、利润贡献、估值和交易状态继续由六层个股分析在分析时核验。

## 当前状态

- 正式影子库：`技能数据/event_map_shadow.db`
- 审计报告：`技能数据/event_db_audit.json`
- 构建/查询CLI：`repo/scripts/event_db.py`
- 共享存储层：`repo/skills/shared/event_store.py`
- 旧查询兼容入口新增：`db-company / sector / event / catalysts / search / db-audit`
- `event_map_updater.py apply`完成后自动重建影子库，失败只告警、不影响CSV主库。

## 常用命令

```bash
python3 repo/scripts/event_db.py build
python3 repo/scripts/event_db.py company 300454
python3 repo/scripts/event_db.py sector AI应用
python3 repo/scripts/event_db.py event AI-SAAS-2026-001
python3 repo/scripts/event_db.py catalysts --sector AI应用
python3 repo/scripts/event_db.py search Agent安全
python3 repo/scripts/event_db.py compare company 金山办公
python3 repo/scripts/event_db.py compare sector AI应用
python3 repo/scripts/event_db.py audit
```

兼容入口：

```bash
python3 repo/skills/stock-analysis/scripts/event_map_query.py db-company 300454
python3 repo/skills/stock-analysis/scripts/event_map_query.py sector AI应用
```

## 影子库构建结果

- 证券主数据字典：5,529个唯一代码（用于名称/代码识别，不代表研究覆盖）
- 已连接正式事件的公司：580家（科技531家、非科技53家，跨池有重叠）
- Excel活跃公司池：991家去重公司；其中部分仅为候选或材料增补，尚未连接正式事件
- 公司关系回填：320条“映射已验证/明确映射”，476条“已登记/角色关联”，
  40条“已登记/主题观察”
- 320条明确映射均保留来源、验证指标、风险和最后验证日期
- 516条已登记关系均保留公司池来源，但验证日期留空，避免把“已登记”伪装成“已验证”
- 科技+非科技正式事件：213
- 公司—事件关系：836
- 行业角色模板：64
- 材料来源：163
- 前瞻事件：115
- 数据库完整性：通过；外键错误：0

## 已知迁移异常

- 科技旧表空ID续行：sources 12、signals 15、forward 6；隔离到`import_anomalies`，未猜测合并。
- 非科技空ID：3条。
- 孤立mapping：`MAP-20260618-006 → MICROLED-OPT-2026-001`，未建立正式外键关系。
- 公司池的原“置信度”仅作为`mapping_confidence`保留，不等同证据等级。
- `benefit_directness / evidence_grade / commercial_stage`已从事件关系表移除，防止把六层的
  公司兑现与交易判断重复前置。

## 切换边界

当前禁止把SQLite作为唯一写入源。满足以下条件后再切换：

1. 连续至少三轮材料更新自动重建成功；
2. AI应用、硬件、非科技各完成一次双读差异解释；
3. 空ID和孤立mapping清理方案确认；
4. 公司关系的状态、受益层级、依据、来源和验证日期通过增量更新复核；
5. stock-analysis、top-picks和market-outlook消费测试通过。

切换后SQLite为唯一事实源，CSV/Excel仅由数据库导出，不再反向写入。
