---
name: project-market-outlook-migration-20260728
description: "从迁移包恢复 market-outlook，并修正两融和全市场资金流接口口径"
metadata:
  node_type: memory
  type: project
---

## 背景

Claude 账户导出后，`market-outlook`、`market-state-machine.md` 和
`market_state_fetcher.py` 只存在于 `技能数据/迁移包_latest/07_技能代码/`，
没有进入 Git 仓库。`stock_data_fetcher.py` 留有两个未提交辅助函数，说明这项工作
在同步仓库前中断。

## 恢复内容

- 新增 `skills/market-outlook/`，保留原四状态市场判断框架。
- 将 `market_state_fetcher.py` 接入 `skills/stock-analysis/scripts/`。
- 所有路径改用 `TZ_CODEX_HOME`，默认指向 `~/Desktop/tz-codex`。

## 数据口径修正

1. 原 `fetch_margin_trend()` 调用 `margin_detail`，却读取不存在的
   `op_date/balance` 字段。现改用 `margin` 汇总接口，按 `trade_date` 合并沪深
   交易所的 `rzye` 后再计算趋势。
2. 原 `fetch_market_moneyflow_dc()` 把 `moneyflow_hsgt` 的南北向资金误写成
   “机构/散户资金”，并错误按元换算。现改用 `moneyflow` 的L2订单规模数据，
   汇总大单/特大单与小单净额；金额从万元除以10000换算为亿元。
3. 输出明确标注：订单规模只能作为资金类型代理，不能识别真实机构或散户身份。

## 验证

- 对两融数据构造两个交易所、10个交易日的模拟记录，验证按交易日汇总和趋势计算。
- 对资金流构造多个交易日的模拟记录，验证只取最新日期、方向分类和单位换算。
- 两项单元测试通过；全部 Python 文件语法检查通过；`market-outlook` 通过 Codex
  技能格式校验。

## 边界

- 本次修复数据语义和工程接入，不改变大盘四状态的历史回测规则。

## 在线验收（2026-07-28）

- 使用原环境中仍有效的旧 Tushare 令牌临时注入进程完成验收；令牌值未输出，
  也未写入新工作区 `.env`，因为该令牌曾存在于代码中，应先轮换。
- 核心模式 `--json --skip-baskets` 全字段成功返回：市场广度、两融趋势、
  两融250日滚动分位、大单/小单订单代理、风格指数、10日背离占比、ETF份额、
  两市成交额和关键指数技术位。
- 两融最新数据日期为2026-07-27，成功按沪深交易所汇总。
- 完整模式成功计算68个有效细分赛道篮子，覆盖213只不重复股票；事件库共76个
  子赛道，未生成篮子的赛道是因为 `signal_stock_code` 为空，不是API失败。
- 宏观避险层成功计算贵金属/黄金篮子6只股票。

## 后续安全事项

- 在 Tushare 后台轮换旧令牌。
- 将新令牌写入 `$TZ_CODEX_HOME/repo/.env` 的 `TUSHARE_TOKEN`，该文件已被Git忽略。
