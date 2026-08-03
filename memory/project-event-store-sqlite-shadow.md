# SQLite产业事件影子库（2026-08-03）

## 目标

把事件、公司、产业角色、材料和催化从Excel/CSV运行时查询迁移到SQLite；影子期CSV仍是
写入主源，SQLite由最新数据确定性重建。Excel仅作为兼容和人工查看产物。

核心关系粒度为：`公司 × 正式事件 × 产业角色`。申万行业只保留为公司背景，不作为事件
选股的主要召回来源。

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
- `benefit_directness / evidence_grade / commercial_stage`结构已预留，但历史mapping多数尚未回填，
  在回填完成前不得进入正式选股评分。

## 切换边界

当前禁止把SQLite作为唯一写入源。满足以下条件后再切换：

1. 连续至少三轮材料更新自动重建成功；
2. AI应用、硬件、非科技各完成一次双读差异解释；
3. 空ID和孤立mapping清理方案确认；
4. 公司关系核心字段回填率达到可用标准；
5. stock-analysis、top-picks和market-outlook消费测试通过。

切换后SQLite为唯一事实源，CSV/Excel仅由数据库导出，不再反向写入。
