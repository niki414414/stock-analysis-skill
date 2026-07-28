---
name: quality-compounder
description: |
  沪深300十年质量-价值筛选器 v1.0。
  在沪深300成分股中剔除金融/地产 → PE低于30 → 要求满10年年报数据 →
  用十年归母净利润CAGR+容错稳定性双门槛（CAGR超过银行理财基准利率、
  下滑年数有限、最近一年须是十年内高位）筛出候选池，再对候选池跑
  现金流质量/杠杆/毛利率趋势/周转率趋势/沪深300内行业排名等量化代理指标。

  与 /top-picks、/stock-analysis 的区别：那两个是催化/动量驱动的波段工具
  （3-6个月），这个是买入并持有型的长期质量股筛选，只判断基本面，不判断买点。

  触发场景：用户要求做十年维度的价值/质量股筛选、找长期持有标的、
  "有没有稳定增长的白马"、"筛一下沪深300里的十年牛股"
  示例输入：「跑一下quality-compounder」「筛一下沪深300十年稳增长的公司」
  「有没有适合长期持有的价值股」
---

# Quality Compounder — 沪深300十年质量-价值筛选器 v1.0

**核心逻辑**：
```
沪深300成分股 → 剔除金融/地产 → 剔除上市不足10年 → PE<30
→ 十年归母净利润CAGR+稳定性双门槛 → Stage 1候选池
→ 现金流质量/杠杆/毛利率/周转率趋势/行业内排名 → Stage 2决策表
```

这是全量化的基本面漏斗，**不判断技术面和买点**。候选股请自行结合
`/stock-analysis` 做技术面/催化面验证再决定是否入场。

---

## 工作流

```
STEP 0   读取 config/thresholds.yaml（阈值全部可调，不写死在代码里）
STEP 1   运行 --phase constituents（成分股缓存30天，过期自动重拉）
STEP 2   运行 --phase screen → 输出 Stage 1 候选表
STEP 3   运行 --phase quality → 输出 Stage 2 量化代理指标决策表
STEP 4   引导用户对感兴趣的候选跑 /stock-analysis 做技术面验证
```

---

## STEP 1-3：运行脚本

```bash
python3 "${TZ_CODEX_HOME:-$HOME/Desktop/tz-codex}/repo/skills/quality-compounder/references/quality_compounder_screener.py" --phase screen
python3 "${TZ_CODEX_HOME:-$HOME/Desktop/tz-codex}/repo/skills/quality-compounder/references/quality_compounder_screener.py" --phase quality
```

如依赖缺失：
```bash
pip3 install tushare pandas pyyaml --quiet
```

首次运行或成分股缓存过期超过30天，脚本会自动重拉；如需强制刷新：
```bash
python3 quality_compounder_screener.py --phase constituents --force-rebuild
```

---

## STEP 4：输出格式

见 `references/output-format-template.md`。核心是两张表：

1. **Stage 1 候选表**：代码/名称/行业/PE/CAGR净利润/CAGR营收/下滑年数/2025年排名
2. **Stage 2 决策表**：每只候选逐项列出量化代理指标的观察值+软性标注（现金流质量/杠杆/毛利率趋势/周转率趋势/行业内排名/主营集中度），订单情况固定标注"需人工核实"

**输出规则**（沿用用户"操作决策表"偏好，不写报告式长文）：
- 不做单一总分强制排序，每只候选的标注独立列出，用户自行判断权重
- Stage 2 里任何"数据不可得"的项（如部分公司 fina_mainbz 取不到）如实标注，不编造
- 结尾固定提示："本工具只判断基本面质量，不判断买点；候选股建议逐个跑 /stock-analysis 验证技术面/催化面再决定是否入场"

---

## 阈值说明（config/thresholds.yaml）

| 参数 | 默认值 | 含义 |
|------|--------|------|
| pe_ttm_max | 30 | PE(TTM)上限 |
| lookback_years | 10 | 要求的完整年报年数 |
| benchmark_rate | 0.035 | 银行理财平均收益基准，10年净利润CAGR门槛 |
| max_down_years | 3 | 10年区间内允许的净利润同比下滑年数上限 |
| top_n_rank_required | 3 | 最近一年净利润须排在10年区间内前N高 |
| exclude_industry_keywords | 银行/保险/证券/多元金融/全国地产/商品城 | 行业剔除关键词（Tushare细分行业字段模糊匹配） |
| ocf_to_profit_flag | 80 | 经营现金流/净利润(%)低于此值标注盈利质量预警 |
| debt_to_assets_flag | 60 | 资产负债率(%)高于此值标注杠杆偏高 |

想放宽/收紧筛选，直接改这个yaml，不用改代码。

---

## 已知局限（如实告知用户，不要过度宣称）

- **订单情况不覆盖**：只对制造/建筑/军工类公司有意义，且需要具体公告核实，本工具不做通用量化
- **行业内排名仅限沪深300成分股范围**：不是全市场排名，标签里已注明
- **主营业务集中度依赖 fina_mainbz**：部分公司无分产品/地区披露数据，会如实标注"数据不可得"
- **10年CAGR是首尾两点计算**，中间年份的波动用"下滑年数"补充刻画，但不是完整的年度序列可视化——如需细看某只股票的逐年曲线，读 Stage 1 输出JSON里的 `np_series` 字段

---

## 错误处理

| 场景 | 处理 |
|------|------|
| TUSHARE_TOKEN 未配置 | 提示检查 `$TZ_CODEX_HOME/repo/.env`（默认 `~/Desktop/tz-codex/repo/.env`） |
| index_weight/income_vip 调用失败 | 整体中止并报错（这两步是全局依赖，跳过没有意义） |
| 单只股票 fina_indicator/fina_mainbz 失败 | 该项标注"数据不可得"，不中断其余候选的处理 |
| Stage 1 候选为0 | 提示"当前阈值下无候选通过，可考虑放宽 config/thresholds.yaml" |
