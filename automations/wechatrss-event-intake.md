# WeChatRSS 订阅文章定时入库

建议任务名称：WeChatRSS 每日订阅入库。
运行时间：每天 08:00，时区 Asia/Shanghai（北京时间）。
运行位置：ChatGPT 桌面应用的本地项目，使用主工作目录。

在本机项目 `/Users/niki/Desktop/tz-codex` 的主工作目录运行。每次执行：

1. 使用已连接的 `wechatrss` MCP 列出订阅文章，按 `ingested` 顺序翻页，检查新文章、迟到的旧文章和此前正文为 `pending` 的文章。不要用旧 `we-mp-rss` 导出目录判断更新。
2. 按 `article_id` 和原始 URL 对照 `技能数据/运行记录/wechat-research-intake/ledger.jsonl`、现有全文副本及事件库去重。只有新、正文变化或上次部分完成的文章需要重做。
3. 对每篇候选文章调用 `get_article` 读取完整正文。正文仍为 `pending` 或 `unavailable` 时保留待处理，记录在本次报告中，不能凭标题、摘要或目录信息入库，也不能记为 `complete`。
4. 完整按 `repo/skills/wechat-research-intake/SKILL.md` 处理全文，按段落分流到 `update-event-map` 和 `update-market-thesis`。将带来源字段的全文副本保存到 `研究材料/wechatrss-inbox/`。事实、预测、传闻与市场情绪分级；只有原始公告等充分证据才写入正式事件，其他可定位线索写入低置信度信号。
5. 入库前执行 `python3 repo/scripts/event_db.py status` 和 `python3 repo/scripts/event_db.py audit`，确认 `sqlite_primary`、完整性正常、外键错误为零，并记录 `company_event_links` 数量。还必须确认 `技能数据/公司.xlsx` 存在。缺失或检查失败时停止写库并报告，不要重建主库。
6. 使用 `event_db_cutover.py apply` 原子写入。写入后再做主库检查；若无明确删除操作，`company_event_links` 不得低于写入前。核对新增或更新的事件、信号、来源与生命周期记录确实存在。只有两条应走的处理链都完成后才将文章记为 `complete`。
7. 只在存在可持续新结论时更新 `market_thesis.md`；普通单日观点留在逐篇学习记录。每次给出简短运行报告：发现/完成/部分/待正文数量，新增与更新 ID，最重要的待核验项，以及主库审计结果。没有新文章时仅报告“本次无新增”，不要重复写入或生成空文件。

本任务已获用户授权按保守证据分级直接入库、完成后报告。不要逐篇等待确认；遇到必须扩大权限、主库校验失败或来源不可用时停止受影响步骤并说明原因。
