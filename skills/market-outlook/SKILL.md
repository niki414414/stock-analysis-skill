---
name: market-outlook
description: |
  大盘/主线趋势判断 v1.0 —— 激活既有的"大盘状态机"框架
  (stock-analysis/references/market-state-machine.md，2026-07-11建、
  849个交易日真实回测支撑，但此前从未被任何技能引用)。

  与六层框架(/stock-analysis)、催化左侧扫描(/top-picks)的区别：那两个管个股，
  这个管大盘——回答"这周该进攻还是防守""主线现在强化还是退潮""风格切没切换"
  这类市场级问题，不下沉到具体买卖点。

  触发场景：用户问大盘走势、市场环境、该进攻还是防守、主线状态、风格切换
  示例输入：「下周大盘怎么看」「这周该进攻还是防守」「主线现在什么状态」
  「市场风格切没切换」「大盘趋势判断」
---

# Market Outlook — 大盘状态机 v1.0

**核心判断框架见** `references/market-state-machine.md`
（四状态判断体系 + 情景→动作翻译表 + "下周作战地图"固定模板，本SKILL.md只负责
数据拉取和工作流编排，判断规则和输出格式**一律以该文件为准**，不在这里重复定义）。

---

## 工作流

```
STEP 0   跑 market_state_fetcher.py --json，拿到市场广度/北向/两融/资金流/
         风格指数对比/背离占比全部原始数据
STEP 1   跑板块轮动雷达 + 事件地图window/corrections，拿到主线状态判断依据
STEP 2   Read market-state-machine.md，对照第一节四条判断规则逐条组装结论
STEP 3   按market-state-machine.md第三、四节的固定模板输出
```

---

## STEP 0：拉取市场级原始数据

```bash
python3 "$TZ_CODEX_HOME/repo/skills/stock-analysis/scripts/market_state_fetcher.py" --json
```

输出字段说明（对应market-state-machine.md第一节的判断依据）：

| 字段 | 用于 |
|---|---|
| `breadth_today.regime_label` | 市场状态判断的"关键陷阱"交叉验证（普涨/抱团集中/权重股压制/普跌）|
| `breadth_today.index_pct_chg/advance_pct/limit_up/limit_down` | 市场状态四档（进攻/震荡/防守/退潮）判定证据 |
| `north_bound` | 市场状态+风险偏好判定证据 |
| `margin_trend` | 风险偏好判定证据（去杠杆/加杠杆节奏）|
| `market_moneyflow` | 风险偏好辅助证据（大单/特大单vs小单订单规模代理，不等同于可识别的机构/散户身份）|
| `style_index_comparison` | 风格状态判断（科技占优/非科技占优/高低切换/红利银行防守/存量跷跷板/全市场增量）|
| `divergence_ratio` | 替代原六层Layer0"分化期"判断——背离占比0-10%/10-30%对应不同的10日后前瞻收益档位（见market-state-machine.md"背离占比"小节的回测数据）|
| `national_team_proxy` | 护盘/托底资金代理指标（2026-07-21新增）：宽基/科技ETF份额变化，用单日最大跳升幅度+日期判断护盘资金是否真实入场、偏向哪个方向。**局限**：ETF份额申购是匿名一级市场行为，不能100%确认买方是"国家队"，只能作为间接代理——申购幅度大且时点恰好卡在护盘公告后，才能算方向性印证，不能单独作为决策依据。**另外每个ETF的`latest_date`可能不是同一天**——上交所(.SH)挂牌ETF的`fund_share`数据比深交所(.SZ)更新慢约1天，报告里必须显式标注每个ETF的数据日期，不能默认所有字段都是"今天" |
| `total_turnover` | 两市(沪深合计)成交额趋势——市场状态判断"两市成交额较5日均值放大/萎缩"的直接依据。**2026-07-21教训**：早期人工分析时只查过上证单市场数据，会明显低估真实放量程度（上证仅占两市合计约一半），必须用这个字段而不是手动只查一个交易所 |
| `technical_levels` | 关键指数（上证/沪深300/创业板/科创50/中证1000）的MA5/10/20/60 + 压力位/支撑位（20/60/120日高低点，含"待突破/已突破/待考验/已跌破"状态标注）+ 本轮涨跌起点（近期20日/更大级别60日两个窗口分别给出"下跌起点(前高)"和"反弹起点(前低)"）。**注意**：压力支撑位不排除最近几个交易日（跟个股的high_baseline逻辑刻意不同）——大盘场景下最近的急跌低点/反弹高点往往就是当前最关键的参考位，排除反而会漏掉最相关的那个点。**两个窗口分开报告不合并**：2026-07-21实测科创50案例，60日窗口找到的是1-4月更深的底部，掩盖了7月这次相对温和的小回调——两个级别的起点都有参考价值，合并成一个会互相掩盖 |
| `subsector_basket_momentum` | **Layer2细分题材动量（2026-07-22新增）**：sectors_status.csv的76个子赛道`signal_stock_code`篮子当日/5日/20日等权涨跌幅，用`pro.daily()`逐只现算（不依赖`sw_daily`/`ths_daily`这类指数接口——2026-07-22实测确认这两个都有约1个交易日的发布延迟，同一申万代码换`index_daily()`才有当天数据）。**存在的意义**：申万31个一级行业（Layer1/rotation_check）颗粒度太粗——2026-07-22实测案例，"有色金属"把贵金属和工业金属混在一起，当天贵金属涨6-8%被稀释成整个行业只有+2.9%；"算力租赁/Token工厂"这种主题申万里根本没有独立分类。子赛道篮子正好卡在这两者之间的颗粒度，且篮子本身就是mapping表自动同步出来的（不需要额外维护）。**用`--skip-baskets`可以跳过**（涉及上百只个股逐一拉取，比其余字段慢） |
| `macro_hedge_baskets` | **Layer3宏观避险篮子（2026-07-22新增）**：事件地图/公司池覆盖范围之外、手工维护的小清单，目前只有贵金属/黄金（见`market_state_fetcher.py`的`MACRO_HEDGE_BASKETS`）。存在原因：贵金属这类避险资金流向是宏观/地缘驱动，不是科技产业催化驱动，事件地图从建库起就没有覆盖，也不适合硬凑进mapping表——2026-07-22当天贵金属篮子涨+6.7%(今日)/+19%(20日)，是当天报告里最强的独立信号，但如果没有这层会完全看不到 |

