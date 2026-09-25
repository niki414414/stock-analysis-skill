# A股投资辅助框架

这是一套由AI协助使用的A股研究与决策辅助框架，覆盖大盘与板块复盘、三类选股策略、
单股六层分析、产业事件库和连续复盘记录。

本仓库现在是框架代码和规则的当前工作版本，同时也用于版本追踪与恢复。日常到底从哪里
开始，请先看 [当前正式入口清单](ACTIVE_ENTRYPOINTS.md)，不要从历史迁移包或旧快照运行。

日常盘面与板块简报当前使用输出驱动入口：

```bash
python3 skills/market-outlook/scripts/market_brief_v2.py --save
```

它一次生成可读文章与同源证据；旧`market_daily_review.py`只在需要事件、持仓或策略全链路时运行。

## 对话快捷口令

日常可以直接说中文简称，不必记脚本名：

- `箱体`或`V2`：简单盘面与板块复盘；普通“复盘”默认也走这里，不确认涨停。
- `全景`：包含事件、持仓和策略路由的完整复盘。
- `推土机`、`波段`、`底仓`：分别运行对应候选池，三类结果不会混排。
- `六层`：深入检查指定股票、ETF或现有持仓。
- `事件查`、`事件录`、`文章校验`、`账本`、`改框架`、`备份`：启动对应专项。

这里的简称是对话路由，不是Shell别名。完整边界以[当前正式入口清单](ACTIVE_ENTRYPOINTS.md)为准。

如果你是被临时拉来接手这个项目的 AI（Codex / Gemini / 其他），请先读 [HANDOFF.md](HANDOFF.md)。

## 事件数据库当前状态

SQLite已经是产业事件的唯一写入主库；CSV和Excel只作为查看、交换和恢复产物，禁止用旧CSV
反向覆盖SQLite。统一查询入口为：

```bash
python3 scripts/event_db.py company 300454
python3 scripts/event_db.py sector AI应用
python3 scripts/event_db.py event AI-SAAS-2026-001
python3 scripts/event_db.py catalysts --sector AI应用
python3 scripts/event_db.py status
python3 scripts/event_db.py audit
```

正式入库与导出使用`scripts/event_db_cutover.py`；旧`event_map_updater.py apply`已经停用。
迁移历史与切换边界见`memory/project-event-store-sqlite-shadow.md`。
股票系统各层职责边界见`memory/feedback_stock_system_layer_boundary.md`。

## 目录结构

```
skills/
  market-outlook/         箱体V2、全景复盘及三类市场扫描器
  stock-analysis/         指定股票或ETF的六层检查
  top-picks/              波段候选实现；日常统一由策略路由器调用
  quality-compounder/     质量底仓候选实现
  update-event-map/       事件资料维护规则
  update-framework/       正式框架变更约束
  shared/                 事件存储、通知及其他内部共享实现

scripts/
  stock_strategy_router.py     推土机、波段、底仓的内部统一路由
  event_db.py                  SQLite事件库只读查询与检查
  event_db_cutover.py          SQLite主库正式写入与导出
  daily_decision_journal.py    判断、操作与结果账本
  create_local_backup.py       本地恢复包创建与校验

memory/
  MEMORY.md               记忆索引，其余 *.md 是具体记忆条目
                          （框架版本决策、回测结果、持仓状态、用户偏好等）

data/
  events-map/             SQLite主库的查看、交换和恢复导出，禁止反向覆盖主库
  company-pool/           公司池与代码映射
  operational-state/      运行状态、自选池和扫描记录
  research-materials/     研究材料原文
```

## 恢复到本地使用

```bash
git clone https://github.com/niki414414/stock-analysis-skill.git /path/to/tz-codex/repo
cd /path/to/tz-codex/repo
cp .env.example .env
export TZ_CODEX_HOME=/path/to/tz-codex
```

日常直接从仓库正式入口运行，不再把`skills/`复制到特定AI工具目录。需要在线数据时，
根据`.env.example`重建`.env`；密钥不进入Git，需要从密码管理器或原始注册渠道找回。

## 维护与备份约定

采用“一套规则、两份介质”，不再单独维护 `迁移包_latest`、手工抄贝技能或框架快照：

1. **Git/GitHub**：保存可追溯的技能、脚本、配置、memory 和数据版本。
2. **本地离线包**：保存完整 Git bundle 及仓库外的活动数据，用于无网络恢复。

实质性改动包括：新增/修改技能或核心脚本、评分逻辑和数据源变更、事件地图批量
更新、公司池及关键配置变更。完成后固定执行：

```text
审核变更 → 测试/校验 → Git commit → GitHub push
         → 生成离线包 → SHA-256复验 → 报告提交号和备份路径
```

可重新生成的临时输出不强制进 Git；不可重现的研究底稿、回放数据和关键结论必须保留。

## 本地离线迁移包

GitHub用于异地版本备份；本地迁移包用于无网络恢复完整Git历史和活动数据：

```bash
python3 scripts/create_local_backup.py
python3 scripts/create_local_backup.py --verify \
  "$TZ_CODEX_HOME/迁移归档/tz-codex-backup-YYYYMMDD-HHMMSS.zip"
```

迁移包包含当前Git源码快照、完整Git bundle、事件地图、公司池、研究材料、分析记录、
持仓和SHA-256清单。`.env`、Git凭据、日志和缓存明确排除。

脚本只备份已提交的 Git `HEAD`；如工作树不干净会直接拒绝运行，避免产生
“看似最新、实际漏文件”的假备份。历史 ZIP 仅作恢复点，定期清理需要人工确认。
