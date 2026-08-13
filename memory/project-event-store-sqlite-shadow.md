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
- `top-picks`与`market-outlook`公司召回默认读取SQLite；`--company-source excel`仅用于回退和双读。
- `event_map_updater.py apply`完成后自动重建影子库，失败只告警、不影响CSV主库。
- `apply`成功后按输入文件指纹记录真实材料更新轮次；重复build或相同快照不计数。

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
python3 repo/scripts/event_db.py migration-status
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

## 消费端迁移验收（2026-08-03）

- 非科技活跃事件双读：SQLite与Excel均召回87家公司，集合一致。
- `top-picks`活跃事件双读：SQLite 48家、Excel 32家；SQLite新增17家均来自正式mapping，
  并排除1家Excel按事件ID跨科技/非科技串线的误匹配（`BAT-2026-002`）。
- 真实Tushare端到端：`top-picks`从SQLite召回26家并完成价格筛选；`market-outlook`完成
  31个申万一级行业雷达，输出`company_source_mode=sqlite`。
- 三轮真实材料更新门槛当时为`0/3`；重复build不计入材料更新轮次。

## 第一轮真实材料更新验收（2026-08-04，0803材料）

- 科技库更新至`csv0804`，非科技库更新至`非科技主线产业事件地图_CSV包_20260804`；
  两侧CSV验证均通过，科技Excel已导出为`0804.xlsx`。
- 同一材料批次分科技/非科技两次apply时，原计数逻辑会把它误计成两轮；已改为按
  `date_short:label`合并，同批次第二次apply更新最终联合快照但不增加轮次，并新增回归测试。
- 首次科技apply由SQLite唯一约束发现`AI-2026-027`重复（旧事件为寒武纪激励，新事件为
  高乐股份合同）；修正为`AI-2026-028`后重建成功。随后已把唯一性检查前移到`apply`创建新版
  CSV之前，并通过“现有ID拦截、空ID留给自动编号”的回归验证；影子库唯一约束继续作为第二道防线。
- 当前影子库：216个正式事件、860条公司—事件关系、595家正式事件关联公司；外键错误0，
  既有37个历史导入异常未新增。
- 新增关系抽查通过：0803材料的8家AI应用公司均连接`AI-SAAS-2026-001`；高乐股份连接
  `AI-2026-028`；药明康德补连`CXO-2026-001`；国电南瑞/中国西电/平高电气/许继电气连接
  `POWER-2026-007`；福莱新材/华依科技连接`ROBOT-2026-002`。
- AI应用双读：旧Excel 48家、SQLite 46家。旧Excel独有7家主要是游戏事件文本回退公司；
  SQLite独有5家为已登记的大模型生态关系。差异来自召回规则而非漏录，正式关系继续以SQLite为准。
- `market-outlook`双读均完成31行业、249条候选、11条六层优先；候选集合有7对差异，均来自
  事件文本回退公司与正式mapping公司之间的选择，状态分布完全一致。
- `top-picks`真实端到端：SQLite召回27家、价格筛选后5家；Excel召回13家、筛选后3家。
  SQLite新增的奥飞数据、行云科技均来自`AI-2026-020`正式关系，不是无来源扩张。
- 误跑的旧`top_picks_screener.py --phase universe`因东方财富概念板块接口断连返回0家公司；
  该脚本不是当前`top-picks`正式消费入口，正式入口`catalyst_left_side_scanner.py`验收通过。
- 真实材料更新门槛现为`1/3`，仍禁止切换SQLite为唯一写入主库。

## 第二轮真实材料更新验收（2026-08-04，0804材料）

- 科技库更新至`csv0805`，CSV验证全部通过，Excel导出为`0805.xlsx`；
  非科技材料无有效增量，保持`20260804`版并再次通过验证。
- 新增中控技术、国能日新两条`AI-SAAS-2026-001`细分映射；深信服、网宿科技已有
  正式关系，未重复造数；金蝶国际仅作港股产业样本，不进入A股公司池。
- 新增7条早期信号、3条纠错和3条来源。FCC光模块限制、日本先进封装设备限制、
  InP缺口幅度和算力租赁个股绑定等未充分证实内容，均未升级为正式事件或批量公司映射。
- 根据Google DeepMind官方产品页修正`ROBOT-2026-011`命名：RT-2是2023年研究模型，
  当前验证锚为Gemini Robotics 1.5、Robotics-ER 1.6和On-Device。
- 影子库重建后为216个正式事件、862条公司—事件关系、597家正式事件关联公司；
  外键错误0，历史37个导入异常未增加。
- SQLite反查中控技术、国能日新均能返回新关系、验证指标、风险和`2026-08-04`
  验证日期；AI应用赛道查询可正常召回两家新公司。
- 真实材料更新门槛现为`2/3`，仍禁止切换SQLite为唯一写入主库。

## 切换边界

当前禁止把SQLite作为唯一写入源。满足以下条件后再切换：

1. 连续至少三轮材料更新自动重建成功；
2. AI应用、硬件、非科技各完成一次双读差异解释；
3. 空ID和孤立mapping清理方案确认；
4. 公司关系的状态、受益层级、依据、来源和验证日期通过增量更新复核；
5. stock-analysis、top-picks和market-outlook消费测试通过。

切换后SQLite为唯一事实源，CSV/Excel仅由数据库导出，不再反向写入。

## 正式切换实现（2026-08-13）

- `source_rows`保存科技/非科技每张源表的完整JSON行，`source_table_schemas`保存真实列顺序；
  因而reviews、weekly及非科技sources历史扩展列均可无损导出，不从规范化查询表猜回原字段。
- `scripts/event_db_cutover.py bootstrap`从当前已验证CSV一次性构建完整库，通过完整性与外键检查后
  原子替换数据库，并写入`metadata.build_mode=sqlite_primary`。
- `scripts/event_db_cutover.py apply --source ... --changes ...`在临时副本内事务应用原有中文变更包，
  导出全部CSV、重新构建规范化查询层并通过校验后，才原子替换正式库；任一步失败都不改正式库。
- `export-csv`与`export-excel`只从SQLite导出兼容产物。旧`event_map_updater.py apply`检测到
  `sqlite_primary`后拒绝CSV写入，防止形成双主。
- 真实库往返验收：SQLite→13张CSV→SQLite后，除动态metadata外所有业务表行数和内容SHA-256一致；
  重复ID、未知字段和非法关系故障均完成整包回滚。
