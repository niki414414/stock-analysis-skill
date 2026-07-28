---
name: project-local-backup-20260728
description: "tz-codex本地离线迁移包机制：Git bundle + 活动数据 + SHA-256校验，明确排除凭据"
metadata:
  node_type: memory
  type: project
---

## 结论

旧 `event_map_updater.py migrate` 只复制固定的少数技能脚本，容易漏掉新增技能；
历史材料还依赖已经不存在的旧路径，不能作为完整灾备。

新增 `scripts/create_local_backup.py`，与GitHub远端备份形成两层保护：

- `repo-source.zip`：当前HEAD的源码、memory和仓库内数据。
- `stock-analysis-skill.bundle`：完整Git提交、分支和标签，可离线clone。
- `02_活动数据/`：最新事件地图、公司池、研究材料、分析记录和持仓。
- `MANIFEST.json`：Git HEAD、分支、工作树状态和数据目标清单。
- `SHA256SUMS.json`：逐文件大小和SHA-256，生成后自动解压复验。

## 安全边界

- 不包含 `.env`、`.git/config`、日志、缓存或其他凭据文件。
- 迁移后必须根据 `.env.example` 重新配置令牌。
- 默认输出到 `$TZ_CODEX_HOME/迁移归档/`，不覆盖原始 `tz`。
