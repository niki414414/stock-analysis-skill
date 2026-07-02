2026-2027科技产业事件地图数据库｜AI软件与服务层补录版
更新时间：2026-06-18

本版基于 20260618 早期信号增强版继续修改，补齐AI软件与服务层六个子赛道：
1. 算力租赁/GPU云服务
2. Token工厂/算力金融化
3. AI SaaS/大模型应用平台
4. AI Agent/智能体基础设施
5. 端侧AI/AI终端硬件
6. AI数据服务/标注/合成数据

核心结构：
- events：新增AI软件/服务层核心事件，保留事件时间、当前状态、重要程度、是否已被交易、预期差、后续观察指标。
- mapping：按上游/中游/下游拆解产业链环节，代表公司仅用于产业链定位，不构成股票推荐。
- forward：每个子赛道设置未来30天/90天/12个月前瞻催化。
- corrections：补充数据来源口径、传闻进入signals_early、AI应用需验证ARR/付费转化等动态修正。
- signals_early：承接GPU租赁价格、Token套餐、AI应用订单等未完全核验线索。
- sources：回填CME、Google、Microsoft、Gartner、Reuters、Counterpoint、Omdia等公开来源。

读取规则：
- 外部AI/研究助手优先读取 events，再按事件ID关联 mapping / forward / corrections / signals_early。
- 算力租赁价格、Token定价、具体订单量若来自传闻或小作文，只放入signals_early，不写入事实事件。
- AI软件层与硬件层关系：算力租赁与AI算力基础设施/液冷/电力是上下游；AI SaaS/Agent/端侧AI/数据服务通过Token消耗和推理请求拉动光模块、PCB、服务器、存储、液冷等硬件需求。
- 禁止将代表公司/公司类型解读为股票推荐。

文件清单：
- events_20260618_ai_software_services.csv
- mapping_20260618_ai_software_services.csv
- forward_20260618_ai_software_services.csv
- corrections_20260618_ai_software_services.csv
- sources_20260618_ai_software_services.csv
- signals_early_20260618_ai_software_services.csv
- weekly_20260618_ai_software_services.csv
- reviews_20260618_ai_software_services.csv
- README_20260618_ai_software_services.txt
