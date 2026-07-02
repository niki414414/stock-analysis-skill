---
name: stock-skills-project
description: 用户的A股投资辅助技能体系——两个技能的设计决策、框架演进和关键规则，用于跨会话延续开发
metadata: 
  node_type: memory
  type: project
  originSessionId: 93271555-862f-4008-8e6e-aa7edc11839b
---

## 技能体系现状（2026-06-06，v3.0 日扫+回测验证版）

### v3.0 核心变更（2026-06-06）

**框架升级至 v4.2（回测验证版）**
- 34只股票、136个信号、3月-6月回测，总准确率53.7%
- 新增 Layer 0 市场阶段识别（阶段A/B/C，决定D/E/A轨迹处理方式）
- C轨迹MA20规则修正：跌破≤15%→WAIT（原REJECT，误伤率71%）
- C+轨迹新增趋势跟踪入场流程（3%回调触发）
- D/E轨迹加回调幅度过滤（D需回调≥10%才降级，E需回调≥5%）
- A⚠️升级条件扩展：市场阶段A+板块主升期自动触发
- B轨迹入场条件表格化，市场阶段A时门槛降低至1/3条件
- 详见 [[backtest-framework-correction-v42]]

**自选池日扫系统（新建）**
- 61只股票，10个赛道：半导体14/光通信11/MLCC3/PCB6/算力6/储能6/电力8/机器人3/有色+航天+医药+核电
- 代码串已存入 daily_notify.py 的 WATCHLIST 常量
- 轨迹分类：A/A⚠️/B/C/C+/D/D→C/E/E回踩
- 信号分级：⚡立即触发完整分析 / 👁状态关注 / —无异动

**daily_notify.py 升级至 v3.0**
- 新增 fetch_watchlist_scan() + format_scan_section()
- 收盘后15:30推送新增"自选池日扫"板块
- 开盘前8:30去掉重复的昨日龙虎榜，改为"昨日待处理⚡信号"缓存提醒
- 调试命令：`python3 daily_notify.py --scan`（只跑日扫不推送）
- 信号缓存文件：`~/.claude/skills/last_scan_alerts.txt`

**output-format-template.md 新增行动清单模块**
- 每次分析末尾必须输出：🔄需要更新的数据 / 👁需要你自己盯的 / 🤖建议问AI的 / ✅框架已处理的

**analysis-prompt-template.md 新增**
- yaml健康检查逻辑（每次分析前检测stage与实际走势是否矛盾）
- Layer 0 市场阶段识别（在Layer 1前执行）
- 硬性约束第11条：行动清单必须输出

**market_status.yaml 更新**
- hvdc_transmission（特高压）：主升期→调整期，2026-06-06，触发：特变电工60日-21.5%
- power_grid（算电协同电网）：主升期→调整期，同上

**今日扫描快照（2026-06-06收盘）**
- ⚡信号3只：拓普集团(601689)、汇川技术(300124)、多氟多(002407)——均C轨迹逆势
- 烽火通信(600498)今日+7.1%量比2.2x，算法判A但实为A⚠️边界，明日继续观察
- 英维克(002837)60日-32%，建议更新 power_ups_cooling 的stage

**待做**
- 回测更多股票（用户计划明日继续）
- 英维克所在赛道 yaml stage 更新
- 底背离/顶背离规则待后续加入框架

---

## 技能体系现状（2026-06-02，v2.2 双框架版）

### v2.2 核心变更（2026-06-02）

**架构决策：双框架分析**
- Framework A：现有5维度评分（中线3-6个月，基本面驱动）
- Framework B：动能三条件检查清单（短线5-15个交易日，量价驱动）
- 两套框架独立输出，不合并评分，在 ⑨b 区块展示交叉判断
- **Pool 2 属于 /stock-analysis，不属于 /top-picks**（架构决策已定）

**新增 calc_momentum_signal()（stock_data_fetcher.py）**
- 条件1：今日成交额/20日均 ≥ 2.0x（量能异常）
- 条件2：price > MA60 AND MA60斜率 > +0.15%/5日
- 条件3：chip_activity < 0.8（筹码锁定，复用 calc_chip_concentration）
- 3条件 → ⚡⚡⚡强/2条件 → ⚡⚡中/1条件 → ⚡弱
- 输出字段：momentum_signal，已接入 analyze_stock() result

**analysis-prompt-template.md 新增 STEP 6**
- Framework B 动能清单格式
- 两框架交叉解读表（A高+B强=最优；A高+B弱=中线等待；A低+B强=纯动能非用户风格）

