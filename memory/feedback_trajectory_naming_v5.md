---
name: feedback-trajectory-naming-v5
description: 2026-07-17轨迹/市场阶段/Dim字母命名系统全部改成中文词，字母代号(A/B/C/D/E)已在框架文件里退役
metadata:
  node_type: memory
  type: feedback
  originSessionId: 3e45492b-6bc8-4f46-82d5-327b5e6657bf
---

用户反馈"轨迹A/B/C/D、阶段A/B/C/D、Dim A/Dim B"这套字母命名太难记（"我自己有时候都记不住"），2026-07-17系统性改名，覆盖`~/.claude/skills/stock-analysis/`下所有引用（analysis-prompt-template.md/output-format-template.md/SKILL.md/research_log.md/market-state-machine.md）。**以后讨论持仓/框架判断，一律用新名，不要再用字母代号。**

## 映射表

**轨迹**（第4层，股票走势状态）：
| 旧 | 新 |
|---|---|
| 轨迹A（下跌途中）| 破位 |
| 轨迹A⚠️（晚进场候选，A的例外升级）| 破位⚠️ |
| 轨迹B（下跌末期蓄势/止跌）| 稳位 |
| 轨迹C（上涨途中回踩）| 回踩 |
| 轨迹C+（强势延续无回踩）| 强势 |
| 轨迹D（高位震荡）| 盘整 |
| 轨迹E（突破新高，此前命名范围遗漏过一次）| 新高 |

**市场阶段**（Layer 0，大盘层面，2026-07-17才发现这套字母系统贯穿全文34+处引用，比最初以为的"轨迹D内部嵌套小分类"规模大得多）：
| 旧 | 新 |
|---|---|
| 阶段A（调整末期/复苏初期）| 复苏期 |
| 阶段B（主升期）| 主升期 |
| 阶段C（过热末期）| 过热期 |
| 阶段D（高位结构分化，已被"背离占比"指标部分取代，见[[project-market-status-unification-20260712]]相关）| 分化期 |

**Dim**（第3层，个股估值构成）：
| 旧 | 新 |
|---|---|
| Dim A / Dimension A（当前主力业绩）| 现实腿 |
| Dim B / Dimension B（未来期权叙事）| 叙事腿 |

**明确不改的**（同样含字母但不在混淆范围内，用户没有反馈看不懂）：
- 盖章人评级 A/B/C级（供给稀缺度证据强度，在"叙事腿"评估内部，跟Dim A/B是两回事）
- 催化类型A-E（`catalyst_window_model.py`，已有emoji区分：A🎭/B🚀/C⚖️/D📈/E🔭）

**Why:** 字母代号本身不携带语义，用户长期记混，尤其轨迹和阶段两套字母表面相同(A/B/C/D)实际指完全不同的东西(个股走势 vs 大盘阶段)，讨论时经常需要反复确认"你说的是哪个A"。中文词直接携带含义，不需要查表。

**How to apply:**
1. 跟用户讨论持仓/入场判断时，直接说"回踩""稳位""过热期"等新名，不要退回字母。
2. 若用户自己说出旧字母（"这是轨迹C吗"），可以对应确认但主动用新名回应，帮助巩固新命名。
3. `~/.claude/projects/-Users-niki/memory/trading-framework-checklist.md`（v4.8核心检查清单memory）内容还是旧字母写的，没有跟着重写（工作量大、且是历史累积记录），已在该文件顶部加了映射表指针，读它时先按指针换算成新名再用。
4. 其余历史回测类memory（backtest-framework-*、backtest-monthly-*等）按写作时间点的字母命名保留不动，属于历史记录，不需要回填新名。
