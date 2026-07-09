---
name: feedback_tool_integration_standard
description: 新脚本/工具完成的判定标准——必须真正接入技能workflow并入库，独立能跑不算完成
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 6c1bbab6-3cfc-49d3-b430-cb83f4813bc1
---

新建的分析脚本/评分工具，不能只是"自己单独跑起来是对的"就算完成。必须满足三条才算真正完成：
1. 有至少一个SKILL.md的实际STEP被改成调用它（不是只留一个CLI子命令等着被手动查）
2. 改动同步进 `~/Desktop/tz/repo`（容灾备份源），不能只留在 `~/.claude/skills` 本地
3. 确认没有遗留的重复/平行评分逻辑仍在运行（旧的独立打分不下线，新工具再对也没用）

**Why:** `catalyst_window_model.py`（2026-07-04建，见[[catalyst-window-model]]）单独跑完全正确，但从未被写进`/top-picks` STEP1或`/stock-analysis` STEP3的实际指令里，只是`event_map_query.py window`的一个旁路CLI命令；同时`catalyst_left_side_scanner.py`自己的静态`catalyst_score`逻辑一行没动。结果是这个"对齐"工作存在了5天，两个技能的实际行为完全没变，直到2026-07-09用户要求跑一次左侧扫描才发现候选全是过期催化。备份仓库还停留在07-02，比这次改动还早，disaster recovery会直接丢失这份工作。

**How to apply:** 每次做完一个新脚本/新评分逻辑后，主动检查上述三条，而不是默认"代码写完+验证过一次就是完成"。涉及"多个工具算同一件事"的场景（如催化新鲜度），改完一个要顺手确认另一个消费方是否已经切过去用它，不能新旧并存。参见[[project-leftside-rightside-unification]]。

**二次验证（2026-07-10）：** 修完scanner/stock-analysis接入问题后，检查`event_map_updater.py`的`cmd_migrate`（迁移包生成器）的文件复制清单，发现`catalyst_window_model.py`同样没被加进去——同一类"新文件忘了加进所有消费清单"的疏漏，这次出现在打包脚本里。另外migrate每次在同一输出目录上叠加生成、不清空，导致06-30版本的旧目录结构（05_技能代码/旧schema）残留了10天没被发现。以后任何"生成/打包/导出"类脚本，新增文件后要检查它是否也需要加进导出清单；生成类脚本优先做成"先清空再全量生成"而不是"在旧产物上叠加"。
