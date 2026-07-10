---
name: reference_update_framework_skill
description: 新建的/update-framework技能位置和用途——改动分析框架逻辑前必须遵循的验证规范
metadata: 
  node_type: memory
  type: reference
  originSessionId: 6c1bbab6-3cfc-49d3-b430-cb83f4813bc1
---

`~/.claude/skills/update-framework/SKILL.md`（2026-07-10新建）：任何要修改六层框架判断逻辑、scanner/window模型打分方法、仓位/止损/目标价计算、`user_profile.yaml`等配置的请求，先过这个技能的验证流程（拆解假设→查证标准→回测标准→自查清单→验证不通过不写入正式文件），不要直接改`analysis-prompt-template.md`等文件。

**Why:** 直接源于2026-07-10当天的教训——见[[project-backtest-methodology-review-20260710]]和[[project-tactical-position-regime-v49]]，先实现后验证的顺序导致"战术仓独立止损公式"白做一轮又要撤回。

**How to apply:** 用户说"优化框架""改止损逻辑""升级第X层"这类话时，应该调用`/update-framework`技能而不是直接动手改文件。
