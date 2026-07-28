---
name: project-p0-execution-20260724
description: 2026-07-24执行完P0两项（北交所排除+mapping覆盖率补录），mapping缺口从52%降到18个纯观察类事件；顺带修了sync_company_pool()不更新已存在公司关联事件ID的结构性bug
metadata: 
  node_type: memory
  type: project
  originSessionId: 2f8e6c22-e418-4538-b1d5-5346ec4d4ab5
  modified: 2026-07-24T00:26:28.302Z
---

2026-07-23讨论完claude.ai数据能力边界问题后梳理出P0/P1/P2任务清单，2026-07-24执行完两项P0：

**P0-2 前缀审计**：ETF份额抓取(`stock_data_fetcher.py`)漏"52"前缀已修；北交所（8/92/43/87/83开头）从未被SH/SZ分类逻辑支持——用户明确表态"北交所不参与，排除掉"，已在公司池同步唯一写入口`sync_company_pool()`加`BJ_CODE_PREFIXES`过滤，公司池里已混入的利尔达(920249)/贝特瑞(920185)两条脏数据也清理掉了。

**P0-1 mapping覆盖率补录**：用window模型权威口径（`load_active_events()`，bucket权重>0）重新核算，116个活跃事件里缺mapping的从28个（不是7-22那次记的旧数字51/37，事件集每天在变）陆续补到18个。分两批：第一批4个（事件材料里已经点名公司的：POWER-2026-006潍柴动力/MEM-2026-012中微公司拓荆科技/SEMI-2026-012先进封装光芯片EDA等15家/NEML-2026-001济川药业等9家中成药+荣昌生物）；第二批6个用WebSearch核实补充（FIBER-2026-006暗光纤→长飞光纤等光纤四杰/SEMI-2026-010功率半导体涨价→华润微等5家/FIBER-2026-003→大族激光等/AUTO-2026-001→广和通/MONKEY-2026-001实验猴→昭衍新药等5家CXO+WebSearch核实自养vs外购受益方向不一致/CONSUMPTAX-2026-001电池消费税→宁德时代亿纬锂能+WebSearch核实这是成本端政策不是纯利好）。

剩18个**故意没有硬填**：AIAPP-2026-010(阿里千问接苹果，主要受益方阿里巴巴是美股/港股，WebSearch没找到高确定性A股关联)、MEM-2026-010(事件原文自己写明"非直接对应A股标的")，以及一批行业会议/海外公司/纯政策观察类事件(WAIC/TSMC Symposium/MWC/GPT-5.6/高通投资者日/AWS提价等)——这些本质上不是"该有个股映射但没填"，是mapping表这个字段本来就不适用于它们，硬凑category标签不会真正让scanner关联到任何公司（`sync_company_pool`按公司名精确匹配，泛泛的"XX公司类型"文本匹配不到任何code），凑数没有实际价值。

**顺带发现并修复的新bug**：`sync_company_pool()`对"公司已存在于池子里"的情况，此前只计数(`already += 1`)不做任何更新——同一家公司后续被新事件再次提及，那个新的关联事件ID永远不会被记录进`关联事件ID`字段，公司会永远停留在它第一次入库时关联的那个事件上。今天手工修CONSUMPTAX-2026-001时补录亿纬锂能才发现这个问题（它已经在池子里挂着"BAT-STORAGE"这个占位事件ID，新的CONSUMPTAX-2026-001关联怎么都进不去）。已在`sync_company_pool()`里加了"已存在公司也检查+追加新事件ID"的逻辑（并集追加不覆盖，同`sync_sectors_status`的signal_stock_code模式），用一次性可回滚测试(士兰微+假事件ID)验证过추加正确、不重复、不污染其他行，测试数据已清理干净。

**Why**：这是[[user-sector-rotation-anchoring-bias]]之外的另一条主线——2026-07-23跟claude.ai讨论完"数据源能力边界"发现问题无解后，转向"把能做的做完"，这次P0执行体现了"不为了凑覆盖率数字牺牲数据真实性"的原则，遇到不确定的公司归属宁可留白+说明原因，也不编造。

**How to apply**：下次继续处理剩余的18个"无需mapping"事件时，如果发现某个会议/政策类事件后续演化出了具体的个股受益逻辑（比如WAIC上有公司发布重磅产品），再单独补录，不需要现在强行处理。P1(Layer4对齐market-state-machine + 情景概率多因子回测)仍待专门时间做，见[[project-layer4-market-state-alignment]]和[[project-scenario-probability-backtest]]。
