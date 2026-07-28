---
name: project-quality-compounder-skill-20260721
description: 新建/quality-compounder技能——沪深300十年质量-价值筛选器，与现有催化/动量工具互补
metadata: 
  node_type: memory
  type: project
  originSessionId: dff83b5c-04b0-4fbd-bcaa-c6049d3f530d
---

2026-07-21新建独立技能 `/quality-compounder`（`~/.claude/skills/quality-compounder/`），
定位是买入并持有型的长期质量股筛选，与 [[stock-skills-project]] 里已有的
`/top-picks`（催化左侧扫描）、`/stock-analysis`（六层技术+催化分析）风格完全不同——
后两者服务3-6个月波段交易，这个只看十年维度基本面，不判断买点。

**核心漏斗**：沪深300 → 剔除金融/地产（关键词匹配）→ 剔除上市不足10年 → PE<30
→ 十年归母净利润CAGR>银行理财基准(3.5%) + 下滑年数≤3 + 最近一年须是十年内前3高
→ Stage 1候选池 → 现金流质量/杠杆/毛利率趋势/周转率趋势/沪深300内行业排名/主营集中度
→ Stage 2量化代理指标决策表（订单情况明确标注"不覆盖，需人工核实"）。

**关键实现决策**（用户已通过AskUserQuestion确认）：
- 增长判定用CAGR+容错稳定性，不用严格逐年增长（A股几乎没公司能扛住2018贸易战+2020疫情两次冲击不掉一年）
- 银行类利率基准取"银行理财平均收益~3.5%"，写在 config/thresholds.yaml，可调
- 第二层财务分析只做纯量化代理指标，订单/精确行业地位不强行量化

**技术实现要点**：
- 数据源：Tushare Pro，`index_weight`(300成分股)+`stock_basic`(行业/上市日期)+`daily_basic`(PE，注意ts_code不支持逗号批量查询，需整日全市场拉取后本地过滤)+`income_vip`(10个财年各一次全市场批量调用，比逐只income()快得多)+`fina_indicator`+`fina_mainbz`(best-effort，部分公司无分产品数据)
- 全流程（screen+quality两阶段）实测约16秒跑完，300只成分股→116只硬过滤后→61只Stage1候选
- 首次实测候选池包含贵州茅台/美的/格力/伊利等公认长期质量股，验证筛选逻辑合理

**已知局限**（写在SKILL.md里如实告知）：行业内排名仅限沪深300内非全市场；主营集中度依赖fina_mainbz部分公司取不到；10年CAGR是首尾两点计算，中间波动靠下滑年数指标补充。

**待做**：暂无，v1.0已跑通验证。如果用户后续觉得阈值需要调整（如top_n_rank_required/max_down_years过严或过松），直接改 `config/thresholds.yaml`，不用改代码。
