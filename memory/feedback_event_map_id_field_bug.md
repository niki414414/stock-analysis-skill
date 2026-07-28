---
name: feedback-event-map-id-field-bug
description: 用/update-event-map的apply写入additions时必须显式填ID字段，否则CSV留空且不报错
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3e45492b-6bc8-4f46-82d5-327b5e6657bf
---

写`changes.json`的`additions.mapping`/`signals_early`/`forward`/`sources`时，必须显式包含各表的ID字段（映射ID/signal_id/前瞻ID/来源ID），不能假设`apply`会自动生成——它只是把JSON里没有的列填成空字符串，不会报错也不会自动补ID。

**Why:** 2026-07-14/15同一session内我连续两次犯这个错（第一批3条mapping、第二批2条mapping都漏填映射ID），导致csv0714的mapping表出现空ID行，被`validate`当成"重复ID:[nan]"噪音。事后排查发现这不是孤例——signals_early表里有24行完全空白的幽灵行+9行只有event_id没内容的残行，forward表有6行、sources表有12行只有部分字段没有主键ID，都是同一模式的历史遗留（推测是更早的更新session也犯过同样的错）。这类空ID行不会让任何现有功能崩溃（signals_early当前完全没有被`event_map_query.py`的任何命令消费，纯人工阅读用），但会持续污染`validate`输出、且一旦以后要给signals_early接入评分逻辑（[[catalyst-window-model]]的延伸方向），这些空行会成为真正的阻塞。

**How to apply:** 每次调用`$UPDATER next-ids`拿到ID后，写JSON前对照一遍，确认每条additions记录里对应表的主键列名（用`head -1 <table>.csv`核对，各表列名不同：mapping是"映射ID"，signals_early是"signal_id"，forward是"前瞻ID"，sources是"来源ID"）确实出现在最终JSON里。apply完成后，可以用一次性检查：`python3 -c "import pandas as pd; df=pd.read_csv(f); print(df[df[df.columns[0]].isna()].index.tolist())"` 扫一遍新增文件的第一列，确认没有空主键。
