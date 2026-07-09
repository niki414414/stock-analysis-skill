---
name: catalyst-window-model
description: 催化窗口期量化模型v1.0：A-E五类催化类型参数、event_map_query.py window命令、数据闭环设计
metadata: 
  node_type: memory
  type: project
  originSessionId: 9a7a3eff-e038-471e-b029-a8da586c7786
---

## 催化窗口期量化模型 v1.0（2026-07-04建立）

### 核心文件
- 模型：`~/.claude/skills/stock-analysis/scripts/catalyst_window_model.py`
- 调用：`python3 ~/.claude/skills/stock-analysis/scripts/event_map_query.py window`
- 按赛道：`... window --sector 半导体`

### 五类催化类型（基于reviews.csv 15条实证校准）

| 类型 | 名称 | Wave1窗口 | 半衰期 | Wave2 | 实证样本 |
|---|---|---|---|---|---|
| A类 | 发布会/展会/季节性 | T-3到T+0 | 5天 | 否 | n=1 |
| B类 | IPO里程碑/首单/发射 | T+0到T+30 | 15天 | 有（T+30-90） | n=4 |
| C类 | 政策管制/市场结构 | T+0到T+14 | 7天 | 有（T+60-90季报） | n=3 |
| D类 | 涨价传导型 | T+0到T+42 | 21天 | 有（T+60-180） | n=5 |
| E类 | 叙事/早期信号 | T+0到T+30 | 21天 | 有（T+90-360） | n=2 |

### 输出格式（五色分层）
- 🔴 峰值临近 T-7到T+7（立即操作）
- 🟡 窗口进行中 T+0到T+21（持续跟踪）
- 🟢 趋势配置 T+21到T+90（月度跟踪）
- 🔵 Wave2窗口（季报验证，低拥挤重入）
- ⚪ 等待/观察（暂不操作）

### 当前已知局限
- event_date取事件首次发生日期，corrections表的升级不会刷新时钟
- 15行reviews样本量较小，参数置信度中等
- HW950等"年7月起"格式事件被正确解析为7月1日
- MLCC-2026-001（5月起）因T+64天已进入Wave2区

**Why:** 用户发现"催化类型→持仓周期"映射比纯事件列表更指导操作，要求建立数据闭环。
**How to apply:** 每次/top-picks或/stock-analysis前先运行window命令获取当前窗口位置，确认目标事件处于🔴或🟡再操作。
