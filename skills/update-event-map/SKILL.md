---
name: update-event-map
description: |
  科技/非科技产业事件地图维护技能 v1.0。
  接收用户提供的研究材料（PDF/文本/截图）或自动WebSearch扫描行业动态，
  完成：加载最新数据库 → 分析材料/搜索 → 分类入库 → 生成新版本CSV+Excel。

  支持操作：
  - 从研究材料批量提取事件并录入
  - WebSearch主动扫描行业动态并更新
  - 手动新增/更新事件、信号、前瞻、修正
  - 导出Excel主库供查看
  - 生成迁移包（容灾/换号恢复）

  触发场景：用户要求更新事件地图、提供研究材料要求录入、要求导出Excel/迁移包、
  要求搜索行业动态并更新数据库
  示例输入：「更新事件地图」「把这份研报录入事件地图」「导出最新Excel」「生成迁移包」
  「搜索AI算力最新动态并更新」「这个消息应该进events还是signals」
allowed-tools:
  - Read
  - Write
  - Bash
  - WebSearch
metadata:
  trigger: 用户要求维护/更新产业事件地图时触发
  version: "1.0"
  last_updated: "2026-06-21"
---

# 产业事件地图维护技能 v1.0

## 核心工具

```
UPDATER=~/.claude/skills/update-event-map/scripts/event_map_updater.py
QUERY=~/.claude/skills/stock-analysis/scripts/event_map_query.py
```

---

## STEP 0: 加载当前状态

每次触发必须先运行：

```bash
python3 $UPDATER status
python3 $UPDATER status --source nonfin
```

向用户报告：当前版本、各表行数、上次更新日期。

---

## STEP 1: 判断模式

根据用户输入判断4种模式：

| 模式 | 触发条件 | 后续步骤 |
|---|---|---|
| 材料录入 | 用户提供了文件（PDF/文本/截图/研报） | → STEP 2A |
| 主动扫描 | "更新事件地图"、"搜索XX最新动态" | → STEP 2B |
| 手动录入 | "把XX加到events"、"这条进corrections" | → STEP 4（跳过分析） |
| 导出 | "导出Excel"、"生成迁移包" | → STEP 6/7（直接执行） |

---

## STEP 2A: 材料分析（材料录入模式）

1. 读取用户提供的文件（Read工具读取PDF/文本/图片）
2. 提取所有事实性声明、数据点、事件、传闻、预测
3. 对每条信息标注来源可靠性：
   - A级：官方公告/财报/监管文件
   - B级：主流财经媒体/行业协会
   - C级：券商研报/行业调研
   - D级：传闻/小作文/未核验
4. 加载现有数据库对比：

```bash
python3 $QUERY events --sector {相关赛道}
python3 $QUERY forward --sector {相关赛道}
python3 $QUERY corrections
```

5. 可选WebSearch验证关键声明：

```
WebSearch("{事件关键词} {当前年月}")
```

---

## STEP 2B: 主动扫描（WebSearch模式）

按赛道依次搜索（每次最多选3-5个重点赛道）：

```
WebSearch("{赛道} 最新产业动态 订单 催化 {当前年月}")
```

科技重点赛道清单：
- AI算力、半导体、存储、先进封装
- 光通信/CPO/NPO、PCB/CCL、被动元件/MLCC
- 机器人/Physical AI、SiC/GaN
- AI应用/SaaS/Agent、端侧AI
- 商业航天/6G、液冷/散热

非科技重点赛道：
- 有色金属、储能、创新药、银行/证券

对搜索结果与现有数据库做增量比对。

---

## STEP 3: 分类决策

对每条提取的信息，按以下决策树分类：

```
Q1: 有官方来源（公告/财报/权威媒体）？
  YES → Q2
  NO  → signals_early（confidence_level=低/中）

Q2: 已经发生或正在发生？
  YES → events（新事件）或 更新现有事件状态
  NO  → Q3

Q3: 是对未来的预测/催化窗口？
  YES → forward
  NO  → Q4

Q4: 指出现有判断需要修正/降权？
  YES → corrections
  NO  → signals_early（作为产业线索留存）

附加规则：
- 涉及具体订单量/目标市值/上市时间 → signals_early，不进events
- 技术路线争议/数据不一致 → corrections
- 高拥挤方向 → corrections（提高验证门槛）
- 产业链新环节/新公司类型 → mapping
- 信息来源 → 同步写入sources
```

---

## STEP 4: Delta对比 + 用户确认

将拟录入的内容整理成表格向用户展示：

```
拟录入变更清单（{日期}）

NEW    events    AI-2026-011  {事件名称}  来源: {来源}
NEW    signals   SIG-{date}-001  {信号内容}  confidence: 中
UPDATE events    MEM-2026-005  当前状态: 进行中→已兑现
SKIP   (已存在) PCB-2026-002  PCB涨价传导  

确认录入？（Y/调整/取消）
```

生成ID时调用：
```bash
python3 $UPDATER next-ids --table events --sector {赛道} --count {N}
python3 $UPDATER next-ids --table signals_early --count {N}
```

---

## STEP 5: 写入

用户确认后：

1. 构造changes JSON，写入/tmp/event_map_changes.json：

