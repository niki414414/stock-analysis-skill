---
name: project-tushare-pipeline-expansion-20260717
description: stock_data_fetcher.py 2026-07-17新增字段清单：两融/大盘资金流/技术因子交叉验证/十大股东机构占比/业绩质量(非经常性损益占比)，修复了dragon_tiger死bug
metadata: 
  node_type: memory
  type: project
  originSessionId: 3e45492b-6bc8-4f46-82d5-327b5e6657bf
---

2026-07-17对`~/.claude/skills/stock-analysis/references/stock_data_fetcher.py`做了一轮数据管线扩充，起因是分析华工科技(000988)/华天科技(002185)持仓时发现六层框架的常规WebSearch没查到"非经常性损益占比过高"这类关键业绩质量问题，且tushare token权限比预想的丰富很多（系统性测试了margin/top_list/top_inst/moneyflow_mkt_dc/moneyflow_hsgt/pledge_stat/stk_factor/index_dailybasic/cn_cpi/cn_gdp/shibor/us_tycr/opt_daily等20+接口，几乎全部可用，只有stk_auction_o/dc_hot需要更高订阅）。

## 新增字段（已测试通过，无判断逻辑变更，纯数据提取）

**market_environment（大盘层面）**：
- `margin_trend`：两融余额N日变化+连续同向天数，直接回答"去杠杆到哪个阶段"
- `market_moneyflow`：全市场超大单/大单/中单/小单资金流，可判断"机构出货、散户接盘"是否是大盘级别现象

**个股层面（analyze_stock返回值新增/修改）**：
- `fundamentals.extra_item_pct` / `earnings_quality_flag`：非经常性损益占比，来自`fina_indicator`的`extra_item`(非经常性损益)+`profit_dedt`(扣非净利润)字段，Q1季报数据当季即可用，不需要等未来报告
- `fundamentals.forecast.change_reason`：`forecast`接口的公司公告原文，此前只提取了type/涨跌幅区间，原文里常直接写明"该部分收益属于非经常性损益"这类关键信息
- `indicators_verified`：tushare官方`stk_factor`算好的MACD/KDJ/RSI/BOLL/CCI，跟手写`indicators`并列展示做交叉校验，不替换（避免影响`calc_trend_score`等下游消费）
- `holder_concentration`：前十大流通股东机构类型占比(`top10_floatholders`)，季度级、有滞后，作为负面筛查辅助校验项
- `capital_flow.pledge.rest_pledge_pct` / `concentration_flag`：质押是否集中在限售股(控股股东/高管)手里
- `capital_flow.dragon_tiger`：**重写修复**，原实现用akshare逐日循环`stock_lhb_detail_em`+正则解析中文列名，一直返回`has_institution: false`（是死代码，不是"没有机构参与"）。改用tushare`top_list`+`top_inst`（注意这两个接口都只接受单一`trade_date`不支持ts_code+区间查询，仍需按日循环但字段结构化更可靠）。修复后验证：华工/华天科技都查出了真实的机构龙虎榜mixed方向活动，此前所有分析里这一项其实都是假空值。

## 具体验证数字（可回查）
- 华天科技(002185) Q1 2026：非经常性损益0.75亿，扣非净利润仅0.11亿，占比86.9%
- 华工科技(000988) Q1 2026：非经常性损益2.66亿，扣非净利润3.73亿，占比41.6%

## 暂缓事项（按用户优先级排序，未实现）
- 宏观数据(CPI/PPI/M1M2/shibor/美债收益率)：接口可用，不做成每次分析标配，按需查
- 期权隐含波动率：`opt_daily`数据可拿到，但需要自己实现期权定价反推IV，工作量较大，优先级最低
