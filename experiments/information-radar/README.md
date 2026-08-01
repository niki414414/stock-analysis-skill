# 信息漏检雷达 POC

这是一个不接入正式事件库、不发送通知的历史回放实验，用于验证：

1. 上游价格/成本变化能否通过传导规则映射到 A 股行业；
2. 行业显著异动能否触发“事件库解释缺口”；
3. 漏项能否进入候选信号收件箱，等待人工核验。

当前回放日为 2026-07-31。输入行情已固化在 `replay_20260731.json`，因此测试不依赖网络。

运行：

```bash
python3 repo/experiments/information-radar/information_radar_poc.py \
  --replay repo/experiments/information-radar/replay_20260731.json \
  --rules repo/experiments/information-radar/transmission_rules.json \
  --event-root 技能数据/科技产业事件/csv0730

python3 -m unittest discover -s repo/experiments/information-radar -p 'test_*.py'
```

判定逻辑：

- 行业单日涨幅不低于 3%；
- 成交量相对前 5 日均量不低于 1.2 倍；
- 行业位居申万一级行业前 20%；
- 候选信号的传导目标命中该行业；
- 最近事件库没有包含该信号解释关键词的事件。

满足以上条件时输出 `coverage_gap=true`。POC 只证明“值得反查”，不证明该消息是上涨的唯一原因。

