---
name: project-infrastructure-20250625
description: 6/25基础设施变更：v4.8框架升级+事件地图csv0624+研究材料工作流+供应链图谱启动
metadata: 
  node_type: memory
  type: project
  originSessionId: aed7a8eb-27c2-4ae3-b211-971f8d035851
---

## 框架升级 v4.7→v4.8（2026-06-25）
- Layer 3新增A/B维度评估模块（Dim A当前业绩 + Dim B未来叙事四条件检查）
- 盖章人从"NVIDIA/TSMC/Apple"扩展为按领域映射（华为/宁德/比亚迪/长鑫等）
- 输出模板新增A/B定性行+四条件打分表

## 事件地图 csv0621→csv0624（2026-06-24）
- 新增5个events：功率器件涨价(SEMI-2026-007)、台积电涨价(SEMI-2026-008)、MCU涨价(SEMI-2026-009)、液冷Rubin(AI-2026-011)、字节capex(AI-2026-012)
- 更新3个events：PCB-2026-002(Kyber延迟)、FIBER-2026-001(压价辟谣)、MEM-2026-007(美光Anthropic)
- 新增3条corrections + 5条signals_early + 3条forward + 5条sources

## 研究材料工作流建立
- 用户在~/Desktop/tz/研究材料/下按日期存txt文件
- 分析师用/update-event-map技能读取→比对→入库
- 首批材料：0624.txt（6/22-24三天研报/公众号汇总）

## 供应链图谱（已讨论，待建设）
- 用户希望建"盖章人→一级供应商→二级供应商"的层级图
- 现有公司池721家已有纵向层级（赛道→环节→定位），缺横向供货关系
- 计划：按需逐链补充，在公司池加"已知客户/盖章人"列
- 优先级：液冷→钨/WF6→PCB

## 待做事项
- v4.8严谨回测（涨跌市各一窗口×随机20只×完整流程）
- 事件地图加"盖章人"+"盖章强度"字段
- 公司池加"已知客户"列
