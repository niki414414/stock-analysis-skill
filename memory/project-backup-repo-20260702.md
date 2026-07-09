---
name: project-backup-repo-20260702
description: 统一容灾备份仓库已建立——GitHub私库合并四技能+memory+数据地图，换设备/换AI工具的恢复入口
metadata:
  type: project
  originSessionId: unknown
---

## 备份仓库（2026-07-02 建立）

- 地址：`https://github.com/niki414414/stock-analysis-skill.git`（私有，账号 niki414414）
- 本地工作副本：`~/Desktop/tz/repo/`（clone，不是实时同步，需要手动 commit+push 更新）
- 结构：`skills/`（四个技能：stock-analysis/top-picks/update-event-map/update-market-thesis + shared脚本）
  + `memory/`（复制自 `~/.claude/projects/-Users-niki/memory/*.md`）+ `data/`（事件地图tech/non-tech、公司池、研究材料）
- 根目录 `HANDOFF.md`：给非Claude工具（Codex等）接手时读的模型无关说明文档
- `.env`（Tushare token、Server酱 SendKey）**不在仓库内**，走 `.gitignore` 排除，换设备后需单独重建

## 认证方式（重要，下次push前必看）

- git push 认证用的是 GitHub Personal Access Token（classic, repo权限, 90天有效期），已存入 Mac 钥匙串
  （`git credential-osxkeychain`），remote URL 显式带了用户名：`https://niki414414@github.com/...`
- **token 90天后会过期**（约 2026-09-30 前后），届时 push 会重新失败，需要用户重新生成一个新 token 并存入钥匙串
- 之前 Mac 钥匙串里存的是另一个账号 `215377346` 的登录（无这个私库权限），已确认 `niki414414` 才是用户当前常用账号

**Why:** 用户担心 Claude 账户出问题后框架/数据/memory 难以恢复或迁移到其他AI工具接续，此前只靠手动"迁移包"导出到 `~/Desktop/tz/迁移包_latest/`，无版本历史。改用git私库后可一条`git clone`恢复全部内容，且内容全是纯文本，天然模型无关。

## 历史归档（2026-07-02 同日完成）

- 仓库新增 `archive/`：`events-map-tech-history/`（科技事件地图6/21-6/30历史CSV，回测避免未来数据用）
  + `framework-snapshots/v4.6_20260621/`（v4.6完整代码快照）+ `research-materials-history/`（早期研究材料）
- `~/Desktop/tz/迁移包_latest/` 里的独特内容确认已全部归档后，整个文件夹已移入 `~/.Trash/`（可从废纸篓找回，非永久删除）
- `~/Desktop/tz/非科技主线产业事件地图_CSV包_20260617/` 是非科技事件地图的唯一活跃数据源（脚本按文件夹名日期取最新），
  确认6/17后再没真正更新过（迁移包里0701的"新版"实测和0617逐字节相同，只是重新导出）
- 追加评估：`chatgpt迁移包`（内有独立的0618科技事件地图CSV快照，比已归档的最早0621版更早3天，
  已补归档进archive）、`claude-memory`（6/8旧memory快照，完全被当前系统覆盖，无独有信息）、
  `framework_versions`（与archive内容逐字节相同）——三者评估后确认无剩余价值，均已移入`~/.Trash/`

## 维护约定（已达成共识，需要持续执行）

- 不再新建 `迁移包_latest` 之外的新快照文件夹，git log 本身就是版本记录
- 以后每次框架有实质性改动（版本升级/新增回测结论/数据源变更），应在 `~/Desktop/tz/repo/` 里同步最新的
  skills/memory/data 内容并 commit+push（我需要主动做这件事，不能只等用户要求）
- `~/Desktop/tz/迁移包_latest/`、`claude-memory/`、历史版本快照等旧文件夹予以保留但不再维护，价值已被本仓库替代