若依赖缺失：
```bash
pip3 install tushare pandas --quiet
```

---

## STEP 1：主线状态判断依据

```bash
# 板块轮动雷达（15个申万一级行业20日/5日涨幅，复用top-picks已有代码）
python3 -c "
import sys, os
root = os.path.abspath(os.path.expanduser(os.environ.get('TZ_CODEX_HOME', '~/Desktop/tz-codex')))
sys.path.insert(0, os.path.join(root, 'repo', 'skills', 'top-picks', 'references'))
from catalyst_left_side_scanner import rotation_check, _get_pro
rotation_check(_get_pro())
"

# 催化新鲜度（判断当前主线是否还有🔴🟡新催化持续出现）
python3 "$TZ_CODEX_HOME/repo/skills/stock-analysis/scripts/event_map_query.py" window --source tech
python3 "$TZ_CODEX_HOME/repo/skills/stock-analysis/scripts/event_map_query.py" window --source nonfin

# 本周修正清单（corrections表出现"高拥挤/需要验证"类修正 → 判断主线是否进入"分歧"状态）
python3 "$TZ_CODEX_HOME/repo/skills/stock-analysis/scripts/event_map_query.py" corrections
```

---

## STEP 2：组装四状态判断

Read `references/market-state-machine.md` 第一节，
逐条对照STEP0/STEP1拿到的数据，给出：
1. 市场状态：进攻/震荡/防守/退潮（附证据）
2. 主线状态：强化/分歧/修复/退潮/新主线试探（附证据）
3. 风格状态：科技占优/非科技占优/高低切换/红利银行防守/存量跷跷板/全市场增量（附证据）
4. 风险偏好：上升/持平/下降/局部上升但整体分化（附证据）

**背离占比换算**（替代原Layer0"分化期"，见market-state-machine.md"背离占比"小节的回测表）：
- 0-10%：广度健康，市场状态判断可以正常沿用四档规则
- 10-30%：三个指数前瞻收益全部走弱，市场状态判断需要谨慎降级（比如"进攻"降级为"震荡偏强"）
- \>30%：参考market-state-machine.md原文，需在报告中明确标注当前处于历史上较少见的高背离区间

---

## STEP 3：按固定模板输出

Read `references/market-state-machine.md`
第三节"下周作战地图"模板 + 第四节"报告开头固定格式"，按原文模板逐字段填写，
不自创新格式。

**核心检查**：报告写完后自问market-state-machine.md开头列的三个问题——
①下周大盘环境允许进攻吗 ②当前主线是强化/分歧/修复还是退潮 ③如果盘面和预期不一样，
该看哪些信号修正判断。读者不看细节就能回答这三个问题，才算合格；如果不能，
说明堆了数据没给方向，重写。

---

## 错误处理

| 场景 | 处理 |
|---|---|
| TUSHARE_TOKEN未配置 | 提示检查 `$TZ_CODEX_HOME/repo/.env` |
| market_state_fetcher.py单个字段拉取失败 | 该字段标注"数据不可用"，不中断其余字段，四状态判断基于剩余可用证据继续给出 |
| 事件地图目录找不到 | 提示检查 `$TZ_CODEX_HOME/技能数据/科技产业事件/` 和 `$TZ_CODEX_HOME/技能数据/非科技产业事件地图/` |
| 当前是非交易日/盘中 | market_state_fetcher.py自动取最近一个已收盘交易日，报告开头注明数据日期 |
