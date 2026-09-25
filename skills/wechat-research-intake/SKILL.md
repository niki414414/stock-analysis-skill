---
name: wechat-research-intake
description: >
  扫描本地微信研究文章收件箱，识别未处理或内容已变化的文章，并按段落分流到事件数据库入库
  与市场/产业趋势学习整理。用于“整理文章”“处理精选文章”或批量处理wechat研究文件夹。
metadata:
  version: "1.1"
  last_updated: "2026-09-11"
---

# 微信研究文章收件箱

## 固定位置

- 原始收件箱：`/Users/niki/wechat-research/精选文章`
- 去重台账：`技能数据/运行记录/wechat-research-intake/ledger.jsonl`
- 单篇学习记录：`分析记录/06_文章学习与趋势研究/`
- 活跃趋势底稿：`repo/skills/stock-analysis/references/market_thesis.md`
- 事实事件主库：`技能数据/event_map_shadow.db`

原始收件箱只读。不得移动、重命名、删除或改写文章，也不要求用户预先分类。

## 扫描

先运行：

```bash
python3 repo/skills/wechat-research-intake/scripts/inbox_scan.py scan --json
```

默认只处理 `new`、`changed` 和此前为 `partial`/`deferred` 的文件；内容哈希未变化且状态为
`complete` 或 `skip` 的文件不重复处理。

## 按段落分流

不要把整篇文章强制二选一：

- **事件链（update-event-map）**：政策和公告、订单、价格、产能、产品发布、客户验证、未来时间窗、
  传闻纠错、公司与产业环节关系。
- **学习链（update-market-thesis）**：盘面和行情解释、宏观与跨资产传导、板块轮动、产业趋势、
  周期判断、研究方法及需要长期跟踪的因果框架。
- **两边都走**：文章同时含事实素材和趋势判断。这是每日资讯整理文章的默认常见情况。
- **跳过**：免责声明、作者仓位盈亏、无依据口号、纯荐股、重复转载且没有新增内容。

一段文字同时含事实与推断时，拆成两个包并保留共同来源，不能让作者推断继承事实的证据等级。

## 处理顺序

1. 读取整篇原文和frontmatter，识别发布日期、文章ID、原始链接和主题段落。
2. 先查询事件档案与趋势底稿，判断是新增、增量、冲突还是重复。
3. 将事件包交给 `update-event-map`，按其证据分级、去重、变更清单和SQLite审计流程处理。用户已授权
   “保守分级直接入库、入库后核对”时，不再逐批等待事前确认：事实、早期信号、市场情绪与传播
   线索分别写入合适层级，完成后在对话窗口展示实际写入结果供用户纠错。
4. 将学习包交给 `update-market-thesis`，保存单篇分析；只有可持续结论才更新活跃底稿。
5. 汇总文章级结果。只有两条应走的链都成功时才记为 `complete`。证据不足但已进入低置信度信号层
   不再记为 `deferred`；只有无法归类、写入失败或确需外部权限时才记为 `partial` 或 `deferred`。
6. 成功后记录台账，例如：

```bash
python3 repo/skills/wechat-research-intake/scripts/inbox_scan.py record \
  --file "/Users/niki/wechat-research/精选文章/文章.md" \
  --route hybrid --status complete \
  --event-ids "EVENT-1,EVENT-2" \
  --analysis-ref "分析记录/06_文章学习与趋势研究/2026-09-10_文章.md"
```

台账是追加式审计记录，不作为事件或研究结论本身的存储。

## 完成摘要

每批只报告：扫描数量；完成、部分、延期、跳过数量；新增/更新事件ID；生成的学习记录；趋势底稿
更新模块；最重要的待核验项。不得把处理成功说成文章观点已经被市场或公司事实验证。

用户需要确认分流或入库内容时，默认直接在对话窗口展示简洁清单，不为一次性确认单独生成
Markdown、Word或PDF。只有内容需要延期核验、审计留档、后续反复查询，或用户明确要求文件时，
才生成持久化文档；后台台账和正式数据库记录不受此规则影响。