**output-format-template.md 新增 ⑨b 区块**
- 紧贴 ⑩ 用户最终判断之前
- 三条件表格 + 信号强度 + 持有窗口 + 交叉解读一句话

**微信推送系统（2026-06-02 新建）**
- 脚本路径：~/.claude/skills/daily_notify.py
- 推送方式：Server酱（SendKey已存入 ~/.claude/skills/.env）
- 推送时间：工作日 8:30（开盘前）+ 15:30（收盘后）
- 消息结构：两段合一条——【主线池动向】Track A + 【全网动能】Track B
- Track A：signal_scanner.py 扫描约150只四大主线股
- Track B：昨日涨停池（akshare）+ 昨日Top3行业板块成分（akshare）→ 候选150-300只 → Tushare pro.daily() 拉结算数据（避免实时接口不稳）→ 按量比排序
- cron 已配置：30 8 * * 1-5 / 30 15 * * 1-5

**池子问题**
- 问题A：覆盖不够（150只主线池外有机会）→ Track B 推送已部分弥补
- 问题B（已解决2026-06-15）：stock_universe.yaml头部已新增"纳入与维护标准"，含tier判定/也在跨赛道标注/tradeable含义/新增和剔除条件

**参考资料（~/Desktop/tz/）**
- DeepSeek TXT：叙事分析强，事件日历+扩散路径+预期差框架有价值
- GPT Excel（10个Sheet）：结构化数据库，字段体系（trading_stage/expectation_gap/diffusion_phase）可借鉴到 market_status.yaml
- 半导体/电力产业报告：AI生成，框架结构可用，具体数字（概率/CAGR/市占率）需打折
- 核心可借鉴：watch_signals 字段（每赛道加2-3个实际可查预警指标）、算电协同子赛道细分、产品线有效期与 wave_number 关联

**待做清单（按优先级）**
1. market_status.yaml 加 trading_stage + watch_signals 字段（中优先级）
2. 算电协同子赛道细分（power_ups_cooling 拆为 dc_power_supply + green_compute_synergy）
3. 节假日交易日历接入（cron 目前只跳过周末）

---

## 技能体系现状（2026-06-01，v2.1 架构修订版）

### v2.1 五大架构修订（2026-06-01）

**修订1：Rule 7 解耦股票池（analysis-prompt-template.md）**
- 扩张期：不在主线 → 软性标注（⚪ 非当前主线），不拒绝分析，维度1子项A=0分，其余维度正常评分
- 收缩期：仍为硬过滤（非主线+非防御池 → 拒绝）
- 原因：股票池尚不完备时，不应用股票池覆盖度来约束分析框架；分析基于因子，不依赖股票池

**修订2：乖离惩罚减轻（stock_data_fetcher.py calc_trend_score + analysis-prompt-template.md）**
- bias_ma5 在 5-15% 区间：从 4/20分 → 10/20分（量化抱团时代强势股乖离必然大）
- bias_ma5 ≥ 15%：6/20分（保持警示但不重惩）
- 技术规则更新：乖离>5%时区分"有量/无量"，不再简单标"不加仓"

**修订3：缺失数据诊断与修复（stock_data_fetcher.py）**
- realtime 空白兜底：加入 OHLCV 最后一根 K 线作为 price/change_pct fallback（尤其适用休市场景）
- fund_flow_multiday：增加多接口尝试（3个akshare接口名备选）
- sector_breadth：加入 tushare行业→akshare板块名 翻译表（_TUSHARE_TO_BOARD），先尝试原名，失败再尝试译名
- realtime 优先级调整：efinance单股优先（轻量），akshare全量备选（字段更丰富）

**修订4：新增 动力自重比 calc_volume_heat()（stock_data_fetcher.py）**
- 输出：power_weight_ratio（成交额/流通市值%）、heat_level、turnover_rate、turnover_level、volume_ratio、volume_ratio_level
- 维度4新增量能热度子项（4分），单日异动5→3分，3日振幅5→3分，维度4总分仍15分
- volume_heat 为空（休市/realtime缺失）→ 中性2分

**修订5：新增 筹码集中度 calc_chip_concentration()（stock_data_fetcher.py）**
- 使用VWAP近似法，基于60日K线估算筹码分布
- 输出：vwap_20d/vwap_60d、in_profit_vol_pct（获利筹码比例）、chip_activity（近5日/20日换手比）、interpretation
- 仅展示，不评分（近似估算，信心不足以评分）
- 展示格式：【筹码参考】{vwap_position} | 获利筹码{pct}% | {interpretation}

## 技能体系现状（2026-05-25，两技能体系 v2.0）

