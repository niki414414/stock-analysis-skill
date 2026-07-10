---
name: project-backtest-methodology-review-20260710
description: "2026-07-10深度回测公开量化方法论(Minervini/Qullamaggie/Turtle/RRG/Zweig/Choppiness)的完整结论——用backtrader规范回测后，均未验证通过，个股层面\"趋势/震荡切换\"仍是未解决的专项"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6c1bbab6-3cfc-49d3-b430-cb83f4813bc1
---

## 背景

在[[project-tactical-position-regime-v49]]基础上，针对"个股层面创新高(轨迹E)时目标价算不出来"这个具体缺口，用户要求查证公开的高置信度方法论/GitHub代码，找参考设计思路。

## 研究+验证过程（体现方法论，供下次复用）

1. 第一轮WebSearch找到Darvas Box/Minervini SEPA-VCP/Qullamaggie止损离场法/IBD RS Rating——只读了搜索摘要，没读真实代码，被用户指出"偷懒"
2. 第二轮真正用GitHub API按star数搜索比对，发现：**Minervini/RRG/regime-detection这类具体策略在GitHub上从未有过"高赞"**（Minervini实现最高25星，RRG实现最高19星，regime detection专项1-4星），真正高星(1000-20000+)的是通用回测框架(backtrader 22392星/backtesting.py 8658星)和技术指标库(pandas-ta-classic 391星)——策略的"可信度"来自原作者真实交易记录（Minervini审计过的比赛冠军），不是GitHub社区验证，这是两种不同性质的"置信度"，不能混为一谈
3. 打开真实源码验证：`skyte/relative-strength`(131星)的RS公式核实无误（但发现自己之前用简单ROC而非复利累计收益，有实现偏差）；`pandas-ta-classic`的chop.py公式核实和自己实现完全一致（代数等价）
4. 用backtrader（22392星框架）规范回测5只股票(中钨高新/埃夫特/天津普林/沪电股份/信维通信)2年数据，测试两个假设：
   - **连续公式止损止盈**（借鉴RyanJHamby/stock-screener的position_manager.py）：5只全部跑输买入持有，埃夫特实际亏损-17.5%（买入持有本可赚157.3%）
   - **Choppiness Index >55门控**（阻止震荡市开新仓）：5只里3只（中钨高新/天津普林/信维通信）加门控后收益更差、回撤更大
5. 额外发现：Choppiness Index的38/61标准阈值对A股高波动个股不适用（用中钨高新自己的已知强趋势期vs已知反复止损期做校准，14日窗口两者Choppiness值几乎无法区分）；RRG板块轮动滚动回测方向和实际后续表现相反（"Lagging"的电子板块后续20天+21.7%表现最好，"Leading"的煤炭板块后续-11.0%表现最差）——这段时间已确认是震荡市（不是今天早些时候误判的"普涨"），震荡市里假设"相对强弱有持续性"的方法论全部失灵

## 最终结论（诚实，不过度承诺）

**个股层面"该用趋势延续打法还是均值回归打法"这个判断，今天没有做出来，也没有找到能替代的公开方法论**——测试的每一个借鉴方案都在规范回测下失败。这不是没做，是做了但没做成，价值在于排除了几条看似合理实则不work的路。

用户后续进一步指出更根本的认知问题：底仓/战术仓当时被设计成两套独立止损止盈口径，这个设计隐含"个股层面能硬性切换两套打法"的假设——而这个假设正是本轮backtest反复证伪的东西。用户的重新定位（见[[project-tactical-position-regime-v49]]的"自我修正"章节）：底仓/战术仓应该退回成资金分配比例，"该拿多久"应该由一个连续的趋势信心刻度决定，不是硬开关。

## Why
用户要求"避免浪费精力改代码"，坚持要先用真实数据和真正规范的回测工具验证，不接受"听起来合理"就采信。这次连续三轮验证（自己脚本回测→backtrader规范回测→自我认知修正）体现的正是这个要求的价值——如果没有backtrader那轮，很可能已经把两个不work的东西写进框架。

## How to apply
- 下次有专项时间处理"个股趋势/震荡切换+连续信心刻度"时，**从这里的失败记录开始**，不要重新从WebSearch摘要开始，直接跳过已验证不work的：Choppiness Index教科书阈值、position_manager.py连续公式止损、RRG滚动跟踪（至少在震荡市环境下不可靠）
- 需要的是：针对我们自己股票池的本地化参数拟合（不是抄别人为其他市场设计的阈值），且要用backtrader做规范回测，不能用自己写的临时脚本（容易有look-ahead bug，[[project-tactical-position-regime-v49]]里踩过一次）
- 底仓/战术仓的止损止盈公式部分需要简化，退回资金分配比例的定位
