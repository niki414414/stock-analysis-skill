# Framework Snapshot v4.6
Date: 2026-06-21 15:35
Note: C/D强催化左侧试探(催化≥15+量能≥1.1+空间≥20%优选)+回测验证(440信号,PASS+量能+空间48%胜率3.3x赔率)

## Files
- analysis-prompt-template.md
- output-format-template.md
- SKILL.md
- event_map_updater.py
- event_map_query.py
- signal_scanner.py
- memory/backtest-framework-correction-v42.md
- memory/portfolio-status-20260611.md
- memory/project-stockpool-redesign.md
- memory/trading-framework-checklist.md
- memory/backtest-framework-v43-round2-results.md
- memory/reference_trigger_watchlist.md
- memory/feedback_company_pool_missing.md
- memory/stock-skills-project.md
- memory/MEMORY.md
- memory/backtest-monthly-jan-may-2026.md
- memory/industry-events-map.md
- memory/feedback_output_decision_matrix.md
- memory/backtest-framework-v43-results.md
- memory/feedback_framework_change_method.md
- memory/portfolio-status-20260616.md
- memory/candidate-catalyst-deep-pullback-202606.md
- memory/backtest-framework-v43-round3-results.md
- memory/feedback_data_source.md

## Restore Instructions
1. Copy framework/ files to ~/.claude/skills/stock-analysis/references/
2. Copy skills/SKILL.md to ~/.claude/skills/stock-analysis/
3. Copy scripts/ to respective skill directories
4. Copy memory/ to ~/.claude/projects/-Users-niki/memory/ (or new project path)
5. Run: python3 event_map_updater.py status  # verify data integrity