### 技能二：/top-picks（v2.0，主线分区看板）
路径：`~/.claude/skills/top-picks/`
**定位**：全流水线漏斗选股——无需用户输入，自动从四大主线宇宙池出发三层漏斗，按主线分区输出 6-8 只 + 全局 Top 3
**漏斗**：四大主线~150只 → 量化粗筛 Top 40（主线均衡，每活跃主线≥8只）→ 催化剂验证 Top 20 → 完整5维度评分 → 每主线 Top 2 + 全局 Top 3
**screener关键变更**：top80用于拉资金流（留补位缓冲），_balanced_top40()确保主线覆盖，输出JSON key=top40
**轻过滤**：ST/停牌/科创板688/价格>300元/市值<30亿；重过滤留给 /stock-analysis
**key文件**：SKILL.md + references/top_picks_screener.py + references/output-format-template.md（v2.0主线分区格式）

### 技能一：/stock-analysis（v2.0 波次感知版）
路径：`~/.claude/skills/stock-analysis/`
**定位**：被动分析——用户给A股代码，输出个性化"适不适合您"决策看板

**v2.0 核心升级**（2026-05-25，P0-P1全部完成）：
- **维度2完全重构**（P0-1）：原单一板块20日涨幅评分→三子指标（10+8+7=25分）
  - 子指标A：龙头距前高（用stock自身OHLCV，排除近20日的120日最高价；6档评分；<60日数据→中性5分）
  - 子指标B：跟风股活跃度（akshare板块成分接口实时计算上涨比例；>50%普涨→2分；30-50%分化→6分；<30%龙头独涨→8分）
  - 子指标C：当前波次（从yaml wave_number字段读取；null→中性5分；1/2/3/4→7/5/3/1分）
- **market_status.yaml扩展**（P0-2）：46个子赛道全部新增 wave_number/wave_position/leader_stocks/sector_20d_pct/catalyst_quality/last_updated 字段（占位符，用户月度手动填）
- **收缩期防御池**（P0-3）：新增 defense_pool（红利/银行/公用事业）；规则7区分扩张期/收缩期逻辑
- **减少WebSearch**（P0-4）：移除板块20日涨幅WebSearch；sector_20d_pct仅做异常监控（>30%过热/< -10%退潮）；减少1次WebSearch/每只股票
- **波次检查软规则**（P1-1）：wave≥4+无超预期催化+非创新高→-10分+警告
- **ETF资金面子指标**（P1-2）：维度3从25分→30分，主力获利率5分→ETF资金共识10分；新增fetch_etf_fund_flow()
- **选股历史记录**（P1-3）：top-picks每次输出后自动写入~/stock_picks_log.md
- **脚本新增输出字段**：high_baseline（排除近20日的120日最高价）、sector_breadth（板块宽度）、etf_fund_flow

**用户确认的设计决策**（v2.0，2026-05-25）：
- 子指标A对象：被分析股票自身（非yaml leader_stocks）
- 子指标B数据：akshare板块接口（非WebSearch）
- sector_20d_pct：辅助展示/异常监控，不参与评分
- 子指标A排除近20日：避免"昨日创新高今日还创新高"虚假信号

**v1.2 核心升级**（2026-05-23，已被v2.0覆盖，仅保留历史参考）：
- `market_status.yaml` 全面重构：从平铺板块列表改为四大主线 × 子赛道层级结构
  - 半导体全链（25个子赛道，精细颗粒度）
  - 机器人（4个子赛道，赛道级）
  - AI算力+算电协同（6个子赛道）
  - 无人驾驶（4个子赛道）
  - 观察池：低空经济/固态电池/储能/商业航天
- 硬过滤新增规则7：股票不在四大主线内 → 直接拒绝推荐（持仓股豁免）
- 维度1评分重构（25分，三分项）：
  - A. 子赛道优先级（10分）：priority 1/2/3 → 10/7/3分
  - B. 赛道相对位置（5分）：低位/中位/高位 → 5/3/1分（相对近期轮动，非绝对估值）
  - C. 超预期催化质量（10分）：超预期业绩/大订单/政策点名→10；事件型→6；纯动量→1
- 估值三重高估规则加主线豁免：主线内+近30天超预期催化 → 降为软性警示(-5分)，不硬禁
  - 原因：当前主线整体PE处于历史90%+分位是结构性现象，硬禁等于封杀整个主线
- 板块名称对照表更新：tushare行业 → 四大主线子赛道key的完整映射

**Why:** 当前A股主线半导体PE在历史90-99%分位，但行情仍在；高低切换的"低"是相对轮动涨幅的低位，不是绝对估值低。框架需要区分"低位有催化"（最优）vs"高位有催化"（可跟）vs"高位无催化"（不追）。

