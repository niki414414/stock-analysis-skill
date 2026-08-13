# 催化窗口自动再激活 MVP

## 目的

解决产业事件先发生、正式证据和价格启动后到来的时间错位。系统保留原始事件日，只有“近期高等级证据＋价格确认”同时成立，才写入新的有效交易锚点。

## 自动流程

1. `event_map_query.py window`读取事件最新更新时间和备注；
2. 最近14天出现正式合同、供货协议、订单、业绩、投产、客户认证、正式政策或涨价函时，休眠事件进入待确认池；
3. `catalyst_left_side_scanner.py --reactivation-only`只拉取待确认事件映射公司的行情；
4. 多公司篮子需要广度、5日收益和涨停/量能共同确认；小样本需要明确映射公司确认；
5. 篮子5日中位涨幅超过15%时拒绝激活；
6. 通过后写入`技能数据/catalyst_activation_ledger.json`；窗口模型下次运行使用确认日作为`effective_date`，不覆盖`event_date`。

## 日常运行

```bash
python3 skills/top-picks/references/catalyst_left_side_scanner.py --reactivation-only --json
```

历史回放：

```bash
python3 skills/top-picks/references/catalyst_left_side_scanner.py --reactivation-only --as-of 20260805 --json
```

审计文件写入：

```text
技能数据/market_daily_snapshot/catalyst_activation_audit_YYYYMMDD.json
```

## 磷化铟回放

- 原始事件日：2026-01-01（模糊年度字段的旧解析结果）；
- 证据日：2026-08-03，识别到供货协议、客户认证；
- 2026-08-04：0%公司站上MA20，不确认；
- 2026-08-05：33.3%站上MA20、100%近5日上涨、篮子5日中位+7.76%、近10日5只涨停，确认；
- 激活台账写入2026-08-05；窗口`effective_date`变为2026-08-05并进入红色活跃桶；
- 2026-08-06若此前没有记录，篮子5日中位已+26.59%，会被过热规则拒绝，避免追高。

## 边界

- 不抓取全网新闻；证据必须先进入事件地图更新数据。
- 价格确认由行情接口自动完成，不需要用户回输。
- MVP只监控休眠且近期出现高等级证据的事件，不重复扫描全部历史事件。
- 激活是进入波段候选池的资格，不是自动买入指令，仍需六层分析。
