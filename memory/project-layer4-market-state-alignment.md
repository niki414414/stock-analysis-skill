---
name: project-layer4-market-state-alignment
description: 待做——六层框架Layer4阶段调整表需要重新对齐到market-state-machine.md的四维度判断，避免两套市场判断逻辑长期漂移
metadata: 
  node_type: memory
  type: project
  originSessionId: dff83b5c-04b0-4fbd-bcaa-c6049d3f530d
---

2026-07-21激活了`/market-outlook`（[[project-quality-compounder-skill-20260721]]同一天，见对话记录）后发现：六层框架（`analysis-prompt-template.md`）和大盘状态机（`market-state-machine.md`）现在是两套部分独立演化的市场判断逻辑，已经出现过真实的漂移案例——同一段"找最近已收盘交易日"的代码被复制到4个文件（`stock_data_fetcher.py`/`market_state_fetcher.py`/`daily_snapshot.py`/`quality_compounder_screener.py`），都带同一个"hour<16猜收盘时间"的bug，2026-07-21才一次性修掉。

**当天已经做的低风险修复**：Layer1条件1（指数位置）从WebSearch猜改成直接读`market_state_fetcher.py`的`style_index_comparison.{指数}.above_ma20`（Tushare精确算的MA20位置），消除了一个具体的数据不一致来源。

**还没做、需要专门时间的**：Layer4的轨迹调整表（"破位+板块主升期→升级破位⚠️，69%准确率"这类规则）目前还是键在六层框架自己的"市场阶段"分类（复苏期/主升期/过热期/分化期）上——而这个四阶段分类本身"从未被独立回测验证过"（框架文件里自己写的），market-state-machine.md已经用"背离占比"这个连续型指标替代了它，但只是把它降级为仓位上限的"背景提示"，Layer4的具体调整规则**仍然依赖这个未验证的旧分类**去触发。

**Why：** 两套系统服务的目的不完全一样（Layer0/1是个股入场门槛，market-state-machine是大盘级研判），不适合简单合并成一个，但Layer4依赖的分类本身没有回测支撑，是长期技术债；而且market-state-machine.md会持续加新指标（如2026-07-21新加的`national_team_proxy`护盘ETF代理指标），六层框架不会自动感知，需要人工同步。

**How to apply：** 用户下次说"专门时间处理框架对齐"或类似意思时，直接从这条memory接上，不用重新解释背景。要做的事：把Layer4阶段调整表的触发条件，从"复苏期/主升期/过热期/分化期"重新映射到market-state-machine.md的"市场状态(进攻/震荡/防守/退潮)+主线状态(强化/分歧/修复/退潮/新主线试探)"这两个维度，且要走`/update-framework`的验证纪律（先回测新映射规则是否还能保持原有69%这类已验证的准确率，不能直接偷换概念就当完成）。

**长期纪律（已确认，不需要重新讨论）：** 以后改`market-state-machine.md`或`market_state_fetcher.py`时，应该顺手检查六层框架有没有对应需要同步的地方。