### 技能二：/sector-rotation（v1.1 主线聚焦版）
路径：`~/.claude/skills/sector-rotation/`
**定位**：主动扫描——识别轮动板块，输出候选代码列表，然后喂给 /stock-analysis 深挖

**v1.1 升级内容**（2026-05-23）：
- STEP 0 新增：Read market_status.yaml 加载四大主线框架
- 候选池从 Top 5 扩大到 Top 8（供主线过滤用）
- 新增 STEP 2.5：主线过滤 + 子赛道映射
  - 不在四大主线内的板块直接剔除
  - 按 priority 加权重排（priority1 × 1.3 / priority2 × 1.0 / priority3 × 0.7）
  - 最终输出主线内 Top 3 板块
- 输出格式新增字段：主线归属、子赛道label、赛道优先级、赛道位置（stage+style_position）
- 催化剂类型映射到维度1 C项评分显示

---

## 四大主线框架（十五五，季度锚定）

| 主线 | 颗粒度 | 原因 |
|------|--------|------|
| 半导体全链 | 精细（25个子赛道）| 制裁清单逐项触发独立行情 |
| 机器人 | 赛道级（5个，含工业母机）| 资金以板块为单位轮动 |
| AI算力+算电协同+数据中心互联 | 赛道级（12个，含核电+卫星通信）| 情绪+大事件驱动；互联链条是算力的隐性受益 |
| 无人驾驶 | 赛道级（4个）| 监管节点驱动 |

**主线三扩充细节**（v1.3，2026-05-23）：
- 新增互联链：optical_module_cpo（光模块/CPO）P1 / pcb_ai_server（高速PCB/铜箔）P2 / fiber_optical_dc（光纤光缆）P2 / ai_network_infra（交换机/连接器）P2
- 新增算电延伸：power_nuclear（核电/SMR）P1 / satellite_comms（卫星互联网）P2
- 算电协同明确拆分为两种资金行为：power_grid（政策周期型机构主导）vs power_ups_cooling（算力需求跟涨型）
- physical_ai 加注：与机器人主线高度联动，双主线计分

**主线二新增**（2026-05-23）：cnc_machine_tools（工业母机/高端数控机床）P2
- 逻辑：机器人+半导体设备国产化的制造基础，放大池子，量能过滤

**观察池**（有商业化突破信号时升级）：低空经济/固态电池/储能/商业航天

---

## 分析框架核心规则（analysis-prompt-template.md）

### 动态权重体系（已实现）
- **扩张期（≥3/5分）**：成长25% + 催化剂25% + 质量20% + 情绪10% + 估值10% + 技术10%
- **收缩期（≤2/5分）**：质量30% + 估值20% + 成长15% + 催化剂10% + 情绪10% + 技术15%

### 估值三维判断（已实现，主线豁免已加）
旧规则：PE历史分位>90%=禁买（过于简单）
新规则：**三重同时触发才禁买**：历史分位>90% AND PEG>2 AND 分析师预期无上调
**主线豁免**：在四大主线内 + 近30天超预期催化 → 软性警示(-5分)，不禁买

### 催化剂分类（已实现）
- 结构性催化（6个月内落地业绩）→ 高权重
- 事件型催化（1-4周，落地不确定）→ 中权重
- Flash事件（当日-3日情绪）→ 不影响中线

### 技术面规则（已实现）
- MA5乖离>5%：不加仓（已持仓不影响中线判断）
- RSI>80：推迟入场，不改变中线判断

---

## 数据源现状

### 已接入（免费）
- efinance + akshare + yfinance：历史K线、实时行情、技术指标
- 北向资金20日净流入：`ak.stock_hsgt_fund_flow_summary_em()`
- 主力大单净流入：`ak.stock_individual_fund_flow(stock, market)`
- 龙虎榜机构席位：`ak.stock_lhb_detail_em(date, flag="全部")`

### 已接入（Tushare Pro）
- Token 配置：`~/.claude/skills/.env`
- `fetch_tushare_fundamentals()`：PE/PB历史百分位、ROE、利润增速、PEG、业绩预告
- 机构持仓：`top10_floatholders`（季报，sector-rotation使用）

### 待接入（有价值）
- 融资余额变动（akshare免费，未实现）
- 机构调研记录（akshare免费，未实现）

---

## 用户投资偏好
- 持仓周期：3-6个月（季度级）
- 不能每天盯盘，无法做规律短线
- 技术面保留用于择时入场，基本面决定买不买
- 看板风格偏好：卡片式决策看板（不要报告文章风格）
- 下次画像校准：2026-08-14