```json
{
  "source": "tech",
  "date": "YYYYMMDD",
  "label": "描述标签",
  "additions": {
    "events": [{"事件ID": "...", "一级赛道": "...", ...}],
    "signals_early": [...],
    "forward": [...],
    "corrections": [...],
    "mapping": [...],
    "sources": [...]
  },
  "updates": {
    "events": [{"id_value": "MEM-2026-005", "fields": {"当前状态": "已兑现"}}]
  }
}
```

2. 执行写入（SQLite主库启用后唯一合法入口）：

```bash
python3 repo/scripts/event_db_cutover.py apply --source tech --changes /tmp/event_map_changes.json
```

旧`event_map_updater.py apply`只用于SQLite切换前的历史流程；检测到
`metadata.build_mode=sqlite_primary`后会主动拒绝CSV写入，禁止形成双主。

3. 验证：

```bash
python3 $UPDATER validate
```

---

## STEP 5.5: 公司池同步（2026-07-10起自动执行，无需手动操作）

`apply`命令内部会自动调用`sync_company_pool()`：扫描本次新增的mapping行的"代表公司/公司类型"字段，查company_code_map.csv确认A股代码，不在公司.xlsx里的自动按(一级赛道, 二级环节, 三级环节/定位, 关联事件ID)写入对应的科技/非科技公司池sheet。apply运行完会打印同步结果（新增几家/跳过几家无代码的）。

**这一步不再需要你（Claude）记得手动做**——之前这里是纯prompt指令，容易漏做；现在写死在代码里，只要走了STEP 5的`apply`就一定会跑。

若某次material里提到的公司改动没走`apply`（比如只是手动改了mapping.csv），可以单独补跑：
```bash
python3 $UPDATER sync-pool --source tech --changes /tmp/event_map_changes.json
```

历史mapping或同步机制上线前的数据需要全量对账时，使用：
```bash
python3 $UPDATER reconcile-pool --source tech --sector {一级赛道}
```

`reconcile-pool`按“公司×事件×产业角色”回填，不再按公司名粗暴去重；同一家公司处于不同
产业环节时会保留多行角色。代表公司字段即使写成“定位样本：A、B、C”等说明文字，也会
优先通过`company_code_map.csv`做实体识别。每次新增一个既有大赛道的mapping后，建议运行
该赛道对账，随后再次运行确认`+0条公司角色`，完成幂等校验。

**关键链路**：事件地图更新(apply) → 公司池自动同步(sync_company_pool) → scanner/window模型自动覆盖新公司，无需额外操作

---

## STEP 6: 导出Excel + 统一备份（每次实质性更新后执行）

```bash
# SQLite直接导出兼容CSV和Excel；导出物只读，不得反向编辑入库
python3 repo/scripts/event_db_cutover.py export-csv \
  --date {YYYYMMDD} --output-root "$TZ_CODEX_HOME/技能数据/事件地图导出/{YYYYMMDD}_sqlite_primary"
python3 repo/scripts/event_db_cutover.py export-excel --source tech \
  --output "$TZ_CODEX_HOME/技能数据/事件地图导出/{YYYYMMDD}_sqlite_primary/{MMDD}_sqlite_primary.xlsx"

# 数据与代码校验通过后，在 repo 中提交并推送。
# 然后生成统一离线包（Git bundle + 活动数据 + SHA-256）。
python3 "$TZ_CODEX_HOME/repo/scripts/create_local_backup.py"
```

Excel包含10个sheet（00_使用说明 到 10_早期信号追踪），冻结首行，自适应列宽。
离线包写入 `$TZ_CODEX_HOME/迁移归档/`，保留时间戳恢复点并在生成后自动复验。

---

## STEP 7: 版本与恢复边界

- Git commit/tag 是唯一框架版本记录，不再另建框架快照。
- GitHub 是异地备份；`create_local_backup.py` 生成的 ZIP 是离线容灾备份。
- 备份脚本遇到未提交修改会拒绝运行；必须先审核、验证和提交。
- 新设备/新工具优先从 GitHub clone；无网络时从 ZIP 内 Git bundle clone，再恢复活动数据。

---

## STEP 8: 报告

向用户输出更新摘要：

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  事件地图更新完成 — {YYYY-MM-DD}
  版本: csv{MMDD}_{label}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  变更统计:
  events:        +{N}新增  ~{M}更新
  signals_early: +{N}新增
  forward:       +{N}新增
  corrections:   +{N}新增
  sources:       +{N}新增

  验证: ✓ 通过 / ⚠️ {N}个问题

  Excel已导出: {path}

  下一步: /stock-analysis 或 /top-picks 将自动使用新版本
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 错误处理

| 场景 | 处理 |
|---|---|
| CSV目录不存在 | 提示用户检查路径或从迁移包恢复 |
| changes JSON字段名不匹配 | 显示正确字段名，要求修正 |
| 事件ID重复 | 警告并要求确认是否覆盖 |
| 赛道名不在已知列表 | 显示已知赛道列表供选择 |
| WebSearch无结果 | 标注"无搜索结果"，基于已有材料继续 |
| validate有问题 | 列出问题，让用户决定是否修正 |

---

## 禁止事项

- 不输出买入/卖出/持有建议
- 不输出目标价/目标市值
- 公司名称仅用于产业链定位
- 传闻订单不直接写成事实事件
- 不单独覆盖旧版本（始终创建新csvMMDD）
