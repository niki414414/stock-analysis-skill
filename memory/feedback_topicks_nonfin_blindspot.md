---
name: feedback-topicks-nonfin-blindspot
description: top-picks扫描器和sync_company_pool此前对nonfin库(创新药/有色/储能等)结构性失效，2026-07-16已修复
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3e45492b-6bc8-4f46-82d5-327b5e6657bf
---

`/top-picks`的`catalyst_left_side_scanner.py`此前硬编码只扫tech源（`load_window_scores()`默认`source="tech"`且调用处没传参，`load_company_candidates()`也只读`公司.xlsx`的"科技公司池"sheet），nonfin库（创新药/有色/储能/证券银行等）的事件不管催化评分多高，永远进不了候选池。同一天还发现`event_map_updater.py`的`sync_company_pool()`硬编码读取"代表公司/公司类型"字段名，但nonfin的mapping表列名是"代表公司类型"(无斜杠)——两个表schema不一致，导致nonfin的mapping新增行代表公司字段永远读成空，**静默不报错**，公司池同步对nonfin从写这个函数开始就没生效过。

**Why:** 2026-07-16用户明确要"下半年长线想象空间"的候选（创新药BD、ABF膜这类），top-picks扫出来的7个候选全是近期tech催化，用户凭直觉追问"怎么感觉你找的还是近期的"，一查才发现整个nonfin库从代码层面被漏掉，不是催化不够格——CXO-2026-001/MONKEY-2026-001实测window评分是🔴红桶6.5分，比很多入选的tech🟡黄桶还高。这类"没报错、看起来一切正常"的覆盖面缺口特别难自己发现，只有走完整链路对比预期结果才会露馅。

**How to apply:** 
1. 以后改这两个脚本前，先确认tech/nonfin两条链路都测过，不能只用tech的样例验证就当作通用逻辑生效。
2. nonfin和tech的CSV表结构不是完全对称的（列名、有没有signals_early/corrections表都不同），任何要同时兼容两个source的代码，字段名要么统一改成一致，要么显式做兼容映射，不能假设复用tech那套字段名。
3. 即使这次修复验证通过（候选公司43→53家），最终价格筛选后的候选没变——因为CXO/创新药那批股票现价已经在60日高点附近、近30天涨了20-50%，是真实的"已充分交易"而非bug，验证了[[feedback_calibration_method]]里"叙事要对照YTD/历史位置校准"的同一个道理：催化新鲜不代表价格没涨。
