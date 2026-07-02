# Memory Index

- [股票技能体系项目](stock-skills-project.md) — 两个技能的框架设计、关键规则决策和数据源现状，用于跨会话延续开发
- [数据源优先级](feedback_data_source.md) — Tushare主，akshare/东方财富备选；含单位换算和超时处理要求
- [持仓状态2026-06-18](portfolio-status-20260616.md) — 账户二资产12.2万，2个股(大族激光/长电科技)+2定投ETF，仓位31.67%；6/17-18出了科创ETF/中天/巨石
- [入场六层检查清单 v4.5](trading-framework-checklist.md) — v4.5新增Qullamaggie门控(C轨迹MA10<MA20降WAIT)+Pocket Pivot(主升赛道A→A⚠️)+强势板块漏单提示，2026-06-20写入代码
- [v4.3回测结果2026-06-12](backtest-framework-v43-results.md) — 50只10批回测准确率48.3%，A轨迹漏单仍最大缺口，验证"复苏延续期A轨迹左侧"候选规则有效(52.4%)
- [v4.3回测第二轮2026-06-14](backtest-framework-v43-round2-results.md) — 扩至71只/213信号，v4.4b复测仍有效；新发现v4.5(C/C+应放量1.0-1.5x回踩入场而非缩量)；首次离场模拟：实际可实现收益约为理论值40-45%
- [v4.3回测第三轮2026-06-15](backtest-framework-v43-round3-results.md) — 跨周期(11-12月)复测，旧v4.5(温和放量)反转放弃；v4.4b低胜率高赔率确认纳入
- [v4.5月度回测2026-06-20](backtest-monthly-jan-may-2026.md) — 40只×11月=440信号跨周期验证，Qullamaggie门控(C胜率13%→43%)+PP(科技捕获6错4)→ENTER胜率33%→50%
- [候选：催化深度回踩漏单2026-06](candidate-catalyst-deep-pullback-202606.md) — 圣泉/东材/华正新材PCB涨价催化案例，2日-9~14%急跌缩量后连续涨停，C/C+阈值可能误判，待下轮回测验证
- [科技产业事件地图](industry-events-map.md) — 每周csvMMDD文件夹(events/mapping/forward/corrections等7个csv，2026-06-15起新格式)；event_map_query.py已接入
- [框架回测结论v4.2](backtest-framework-correction-v42.md) — 34只股票136个信号3-6月回测，总准确率53.7%，4月仅32.4%；D/E/A规则需按市场阶段动态调整
- [框架改动方法论](feedback_framework_change_method.md) — 改动前先回顾分层目标→多维对比→奥卡姆剃刀；用户明确要求，不可跳过
- [触发条件追踪清单](reference_trigger_watchlist.md) — ~/stock_trigger_watchlist.md记录待触发入场条件，用户来时核对
- [输出格式：操作决策表](feedback_output_decision_matrix.md) — 分析结论用"可观察条件→操作"映射表，不用方向预测，让用户盯盘可直接对照执行
- [公司表缺失提醒](feedback_company_pool_missing.md) — 分析时公司表查不到的股票，WebSearch补充同时提醒用户更新
- [选股池改造计划](project-stockpool-redesign.md) — 现有~150只池太窄/多过热，待专项时间做方案A(宽库量化筛选)+C(事件地图驱动)改造
