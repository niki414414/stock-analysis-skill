---
name: project-market-outlook-coverage-20260722
description: 2026-07-22大盘复盘暴露sw_daily/ths_daily数据滞后+mapping覆盖率缺口+市场广度粒度不足三个真实问题，均已修复；新增Layer2/3篮子动量层
metadata: 
  node_type: memory
  type: project
  originSessionId: dff83b5c-04b0-4fbd-bcaa-c6049d3f530d
---

2026-07-22收盘后跑`/market-outlook`复盘，用户当场指出报告说"有色/贵金属/算力租赁"还在跌，但实盘这三个方向恰恰是当天涨得最好的——顺着这条线一路查下去，发现三个独立但环环相扣的真实问题，全部修完。

**问题1：`sw_daily`接口比`pro.daily()`/`index_daily()`慢发布至少1个交易日**
`catalyst_left_side_scanner.py`的`rotation_check()`（申万行业轮动雷达，`/top-picks`和`/market-outlook`共用）直接拿`sw_daily()`最后一行当"今天"用，没做新鲜度校验。实测同一个申万指数代码(801050.SI有色金属)，`index_daily()`当天下午就有当日数据，`sw_daily()`还停在前一交易日。修复：①换成`index_daily()`（同样的申万代码，两个接口都支持，直接换接口没有数据缺失）②加新鲜度校验，万一哪天`index_daily()`也滞后会在标题里报警，不再静默用旧数据当新数据。同时顺手把`_SW_SECTORS`从22个补齐到申万31个一级行业全覆盖。

**问题2：`resolve_trade_date()`还在用"hour<15"猜收盘时间**
同一个文件里`resolve_trade_date()`（决定扫描器用哪天的候选）还留着这个会话前面已经在`stock_data_fetcher.py`修过的老bug模式——沙盒时钟14:43被误判成"未收盘"，导致扫描器拿前一交易日的候选跑了一整天。改成跟`fetch_market_breadth()`一样的直接探测法（试着拉当天数据，拉到就用，拉不到才退回前一天）。**这提醒：同一类bug可能在多个文件里各自独立存在，修过一处不代表全修完，遇到"数据好像滞后"要习惯性检查是不是同一个模式**。

**问题3（最大的一个）：mapping表覆盖率缺口——52%的活跃事件没有关联公司**
根查到底发现：算力租赁老事件(TOKEN-2026-001等)衰减模型判断是对的(🟢趋势配置期，持轻仓等Wave2)，真正问题是当天驱动Token工厂大涨的新催化(`AI-2026-020` Kimi K3，★★★★★/🔴红桶)**从建档起就没有任何mapping关联**——事件记了，但"这个事件该关联哪些受益公司"这一步被跳过了。顺手查了一下全库：98个活跃事件里51个(52%)完全没有mapping行，包括十几个★★★★★级别的(MLCC-2026-002/003/004、PKG-2026-001、AI-2026-013/016/017/019/023等)。这是处理研究材料时"记事件"和"记事件→公司映射"这两步经常只做了第一步的系统性疏漏，不是催化衰减模型的问题。

已补录14个★★★★★事件的mapping关联（含AI-2026-020→行云科技/利通电子/光环新网等算力租赁代表公司），`sync_company_pool()`+`sync_sectors_status()`自动同步生效。**还有~37个非五星活跃事件仍缺mapping，值得找专项时间继续补**（这次只做了最高优先级那批）。

**新增两层数据（直接解决"大盘分析广度不够"的问题，而不是简单扩大申万数量）**：
- Layer2 `fetch_subsector_basket_momentum()`（`market_state_fetcher.py`）：复用sectors_status.csv的76个子赛道`signal_stock_code`篮子（问题3修完后覆盖率大幅提升），用`pro.daily()`逐只现算当日/5日/20日等权涨跌幅，不依赖任何"预算好的指数"接口。颗粒度卡在"申万31个一级行业"(太粗)和"个股"(太细)中间，能看到"有色金属"里贵金属被稀释、"电子/通信"里token_factory/optical_module_cpo各自不同走势这类被大类掩盖的信号。
- Layer3 `fetch_macro_hedge_baskets()`：手工维护的宏观避险篮子清单(`MACRO_HEDGE_BASKETS`)，目前只有贵金属/黄金6只代表股。这类资金流向是宏观/地缘驱动，天然在事件地图/公司池的科技产业催化框架之外，不适合硬塞进mapping表，单独维护一个很小的清单即可。2026-07-22实测贵金属篮子当日+6.7%/20日+19%，是当天全报告里最强的独立信号。

**Why这次没有走"申万继续细分成二级行业"或"全面接入akshare概念板块"这类更重的方案**：概念指数(`ths_daily`)测过也有同样1天的发布延迟问题，不能指望换个"预算好的指数"数据源解决同日新鲜度问题；而重新造一套细分题材篮子体系，会跟公司池/mapping表的现有基础设施重复——问题3修完之后，Layer2需要的篮子数据本来就已经自动生成好了，直接复用即可，不需要新建维护体系。

**How to apply**：以后遇到"大盘复盘数据跟实盘对不上"，先怀疑数据源发布延迟(用`index_daily()`同代码对照`sw_daily()`/`ths_daily()`能快速验证)，再查是不是mapping覆盖率缺口(`活跃事件数 - 有mapping关联的事件数`能快速定位)，两者都是这次实测证实过的真实、可复现的问题模式，不是零星个例。

迁移包已同步（`market_state_fetcher.py`/`market-state-machine.md`/`market-outlook_SKILL.md`此前都不在migrate复制清单里，这次一并补进去，同时发现`market-outlook_SKILL.md`如果直接用"SKILL.md"复制会跟`update-event-map`自己的SKILL.md撞名互相覆盖，已改名复制）。
