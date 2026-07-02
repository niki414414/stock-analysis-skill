---
name: feedback_data_source
description: 股票数据源优先级——Tushare 主，akshare/东方财富备选
metadata: 
  node_type: memory
  type: feedback
  originSessionId: d9a8af0b-6643-43d2-bef9-f6186f78011f
---

选股脚本的数据源优先级必须是：**Tushare（主）→ akshare（备1）→ efinance/东方财富（备2）**

**Why:** 用户在2026-05-26明确提出，akshare和东方财富API不稳定，经常断连；Tushare有token且数据可靠，应作为主数据源。之前的代码用akshare作主源，导致当天API挂了时整个流程卡死10分钟+。

**How to apply:** 
- 任何涉及A股行情数据的脚本（screener、data_fetcher等）都应先尝试Tushare
- Tushare total_mv单位是万元，screener内部按元计算时需×1e4转换
- efinance历史降级要加连通性预检+multiprocessing超时（spawn模式），防止卡死
