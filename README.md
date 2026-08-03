# A股投资辅助框架 — 备份仓库

这是一套配合 Claude Code 使用的股票分析框架的完整备份，覆盖四个技能、决策 memory、和赛道数据。

**用途**：容灾/换设备/换 AI 工具时的单一恢复入口。日常工作仍在本地 `~/.claude/skills/` 和
`~/.claude/projects/-Users-niki/memory/` 进行，本仓库是每次做完一轮迭代后同步的镜像，
不是实时同步。

如果你是被临时拉来接手这个项目的 AI（Codex / Gemini / 其他），请先读 [HANDOFF.md](HANDOFF.md)。

## 事件数据库影子模式

产业事件数据当前处于SQLite影子迁移阶段：CSV仍为写入主源，更新后自动重建
`技能数据/event_map_shadow.db`。统一查询入口为：

```bash
python3 scripts/event_db.py company 300454
python3 scripts/event_db.py sector AI应用
python3 scripts/event_db.py event AI-SAAS-2026-001
python3 scripts/event_db.py catalysts --sector AI应用
python3 scripts/event_db.py compare sector AI应用
```

迁移状态与切换边界见`memory/project-event-store-sqlite-shadow.md`。

## 目录结构

```
skills/
  stock-analysis/       被动分析：给股票代码 → 六层检查看板（v4.8框架核心）
  top-picks/             主动扫描：催化驱动左侧扫描，找就绪/预热标的
  update-event-map/       维护赛道事件地图数据库
  update-market-thesis/   维护市场趋势判断底稿
  shared/                 daily_notify.py（微信推送）+ watchlist.yaml（自选池）

memory/
  MEMORY.md               记忆索引，其余 *.md 是具体记忆条目
                          （框架版本决策、回测结果、持仓状态、用户偏好等）

data/
  events-map/tech/         科技主线事件地图最新一版CSV + Excel
  events-map/non-tech/     非科技主线事件地图
  company-pool/            公司池 Excel + 代码映射表
  research-materials/      近期研究材料原文
```

## 恢复到本地使用

```bash
git clone https://github.com/niki414414/stock-analysis-skill.git
cp -r stock-analysis-skill/skills/* ~/.claude/skills/
cp -r stock-analysis-skill/memory/* ~/.claude/projects/-Users-niki/memory/
```

之后需要手动重建 `~/.claude/skills/.env`（Tushare token、Server酱 SendKey ——
这两个密钥没有进本仓库，需要从密码管理器或原始注册渠道找回）。

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
