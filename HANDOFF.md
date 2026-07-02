# 接续指南（给非 Claude 的 AI 工具）

你正在接手一套已经运行了几个月的 A股投资分析框架。原本这套框架是和 Claude Code 共创的，
用户现在换了工具（或换了账户），需要你在没有 Claude 记忆系统的情况下，凭这些文件重建上下文。

## 1. 先读这三个文件建立背景

1. `memory/MEMORY.md` — 记忆索引，一行一条，指向具体记忆文件。**优先读带 `feedback_` 前缀的
   （用户明确纠正过的做法）和最新日期的 `project-` / `portfolio-status-` 文件（当前状态）**。
2. `memory/trading-framework-checklist.md` — 当前分析框架版本（六层检查清单）的完整规则。
3. `memory/stock-skills-project.md` — 框架从 v2.0 到现在的演进历史和关键设计决策，
   了解"为什么是这样"比"现在是这样"更重要，避免重新踩已经踩过的坑。

## 2. 这套框架是做什么的

用户是 A 股投资者，持仓周期 3-6 个月，不能每天盯盘。核心是两个工作流：

- **被动分析**（`skills/stock-analysis/`）：给一个股票代码，跑六层检查
  （市场水位/事件催化/产业地位/趋势结构/买点位置/量能确认），输出买/不买的决策看板。
  `SKILL.md` 是完整流程说明，`references/` 下是各版本的 prompt 模板和数据脚本。
- **主动扫描**（`skills/top-picks/`）：不给代码，从赛道催化事件出发反向找哪些股票还没涨起来。

这两个技能都依赖 `data/events-map/` 里的赛道事件地图（谁在什么时间点有什么催化）
和 `data/company-pool/` 里的公司池（哪只股票属于哪个赛道、什么产业环节）。

## 3. 技术细节

- 数据脚本是纯 Python（`references/stock_data_fetcher.py` 等），不依赖 Claude Code 的任何
  专有机制，直接 `python3` 执行即可。数据源优先级见 `memory/feedback_data_source.md`
  （Tushare 主力，akshare/东方财富备选）。
- `skills/*/SKILL.md` 是 Claude Code 的"技能"格式（有 YAML frontmatter + 触发场景说明），
  你不需要理解这套格式本身，把它当成一份详细的操作说明文档来读、照着执行即可。
- 密钥（Tushare token、Server酱 SendKey）**不在这个仓库里**，原本存在
  `~/.claude/skills/.env`。如果需要跑数据脚本但没有这些密钥，先看能不能用免费数据源
  （akshare/efinance/yfinance）跑通，token 是增强而非必需。

## 4. 开始工作前

直接问用户："框架现在是 v 几？最近一次讨论到哪了？" ——
`memory/MEMORY.md` 里的条目是有日期的，但可能不是最新对话状态，用户口头确认更准。
