#!/usr/bin/env python3
"""event_map_updater.py — 产业事件地图写入/导出/迁移工具。

与 event_map_query.py（只读）配合，本脚本负责所有写入操作：
  status    显示当前数据库状态
  apply     从JSON增量更新CSV
  export-excel  从CSV生成合并Excel
  migrate   生成迁移包（科技+非科技+公司池+脚本）
  validate  跨表一致性检查
  reconcile-pool  用最新mapping全量对账并回填公司池
  next-ids  生成下一批事件ID

用法：
  python3 event_map_updater.py status
  python3 event_map_updater.py apply --date 0621 --label weekly --changes /tmp/changes.json
  python3 event_map_updater.py export-excel --output ~/Desktop/tz/技能数据/科技产业事件/621.xlsx
  python3 event_map_updater.py migrate --output ~/Desktop/tz/技能数据/迁移包_20260621
  python3 event_map_updater.py validate
  python3 event_map_updater.py next-ids --table events --count 3
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime

import pandas as pd

# ── 路径常量（与 event_map_query.py 保持一致）─────────────────────────
WORKSPACE_ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
))
REPO_ROOT = os.path.join(WORKSPACE_ROOT, "repo")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
DATA_ROOT = os.path.join(WORKSPACE_ROOT, "技能数据")
TECH_DIR = os.path.join(DATA_ROOT, "科技产业事件")
NONFIN_DIR = os.path.join(DATA_ROOT, "非科技产业事件地图")
COMPANY_POOL = os.path.join(DATA_ROOT, "公司.xlsx")
CODE_MAP_CSV = os.path.join(DATA_ROOT, "company_code_map.csv")
CHATGPT_MIGRATE = os.path.join(DATA_ROOT, "chatgpt迁移包")
SKILL_DIR = os.path.join(REPO_ROOT, "skills", "update-event-map")
QUERY_SCRIPT = os.path.join(REPO_ROOT, "skills", "stock-analysis", "scripts", "event_map_query.py")
SECTORS_STATUS_CROSSWALK_CSV = os.path.join(SKILL_DIR, "config", "sectors_status_crosswalk.csv")

# 用户2026-07-23明确表态不参与北交所，公司池同步时排除掉（不构建/维护BJ的ts_code后缀支持）
BJ_CODE_PREFIXES = ("83", "87", "88", "92", "43")

# ── 科技版列定义 ─────────────────────────────────────────────────────
TECH_TABLES = {
    "events": {
        "prefix": "events",
        "id_col": "事件ID",
        "cols": ["事件ID", "一级赛道", "二级事件", "事件名称", "事件时间", "当前状态",
                 "重要程度", "影响周期", "市场关注度", "信息来源类型", "来源URL",
                 "是否已被交易", "是否存在预期差", "主要影响方向", "后续观察指标",
                 "最新更新时间", "备注"],
    },
    "mapping": {
        "prefix": "mapping",
        "id_col": "映射ID",
        "cols": ["映射ID", "事件ID", "事件名称", "一级赛道", "一级产业", "二级环节",
                 "三级零部件/材料/设备", "代表公司/公司类型", "当前市场关注度",
                 "是否已炒作", "预期差", "验证指标", "风险点", "最新更新时间", "来源"],
    },
    "forward": {
        "prefix": "forward",
        "id_col": "前瞻ID",
        "cols": ["前瞻ID", "事件", "时间", "发生概率", "重要程度", "一级赛道",
                 "可能受益方向", "可能受损方向", "是否已被交易", "预期差",
                 "后续观察指标", "事件窗口", "更新时间", "来源"],
    },
    "corrections": {
        "prefix": "corrections",
        "id_col": None,
        "cols": ["更新日期", "问题", "判断", "对应事件ID", "后续动作", "优先级", "状态", "备注"],
    },
    "signals_early": {
        "prefix": "signals_early",
        "id_col": "signal_id",
        "cols": ["signal_id", "signal_date", "event_id", "对应事件", "signal_stage",
                 "早期信号内容", "产业链环节", "代表公司/公司类型", "later_validation",
                 "market_trade_status", "confidence_level", "source_type", "source_ref",
                 "风险/修正说明", "最新更新时间"],
    },
    "reviews": {
        "prefix": "reviews",
        "id_col": None,
        "cols": ["事件", "事件时间", "一级赛道", "事件状态", "第一阶段最先上涨方向",
                 "第二阶段扩散方向", "第三阶段影子/补涨方向", "持续周期", "有效方向",
                 "退潮信号", "最终结论", "更新时间"],
    },
    "sources": {
        "prefix": "sources",
        "id_col": "来源ID",
        "cols": ["来源ID", "日期", "来源类型", "标题/内容", "URL", "对应事件",
                 "可靠性", "使用方式", "备注", "更新时间"],
    },
    "weekly": {
        "prefix": "weekly",
        "id_col": None,
        "cols": ["章节", "核心结论", "关键事件/方向", "市场交易状态", "预期差",
                 "历史相似案例", "需要跟踪的指标", "风险提示"],
    },
    "sectors_status": {
        "prefix": "sectors_status",
        "id_col": "sector_id",
        # 2026-07-22精简：删除wave_number/wave_position/catalyst_quality/
        # ai_correlation/priority/style_position——排查发现这6个字段在现行框架里
        # 零消费方（只被已归档的analysis-prompt-template-v2.md和已下线的
        # top_picks_screener.py引用，见feedback_sectors_status_sync_redesign）。
        # stage保留：output-format-template.md仍用它做展示+过时性自查（非决策输入，
        # analysis-prompt-template.md的破位升级路径①已改用corrections表+活跃催化）。
        "cols": ["sector_id", "theme", "sub_sector", "stage",
                 "signal_stock_code", "event_map_status", "last_updated", "note"],
    },
}

NONFIN_TABLES = {
    "events": {
        "prefix": "events",
        "id_col": "事件ID",
        "cols": ["事件ID", "一级赛道", "二级事件", "事件名称", "事件时间", "当前状态",
                 "重要程度", "是否已被交易", "是否存在预期差", "主要影响方向",
                 "后续观察指标", "备注"],
    },
    "mapping": {
        "prefix": "mapping",
        "id_col": None,
        "cols": ["事件ID", "一级赛道", "二级事件", "产业链位置", "受益方向",
                 "代表公司类型", "弹性来源", "风险点", "备注"],
    },
    "forward": {
        "prefix": "forward",
        "id_col": None,
        "cols": ["事件ID", "观察周期", "关键日期/窗口", "观察指标", "验证逻辑",
                 "可能结果", "跟踪优先级", "备注"],
    },
    "sources": {
        "prefix": "sources",
        "id_col": "来源ID",
        "cols": ["来源ID", "日期", "来源类型", "标题/内容", "URL", "对应事件", "可靠性"],
    },
}

EXCEL_SHEET_ORDER_TECH = [
    ("00_使用说明", None),
    ("01_总览仪表盘", None),
    ("02_事件总库", "events"),
    ("03_产业映射树", "mapping"),
    ("04_事件复盘库", "reviews"),
    ("05_未来12个月前瞻", "forward"),
    ("06_周报模板", "weekly"),
    ("07_动态修正清单", "corrections"),
    ("08_来源与调研记录", "sources"),
    ("10_早期信号追踪", "signals_early"),
]


# ═══════════════════════════════════════════════════════════════════════
# PART 1: 基础工具
# ═══════════════════════════════════════════════════════════════════════

def find_latest_csv_dir(source="tech"):
    if source == "tech":
        best_date, best_dir = None, None
        for d in glob.glob(os.path.join(TECH_DIR, "csv*")):
            if not os.path.isdir(d):
                continue
            for f in glob.glob(os.path.join(d, "*_*.csv")):
                m = re.search(r"_(\d{8})", os.path.basename(f))
                if m and (best_date is None or m.group(1) > best_date):
                    best_date, best_dir = m.group(1), d
        if best_dir is None:
            raise FileNotFoundError(f"未找到科技事件地图csv目录: {TECH_DIR}")
        return best_dir
    else:
        candidates = glob.glob(os.path.join(NONFIN_DIR, "非科技主线产业事件地图_*"))
        candidates = [c for c in candidates if os.path.isdir(c)]
        if not candidates:
            raise FileNotFoundError(f"未找到非科技事件地图目录: {NONFIN_DIR}")
        def _date(p):
            m = re.search(r"(\d{8})$", os.path.basename(p))
            return m.group(1) if m else "0"
        return sorted(candidates, key=_date)[-1]


def load_csv(csv_dir, prefix):
    matches = glob.glob(os.path.join(csv_dir, f"{prefix}_*.csv"))
    if not matches:
        return None, None
    f = sorted(matches)[-1]
    return pd.read_csv(f), f


def get_tables(source="tech"):
    return TECH_TABLES if source == "tech" else NONFIN_TABLES


def _extract_date_from_dir(csv_dir):
    for f in glob.glob(os.path.join(csv_dir, "*_*.csv")):
        m = re.search(r"_(\d{8})", os.path.basename(f))
        if m:
            return m.group(1)
    bn = os.path.basename(csv_dir)
    m = re.search(r"(\d{3,8})", bn)
    if m:
        val = m.group(1)
        if len(val) <= 4:
            return f"2026{val.zfill(4)}"
        return val
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════
# PART 2: status
# ═══════════════════════════════════════════════════════════════════════

def cmd_status(source="tech"):
    csv_dir = find_latest_csv_dir(source)
    date = _extract_date_from_dir(csv_dir)
    tables = get_tables(source)
    info = {"csv_dir": csv_dir, "date": date, "source": source, "tables": {}}

    for tname, tdef in tables.items():
        df, fpath = load_csv(csv_dir, tdef["prefix"])
        if df is not None:
            info["tables"][tname] = {"rows": len(df), "cols": len(df.columns), "file": fpath}
        else:
            info["tables"][tname] = {"rows": 0, "cols": 0, "file": None}

    print(f"当前主库: {os.path.basename(csv_dir)}  日期: {date}  源: {source}")
    print(f"路径: {csv_dir}\n")
    for tname, tinfo in info["tables"].items():
        status = f"{tinfo['rows']}行 {tinfo['cols']}列" if tinfo["file"] else "缺失"
        print(f"  {tname:<18} {status}")
    return info


# ═══════════════════════════════════════════════════════════════════════
# PART 3: apply
# ═══════════════════════════════════════════════════════════════════════

def create_new_version(source, date_short, label):
    latest_dir = find_latest_csv_dir(source)
    if source == "tech":
        new_dir = os.path.join(TECH_DIR, f"csv{date_short}")
    else:
        full_date = f"2026{date_short}" if len(date_short) == 4 else date_short
        new_dir = os.path.join(NONFIN_DIR, f"非科技主线产业事件地图_CSV包_{full_date}")

    if os.path.exists(new_dir):
        print(f"版本目录已存在: {new_dir}，将在现有文件上追加")
        return new_dir

    os.makedirs(new_dir)
    full_date = f"2026{date_short}" if len(date_short) == 4 else date_short

    for f in glob.glob(os.path.join(latest_dir, "*.csv")):
        bn = os.path.basename(f)
        old_date_match = re.search(r"_(\d{8})", bn)
        if old_date_match:
            new_bn = bn[:old_date_match.start(1)] + full_date + bn[old_date_match.end(1):]
            suffix_match = re.search(r"_\d{8}_(.+)\.csv$", bn)
            if suffix_match:
                new_bn = bn[:old_date_match.start(1)] + full_date + f"_{label}.csv"
            else:
                new_bn = bn[:old_date_match.start(1)] + full_date + f"_{label}.csv"
        else:
            new_bn = bn
        shutil.copy2(f, os.path.join(new_dir, new_bn))

    for f in glob.glob(os.path.join(latest_dir, "*.txt")):
        shutil.copy2(f, new_dir)

    print(f"新版本创建: {new_dir} (基于 {os.path.basename(latest_dir)})")
    return new_dir


def _autofill_ids(tname, tdef, existing_df, new_df, date_short):
    """兜底：changes.json里漏填主键ID的新增行，在写入前自动补上，不再依赖调用方记得填。

    2026-07-14/15同一session里连续三次漏填映射ID导致空主键行污染validate输出
    （见memory: feedback_event_map_id_field_bug），这里把兜底直接写死在apply()里，
    而不是继续指望prompt提醒。
    """
    id_col = tdef.get("id_col")
    if not id_col or id_col not in new_df.columns:
        return new_df

    blank_mask = new_df[id_col].isna() | (new_df[id_col].astype(str).str.strip() == "")
    if not blank_mask.any():
        return new_df

    existing_ids = set(existing_df[id_col].astype(str).tolist()) if existing_df is not None and not existing_df.empty else set()
    existing_ids |= set(new_df.loc[~blank_mask, id_col].astype(str).tolist())

    # date_short可能是"0715"(4位)或"20260715"(8位)，统一成完整8位日期，
    # 跟create_new_version()里full_date的算法保持一致，否则生成的ID格式对不上现有约定
    full_date = f"2026{date_short}" if len(date_short) == 4 else date_short

    if tname == "events":
        for idx in new_df[blank_mask].index:
            sector = new_df.at[idx, "一级赛道"] if "一级赛道" in new_df.columns else None
            prefix = SECTOR_ID_PREFIX.get(sector, "EVT")
            year = full_date[:4]
            pattern = re.compile(rf"^{prefix}-{year}-(\d+)$")
            max_num = max([int(m.group(1)) for eid in existing_ids
                           if (m := pattern.match(eid))], default=0)
            new_id = f"{prefix}-{year}-{str(max_num + 1).zfill(3)}"
            new_df.at[idx, id_col] = new_id
            existing_ids.add(new_id)
    else:
        prefix_map = {"mapping": "MAP", "forward": "FWD", "sources": "SRC", "signals_early": "SIG"}
        pfx = prefix_map.get(tname, tname.upper())
        pattern = re.compile(rf"^{pfx}-{full_date}-(\d+)$")
        for idx in new_df[blank_mask].index:
            max_num = max([int(m.group(1)) for eid in existing_ids
                           if (m := pattern.match(eid))], default=0)
            new_id = f"{pfx}-{full_date}-{str(max_num + 1).zfill(3)}"
            new_df.at[idx, id_col] = new_id
            existing_ids.add(new_id)

    filled = blank_mask.sum()
    print(f"  [自动补ID] {tname}: {filled}行漏填{id_col}，已自动生成")
    return new_df


def cmd_apply(source, date_short, label, changes_path):
    with open(changes_path, encoding="utf-8") as f:
        changes = json.load(f)

    new_dir = create_new_version(source, date_short, label)
    tables = get_tables(source)
    summary = {}

    additions = changes.get("additions", {})
    for tname, new_rows in additions.items():
        if not new_rows or tname not in tables:
            continue
        tdef = tables[tname]
        df, fpath = load_csv(new_dir, tdef["prefix"])
        if df is None:
            df = pd.DataFrame(columns=tdef["cols"])
            fpath = os.path.join(new_dir, f"{tdef['prefix']}_{date_short}_{label}.csv")

        new_df = pd.DataFrame(new_rows)
        for col in tdef["cols"]:
            if col not in new_df.columns:
                new_df[col] = ""
        new_df = new_df[[c for c in tdef["cols"] if c in new_df.columns]]
        new_df = _autofill_ids(tname, tdef, df, new_df, date_short)

        df = pd.concat([df, new_df], ignore_index=True)
        df.to_csv(fpath, index=False, encoding="utf-8-sig")
        summary[tname] = summary.get(tname, {"added": 0, "updated": 0})
        summary[tname]["added"] += len(new_rows)

    updates = changes.get("updates", {})
    for tname, update_list in updates.items():
        if not update_list or tname not in tables:
            continue
        tdef = tables[tname]
        id_col = tdef.get("id_col")
        if not id_col:
            print(f"  警告: {tname} 无主键列，跳过update操作")
            continue
        df, fpath = load_csv(new_dir, tdef["prefix"])
        if df is None:
            continue

        for upd in update_list:
            id_val = upd.get("id_value")
            fields = upd.get("fields", {})
            mask = df[id_col].astype(str) == str(id_val)
            if mask.sum() == 0:
                print(f"  警告: {tname} 中未找到 {id_col}={id_val}")
                continue
            for col, val in fields.items():
                if col in df.columns:
                    df.loc[mask, col] = val
            summary[tname] = summary.get(tname, {"added": 0, "updated": 0})
            summary[tname]["updated"] += 1

        df.to_csv(fpath, index=False, encoding="utf-8-sig")

    print(f"\n变更已应用到: {new_dir}")
    for tname, s in summary.items():
        print(f"  {tname}: +{s['added']}新增, ~{s['updated']}更新")

    # 公司池同步：不再是SKILL.md里"记得手动做"的一步，apply时自动跑。
    pool_summary = sync_company_pool(source, mapping_rows=additions.get("mapping"), date_short=date_short)
    if pool_summary["added"] or pool_summary["skipped_no_code"]:
        print(f"\n公司池同步: +{pool_summary['added']}家新增, "
              f"{len(pool_summary['skipped_no_code'])}家无A股代码跳过"
              f"{'(' + '、'.join(pool_summary['skipped_no_code'][:5]) + ')' if pool_summary['skipped_no_code'] else ''}")

    # sectors_status同步：2026-07-22新增，替代此前"改事件地图时记得手动同步
    # sectors_status.csv"这一步——那一步从2026-06-18写进设计文档起就只是prompt
    # 提醒，从未真正代码强制过，导致76个赛道里62个的signal_stock_code/
    # event_map_status从建档起就没更新过。sectors_status目前只有tech source。
    if source == "tech":
        sectors_summary = sync_sectors_status(
            mapping_rows=additions.get("mapping"),
            events_rows=additions.get("events"),
            date_short=date_short,
            new_dir=new_dir,
        )
        if sectors_summary["updated_sectors"]:
            print(f"\nsectors_status同步: {len(sectors_summary['updated_sectors'])}个赛道"
                  f"自动补充signal_stock_code/event_map_status "
                  f"({'、'.join(sectors_summary['updated_sectors'])})")
        if sectors_summary["flagged_for_review"]:
            print(f"  stage/wave/priority为判断字段，不自动改，以下赛道有新催化命中，建议人工复核：")
            for f in sectors_summary["flagged_for_review"]:
                print(f"    {f['sector_id']}（{f['sub_sector']}）当前stage={f['current_stage']}"
                      f" 匹配公司:{f['matched_companies']}")

    # 影子期：CSV仍是写入主源，每次apply后自动重建SQLite投影。
    # 失败只告警，不回滚已经验证过的CSV更新；切换为SQLite主库前再收紧为事务级失败。
    try:
        from skills.shared.event_store import build_shadow_database, record_shadow_update_cycle
        db_audit = build_shadow_database()
        migration_history = record_shadow_update_cycle(db_audit, {
            "source": source, "date_short": date_short, "label": label,
            "changes_path": os.path.abspath(changes_path),
        })
        print(f"\nSQLite影子库: ✓ 已重建 "
              f"(events={db_audit['counts'].get('events', 0)}, "
              f"company_event_links={db_audit['counts'].get('company_event_links', 0)}, "
              f"anomalies={db_audit['counts'].get('import_anomalies', 0)})")
        print(f"迁移观察轮次: {migration_history['completed_unique_cycles']}/"
              f"{migration_history['required_unique_cycles']}（仅唯一材料快照计数）")
    except Exception as exc:
        print(f"\nSQLite影子库: ⚠️ 重建失败，不影响CSV主库: {exc}")

    return new_dir, summary


# ═══════════════════════════════════════════════════════════════════════
# PART 3b: 公司池同步（原STEP 5.5，从prompt指令改为代码函数，2026-07-10）
# ═══════════════════════════════════════════════════════════════════════

def _pool_role_key(name, event_id, sector, sub2, sub3):
    """公司池按“公司×事件×产业角色”去重，保留同一公司的多重产业定位。"""
    return tuple(str(x or "").strip() for x in (name, event_id, sector, sub2, sub3))


def _extract_known_companies(rep, code_dict):
    """优先用代码表做实体识别，兼容“定位样本：A、B”和带说明文字的代表公司字段。"""
    text = str(rep or "")
    matched = [name for name in code_dict if name and name in text]
    if matched:
        # 长名称优先，避免简称恰好是另一公司名称子串；保持文本出现顺序。
        matched = sorted(set(matched), key=lambda name: (text.find(name), -len(name)))
        return matched
    return [
        name.strip() for name in re.split(r'[、，,;；/]', text)
        if len(name.strip()) >= 2
    ]


def sync_company_pool(source="tech", mapping_rows=None, date_short=None):
    """从新增mapping行的"代表公司/公司类型"字段抽取公司名，自动补入公司.xlsx缺失的公司。

    唯一的公司池写入入口——不再依赖SKILL.md里"记得手动检查"这一步。
    返回 {"added": int, "already_exists": int, "skipped_no_code": [names]}。
    """
    empty_result = {"added": 0, "already_exists": 0, "skipped_no_code": []}
    if not mapping_rows:
        return empty_result
    if not os.path.exists(CODE_MAP_CSV) or not os.path.exists(COMPANY_POOL):
        return empty_result

    code_map_df = pd.read_csv(CODE_MAP_CSV)
    code_dict = dict(zip(code_map_df["name"], code_map_df["code"].astype(str).str.zfill(6)))

    sheet_name = "科技公司池" if source == "tech" else "非科技公司池"
    da_lei = "科技" if source == "tech" else "非科技"

    existing = pd.read_excel(COMPANY_POOL, sheet_name=sheet_name)
    role_cols = ["公司名称", "一级赛道", "二级环节", "三级环节/定位", "关联事件ID"]
    before_dedupe = len(existing)
    existing = existing.drop_duplicates(subset=role_cols, keep="first").reset_index(drop=True)
    deduped = before_dedupe - len(existing)
    existing_names = set(existing["公司名称"].astype(str))
    existing_role_keys = set()
    for _, old in existing.iterrows():
        old_name = str(old.get("公司名称", "") or "").strip()
        old_ids = re.findall(r'[A-Z][A-Z0-9-]*-\d{4}-\d{3}', str(old.get("关联事件ID", "") or ""))
        if not old_ids:
            old_ids = [""]
        for old_id in old_ids:
            existing_role_keys.add(_pool_role_key(
                old_name, old_id, old.get("一级赛道", ""), old.get("二级环节", ""),
                old.get("三级环节/定位", ""),
            ))

    new_rows = []
    skipped = []
    already = 0
    batch_label = date_short or datetime.now().strftime("%m%d")

    for row in mapping_rows:
        # tech的mapping表列名是"代表公司/公司类型"，nonfin是"代表公司类型"(无斜杠)。
        # 之前硬编码只读tech那个名字，nonfin的mapping新增行代表公司字段永远读成空，
        # sync_company_pool对nonfin一直是静默失效的——不报错，就是不加，很难发现。
        rep = str(row.get("代表公司/公司类型") or row.get("代表公司类型") or "")
        if not rep or rep == "nan":
            continue
        event_id = str(row.get("事件ID", "") or "")
        sector = row.get("一级赛道", "")
        sub2 = row.get("二级环节", "")
        sub3 = row.get("三级零部件/材料/设备", "")
        for name in _extract_known_companies(rep, code_dict):
            if not name or len(name) < 2:
                continue
            role_key = _pool_role_key(name, event_id, sector, sub2, sub3)
            if role_key in existing_role_keys:
                already += 1
                continue
            code = code_dict.get(name)
            if code and code.startswith(BJ_CODE_PREFIXES):
                code = None  # 北交所不参与，不纳入公司池
            if not code:
                skipped.append(name)
                continue
            new_rows.append({
                "大类":         da_lei,
                "一级赛道":     sector,
                "二级环节":     sub2,
                "三级环节/定位": sub3,
                "公司名称":     name,
                "市场/属性":    "A股",
                "角色":        "代表公司/公司类型",
                "来源批次":     f"{batch_label}公司池自动同步",
                "关联事件ID":   row.get("事件ID", ""),
                "入库建议":     "mapping自动同步",
                "置信度":      "中",
                "备注":        "",
            })
            existing_names.add(name)
            existing_role_keys.add(role_key)  # 防止本批次内重复添加同一角色

    if new_rows or deduped:
        combined = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
        with pd.ExcelWriter(COMPANY_POOL, engine="openpyxl", mode="a",
                            if_sheet_exists="replace") as writer:
            combined.to_excel(writer, sheet_name=sheet_name, index=False)

    return {"added": len(new_rows), "already_exists": already,
            "deduped": deduped, "skipped_no_code": skipped}


def reconcile_company_pool(source="tech", sector=None):
    """用最新mapping对账公司池，修复历史mapping未触发增量同步造成的缺口。"""
    latest_dir = find_latest_csv_dir(source)
    prefix = get_tables(source)["mapping"]["prefix"]
    mapping_df, _ = load_csv(latest_dir, prefix)
    if mapping_df is None:
        raise FileNotFoundError(f"未找到{source} mapping表")
    if sector:
        mapping_df = mapping_df[
            mapping_df["一级赛道"].astype(str).str.contains(sector, na=False)
        ]
    rows = mapping_df.fillna("").to_dict("records")
    result = sync_company_pool(source, mapping_rows=rows, date_short=datetime.now().strftime("%m%d"))
    result["mapping_rows_scanned"] = len(rows)
    result["sector"] = sector or "全量"
    return result


# ═══════════════════════════════════════════════════════════════════════
# PART 3c: sectors_status.csv同步（2026-07-22新增）
# ═══════════════════════════════════════════════════════════════════════

def sync_sectors_status(mapping_rows=None, events_rows=None, date_short=None, new_dir=None):
    """把本次apply新增的mapping/events行，通过关键词对照表(config/sectors_status_crosswalk.csv)
    匹配到sectors_status.csv对应的sector_id，自动补充signal_stock_code(与已有内容合并，不覆盖)
    和event_map_status(记录最新关联事件ID)。

    stage/wave_number/wave_position/priority这几个字段是产业阶段判断，机器不下这个判断，
    永远不自动写——命中匹配时只打印提醒清单，人工决定要不要改。

    Why: sectors_status.csv 2026-06-18建档时设计成"用户更新事件地图时，同步更新对应赛道
    的stage和event_map_status"，但这一步从来只是文档里的一句话，从未写进代码强制执行，
    76个赛道里62个signal_stock_code/event_map_status从建档起就没再更新过——跟公司池
    在2026-07-10前的问题是同一类("新逻辑接上、旧流程没有代码强制")。这次直接把"能自动
    判断的部分"(公司代码关联)代码化，"需要人工判断的部分"(阶段/优先级)保留人工，但通过
    打印提醒让它不再"悄悄过期"。

    只对tech source生效——sectors_status目前是tech专属表，nonfin没有对应表。
    """
    empty = {"updated_sectors": [], "flagged_for_review": []}
    if not mapping_rows and not events_rows:
        return empty
    if not os.path.exists(SECTORS_STATUS_CROSSWALK_CSV) or new_dir is None:
        return empty

    cross = pd.read_csv(SECTORS_STATUS_CROSSWALK_CSV)
    cross_map = {r["sector_id"]: str(r["keywords"]).split("|") for _, r in cross.iterrows()}

    df, fpath = load_csv(new_dir, "sectors_status")
    if df is None:
        return empty

    code_dict = {}
    if os.path.exists(CODE_MAP_CSV):
        code_map_df = pd.read_csv(CODE_MAP_CSV)
        code_dict = dict(zip(code_map_df["name"], code_map_df["code"].astype(str).str.zfill(6)))

    def searchable_text(row, cols):
        return " ".join(str(row.get(c, "") or "") for c in cols)

    sector_matches = {}  # sector_id -> {"companies": set(), "event_ids": set()}

    for row in (mapping_rows or []):
        text = searchable_text(row, ["一级赛道", "二级环节", "三级零部件/材料/设备"])
        rep = str(row.get("代表公司/公司类型") or "")
        event_id = str(row.get("事件ID", "") or "")
        for sid, kws in cross_map.items():
            if any(kw and kw in text for kw in kws):
                m = sector_matches.setdefault(sid, {"companies": set(), "event_ids": set()})
                if event_id and event_id != "nan":
                    m["event_ids"].add(event_id)
                for name in _extract_known_companies(rep, code_dict):
                    if len(name) >= 2:
                        m["companies"].add(name)

    for row in (events_rows or []):
        text = searchable_text(row, ["一级赛道", "二级事件", "事件名称"])
        event_id = str(row.get("事件ID", "") or "")
        for sid, kws in cross_map.items():
            if any(kw and kw in text for kw in kws):
                m = sector_matches.setdefault(sid, {"companies": set(), "event_ids": set()})
                if event_id and event_id != "nan":
                    m["event_ids"].add(event_id)

    updated = []
    flagged = []
    full_date = f"2026{date_short}" if date_short and len(date_short) == 4 else date_short
    last_updated_val = f"{full_date[:4]}-{full_date[4:6]}-{full_date[6:]}" if full_date and len(full_date) == 8 \
        else datetime.now().strftime("%Y-%m-%d")

    for sid, m in sector_matches.items():
        mask = df["sector_id"] == sid
        if mask.sum() == 0:
            continue
        idx = df.index[mask][0]
        changed = False

        existing_val = df.at[idx, "signal_stock_code"]
        existing_codes = str(existing_val) if pd.notna(existing_val) else ""
        existing_set = {c.strip() for c in existing_codes.split(";") if c.strip() and c.strip() != "nan"}
        new_codes = {code_dict[name] for name in m["companies"] if name in code_dict}
        merged = existing_set | new_codes
        if merged and merged != existing_set:
            df.at[idx, "signal_stock_code"] = ";".join(sorted(merged))
            changed = True

        if m["event_ids"]:
            df.at[idx, "event_map_status"] = f"已关联:{','.join(sorted(m['event_ids']))}"
            changed = True

        if changed:
            df.at[idx, "last_updated"] = last_updated_val
            updated.append(sid)

        flagged.append({
            "sector_id": sid,
            "sub_sector": df.at[idx, "sub_sector"] if "sub_sector" in df.columns else "",
            "current_stage": df.at[idx, "stage"] if "stage" in df.columns else "",
            "matched_companies": sorted(m["companies"]),
        })

    if updated:
        df.to_csv(fpath, index=False, encoding="utf-8-sig")

    return {"updated_sectors": updated, "flagged_for_review": flagged}


# ═══════════════════════════════════════════════════════════════════════
# PART 4: next-ids
# ═══════════════════════════════════════════════════════════════════════

SECTOR_ID_PREFIX = {
    "AI算力": "AI", "半导体": "SEMI", "存储": "MEM", "机器人": "ROBOT",
    "电力与算电": "POWER", "光通信与高速连接": "COMM", "先进封装": "GLASS",
    "商业航天": "SPACE", "低空经济": "LOWALT", "被动元件": "MLCC",
    "新材料": "MAT", "AI应用": "AIAPP", "消费电子与端侧AI": "EDGE",
    "电池储能": "BAT", "固态电池": "SSB",
}


def cmd_next_ids(source, table, count=5, sector=None, date=None):
    csv_dir = find_latest_csv_dir(source)
    tables = get_tables(source)
    tdef = tables.get(table)
    if not tdef or not tdef.get("id_col"):
        print(f"表 {table} 无ID列")
        return []

    df, _ = load_csv(csv_dir, tdef["prefix"])
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    id_col = tdef["id_col"]
    existing_ids = set(df[id_col].astype(str).tolist()) if df is not None else set()

    if table == "events":
        prefix = SECTOR_ID_PREFIX.get(sector, "EVT")
        year = date[:4]
        pattern = re.compile(rf"^{prefix}-{year}-(\d+)$")
        max_num = 0
        for eid in existing_ids:
            m = pattern.match(eid)
            if m:
                max_num = max(max_num, int(m.group(1)))
        ids = [f"{prefix}-{year}-{str(max_num + i + 1).zfill(3)}" for i in range(count)]
    elif table in ("mapping", "forward", "sources", "signals_early"):
        prefix_map = {
            "mapping": "MAP", "forward": "FWD",
            "sources": "SRC", "signals_early": "SIG",
        }
        pfx = prefix_map[table]
        pattern = re.compile(rf"^{pfx}-{date}-(\d+)$")
        max_num = 0
        for eid in existing_ids:
            m = pattern.match(eid)
            if m:
                max_num = max(max_num, int(m.group(1)))
        ids = [f"{pfx}-{date}-{str(max_num + i + 1).zfill(3)}" for i in range(count)]
    else:
        ids = [f"{table.upper()}-{date}-{str(i + 1).zfill(3)}" for i in range(count)]

    for id_val in ids:
        print(id_val)
    return ids


# ═══════════════════════════════════════════════════════════════════════
# PART 5: validate
# ═══════════════════════════════════════════════════════════════════════

def cmd_validate(source="tech", strict_refs=False):
    csv_dir = find_latest_csv_dir(source)
    tables = get_tables(source)
    warnings = []
    reference_notes = []

    events_df, _ = load_csv(csv_dir, "events")
    if events_df is None:
        warnings.append("events表缺失")
        print(f"验证完成: {len(warnings)} 个问题")
        for w in warnings:
            print(f"  ⚠️ {w}")
        return warnings

    if strict_refs:
        event_ids = set(
            events_df["事件ID"].dropna().astype(str).str.strip().tolist()
        )
        for tname in ("mapping", "forward", "signals_early"):
            if tname not in tables:
                continue
            tdef = tables[tname]
            df, _ = load_csv(csv_dir, tdef["prefix"])
            if df is None:
                continue
            eid_col = "事件ID" if "事件ID" in df.columns else "event_id"
            if eid_col not in df.columns:
                continue
            for _, row in df.iterrows():
                ref_ids = re.split(r"[;,，；]", str(row[eid_col]))
                for rid in ref_ids:
                    rid = rid.strip()
                    if rid and rid != "nan" and rid not in event_ids:
                        reference_notes.append(
                            f"{tname}.{eid_col}={rid} 未进入events主表"
                        )

    for tname, tdef in tables.items():
        id_col = tdef.get("id_col")
        if not id_col:
            continue
        df, _ = load_csv(csv_dir, tdef["prefix"])
        if df is None:
            continue
        ids = df[id_col]
        valid_ids = ids.notna() & ids.astype(str).str.strip().ne("")
        dups = df[valid_ids & ids.duplicated(keep=False)]
        if not dups.empty:
            dup_ids = dups[id_col].unique().tolist()
            warnings.append(f"{tname} 有重复ID: {dup_ids[:5]}")

    if "是否已被交易" in events_df.columns:
        valid_traded = {"充分交易", "部分交易", "初步交易", "未充分交易", "未交易"}
        for _, row in events_df.iterrows():
            val = str(row.get("是否已被交易", ""))
            base = val.split("/")[0].strip()
            if base and base not in valid_traded and base != "nan":
                pass

    print(f"验证完成（{os.path.basename(csv_dir)}）: ", end="")
    if not warnings:
        print("✓ 全部通过")
    else:
        print(f"{len(warnings)} 个问题")
        for w in warnings[:20]:
            print(f"  ⚠️ {w}")
    if strict_refs:
        unique_notes = list(dict.fromkeys(reference_notes))
        print(f"严格引用检查: {len(unique_notes)} 个候选/外部引用")
        for note in unique_notes[:20]:
            print(f"  ℹ️ {note}")
    return warnings


# ═══════════════════════════════════════════════════════════════════════
# PART 6: export-excel
# ═══════════════════════════════════════════════════════════════════════

def cmd_export_excel(source="tech", csv_dir=None, output_path=None):
    if csv_dir is None:
        csv_dir = find_latest_csv_dir(source)
    date = _extract_date_from_dir(csv_dir)
    tables = get_tables(source)

    if output_path is None:
        if source == "tech":
            output_path = os.path.join(TECH_DIR, f"{date[-4:]}.xlsx")
        else:
            output_path = os.path.join(NONFIN_DIR, f"非科技事件地图_{date}.xlsx")

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        if source == "tech":
            readme_df = pd.DataFrame({
                "科技产业事件地图": [
                    f"版本日期: {date}",
                    f"CSV来源: {os.path.basename(csv_dir)}",
                    f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    "",
                    "表说明:",
                    "02_事件总库 — 已验证的核心产业事件",
                    "03_产业映射树 — 事件→产业链→环节→公司类型",
                    "05_未来12个月前瞻 — 催化窗口与概率评估",
                    "10_早期信号追踪 — 传闻/研报/未验证线索",
                    "07_动态修正清单 — 需要纠偏或降权的判断",
                    "08_来源与调研记录 — 信息来源追溯",
                    "04_事件复盘库 — 已完成事件的多阶段复盘",
                    "06_周报模板 — 本周核心结论",
                ]
            })
            readme_df.to_excel(writer, sheet_name="00_使用说明", index=False)

            summary_data = {"指标": [], "值": []}
            for tname, tdef in tables.items():
                df, _ = load_csv(csv_dir, tdef["prefix"])
                rows = len(df) if df is not None else 0
                summary_data["指标"].append(f"{tname}行数")
                summary_data["值"].append(rows)
            summary_data["指标"].extend(["版本日期", "导出时间"])
            summary_data["值"].extend([date, datetime.now().strftime("%Y-%m-%d %H:%M")])
            pd.DataFrame(summary_data).to_excel(writer, sheet_name="01_总览仪表盘", index=False)

            sheet_map = {
                "events": "02_事件总库",
                "mapping": "03_产业映射树",
                "reviews": "04_事件复盘库",
                "forward": "05_未来12个月前瞻",
                "weekly": "06_周报模板",
                "corrections": "07_动态修正清单",
                "sources": "08_来源与调研记录",
                "signals_early": "10_早期信号追踪",
            }
        else:
            sheet_map = {
                "events": "事件总库",
                "mapping": "产业链映射",
                "forward": "前瞻验证",
                "sources": "来源记录",
            }

        for tname, sheet_name in sheet_map.items():
            if tname not in tables:
                continue
            tdef = tables[tname]
            df, _ = load_csv(csv_dir, tdef["prefix"])
            if df is not None:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
            else:
                pd.DataFrame(columns=tdef["cols"]).to_excel(
                    writer, sheet_name=sheet_name, index=False
                )

    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
        wb = load_workbook(output_path)
        for ws in wb.worksheets:
            ws.freeze_panes = "A2"
            for col_idx, col_cells in enumerate(ws.columns, 1):
                max_len = 0
                for cell in col_cells[:50]:
                    if cell.value:
                        max_len = max(max_len, len(str(cell.value)))
                ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 50)
        wb.save(output_path)
    except Exception:
        pass

    print(f"Excel已导出: {output_path}")
    return output_path


# ═══════════════════════════════════════════════════════════════════════
# PART 7: migrate
# ═══════════════════════════════════════════════════════════════════════

def cmd_migrate(output_dir=None):
    date = datetime.now().strftime("%Y%m%d")
    if output_dir is None:
        output_dir = os.path.join(DATA_ROOT, f"迁移包_{date}")

    # 每次全新生成，不在旧目录上叠加——否则历史版本的目录结构/已删除脚本的残留
    # 会一直躺在里面（曾发现05_技能代码/top-picks_references是06-30遗留，早已过期）。
    if os.path.isdir(output_dir):
        shutil.rmtree(output_dir)

    dirs = [
        os.path.join(output_dir, "01_项目主索引_README"),
        os.path.join(output_dir, "02_科技主线数据库"),
        os.path.join(output_dir, "03_非科技主线数据库"),
        os.path.join(output_dir, "04_代表公司分类池"),
        os.path.join(output_dir, "05_历史材料"),
        os.path.join(output_dir, "06_版本记录"),
        os.path.join(output_dir, "07_技能代码"),
        os.path.join(output_dir, "08_框架版本快照"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)

    # 02: 科技主线
    try:
        tech_dir = find_latest_csv_dir("tech")
        tech_excel = cmd_export_excel("tech", tech_dir,
                                       os.path.join(output_dir, "02_科技主线数据库",
                                                    f"科技产业事件地图_{date}.xlsx"))
        zip_path = os.path.join(output_dir, "02_科技主线数据库", f"CSV包_{date}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in glob.glob(os.path.join(tech_dir, "*")):
                zf.write(f, os.path.basename(f))
        print(f"科技CSV包: {zip_path}")
    except Exception as e:
        print(f"科技主线打包失败: {e}")

    # 03: 非科技主线
    try:
        nonfin_dir = find_latest_csv_dir("nonfin")
        nonfin_excel = cmd_export_excel("nonfin", nonfin_dir,
                                         os.path.join(output_dir, "03_非科技主线数据库",
                                                      f"非科技产业事件地图_{date}.xlsx"))
        zip_path = os.path.join(output_dir, "03_非科技主线数据库", f"CSV包_{date}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in glob.glob(os.path.join(nonfin_dir, "*")):
                zf.write(f, os.path.basename(f))
        print(f"非科技CSV包: {zip_path}")
    except Exception as e:
        print(f"非科技主线打包失败: {e}")

    # 04: 公司池
    for src in (COMPANY_POOL, CODE_MAP_CSV):
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(output_dir, "04_代表公司分类池"))

    # 05: 历史材料
    hist_src = os.path.join(CHATGPT_MIGRATE,
                            "科技产业事件地图_迁移包_20260618_穿阅度增补版", "05_历史材料")
    if os.path.isdir(hist_src):
        for f in os.listdir(hist_src):
            src_f = os.path.join(hist_src, f)
            if os.path.isfile(src_f):
                shutil.copy2(src_f, os.path.join(output_dir, "05_历史材料"))

    # 07: 技能代码
    for src in (
        os.path.join(SKILL_DIR, "scripts", "event_map_updater.py"),
        os.path.join(SKILL_DIR, "SKILL.md"),
        QUERY_SCRIPT,
        SECTORS_STATUS_CROSSWALK_CSV,
    ):
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(output_dir, "07_技能代码"))

    # 08: 框架版本快照
    fw_versions_dir = os.path.join(DATA_ROOT, "framework_versions")
    if os.path.isdir(fw_versions_dir):
        dst_fw = os.path.join(output_dir, "08_框架版本快照")
        for vdir in sorted(os.listdir(fw_versions_dir)):
            src_vdir = os.path.join(fw_versions_dir, vdir)
            if os.path.isdir(src_vdir):
                shutil.copytree(src_vdir, os.path.join(dst_fw, vdir), dirs_exist_ok=True)
        print(f"框架版本快照已打包")
    # Also copy current analysis template + scripts directly（唯一权威清单——
    # 每次新增技能脚本必须加进这个列表，否则迁移包会静默漏掉，见2026-07-10教训）
    for src in (
        os.path.join(REPO_ROOT, "skills", "stock-analysis", "references", "analysis-prompt-template.md"),
        os.path.join(REPO_ROOT, "skills", "stock-analysis", "scripts", "catalyst_window_model.py"),
        os.path.join(REPO_ROOT, "skills", "stock-analysis", "scripts", "framework_snapshot.py"),
        os.path.join(REPO_ROOT, "skills", "top-picks", "references", "catalyst_left_side_scanner.py"),
        os.path.join(REPO_ROOT, "skills", "stock-analysis", "scripts", "market_state_fetcher.py"),
        os.path.join(REPO_ROOT, "skills", "stock-analysis", "references", "market-state-machine.md"),
    ):
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(output_dir, "07_技能代码"))

    # market-outlook/SKILL.md：basename是通用的"SKILL.md"，会跟上面update-event-map
    # 自己的SKILL.md撞名互相覆盖，必须显式改名复制（同update-market-thesis skill.md
    # 已经在用的命名方式：{技能名}_SKILL.md）
    mo_skill = os.path.join(REPO_ROOT, "skills", "market-outlook", "SKILL.md")
    if os.path.exists(mo_skill):
        shutil.copy2(mo_skill, os.path.join(output_dir, "07_技能代码", "market-outlook_SKILL.md"))

    # 01: README
    readme_content = f"""# 产业事件地图迁移包
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## 恢复步骤

1. 将本迁移包放入 ~/Desktop/tz/技能数据/
2. 解压 02_科技主线数据库/CSV包_{date}.zip 到 ~/Desktop/tz/技能数据/科技产业事件/csv{date[-4:]}/
3. 解压 03_非科技主线数据库/CSV包_{date}.zip 到 ~/Desktop/tz/技能数据/非科技产业事件地图/非科技主线产业事件地图_CSV包_{date}/
4. 将 04_代表公司分类池/ 下的文件复制到 ~/Desktop/tz/技能数据/
5. 将 07_技能代码/event_map_updater.py 放入 ~/.claude/skills/update-event-map/scripts/
6. 将 07_技能代码/SKILL.md 放入 ~/.claude/skills/update-event-map/
7. 将 07_技能代码/event_map_query.py 和 catalyst_window_model.py 放入 ~/.claude/skills/stock-analysis/scripts/
8. 将 07_技能代码/analysis-prompt-template.md 放入 ~/.claude/skills/stock-analysis/references/
9. 将 07_技能代码/catalyst_left_side_scanner.py 放入 ~/.claude/skills/top-picks/references/
10. 将 07_技能代码/framework_snapshot.py 放入 ~/.claude/skills/stock-analysis/scripts/
11. 将 08_框架版本快照/ 复制到 ~/Desktop/tz/技能数据/framework_versions/
12. 将 05_历史材料/ 中的memory/*.md 复制到 ~/.claude/projects/对应目录/memory/

## 数据概况

### 科技主线
- 覆盖赛道: AI算力、半导体、存储、光通信、先进封装、机器人、商业航天、被动元件等
- 数据表: events/mapping/forward/corrections/signals_early/reviews/sources/weekly/sectors_status

### 非科技主线
- 覆盖赛道: 有色金属、储能、创新药、银行、证券等
- 数据表: events/mapping/forward/sources

### 公司池
- 科技公司池 + 非科技公司池 + 代码映射表
- 用于事件→公司→股票代码的全链路关联

## 分类规则

- 已验证产业事实 → events
- 传闻/小作文/未验证订单 → signals_early
- 技术路线争议/拥挤风险 → corrections
- 未来催化窗口 → forward
- 公司名称仅用于产业链定位，不构成股票推荐
"""
    with open(os.path.join(output_dir, "01_项目主索引_README", f"README_{date}.md"), "w") as f:
        f.write(readme_content)

    # 06: 版本记录
    with open(os.path.join(output_dir, "06_版本记录", f"迁移包说明_{date}.md"), "w") as f:
        f.write(f"# 迁移包说明 {date}\n\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write("包含: 科技主线 + 非科技主线 + 公司池 + 历史材料 + 技能代码\n")

    print(f"\n迁移包已生成: {output_dir}")
    return output_dir


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="产业事件地图更新工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="显示当前数据库状态")
    p_status.add_argument("--source", choices=["tech", "nonfin"], default="tech")

    p_apply = sub.add_parser("apply", help="应用JSON变更到CSV")
    p_apply.add_argument("--source", choices=["tech", "nonfin"], default="tech")
    p_apply.add_argument("--date", required=True, help="版本日期 MMDD（如0621）")
    p_apply.add_argument("--label", required=True, help="版本标签（如weekly_update）")
    p_apply.add_argument("--changes", required=True, help="变更JSON文件路径")

    p_excel = sub.add_parser("export-excel", help="从CSV生成合并Excel")
    p_excel.add_argument("--source", choices=["tech", "nonfin"], default="tech")
    p_excel.add_argument("--output", help="输出路径（默认自动生成）")

    p_migrate = sub.add_parser("migrate", help="生成迁移包（科技+非科技+公司池）")
    p_migrate.add_argument("--output", help="输出目录（默认~/Desktop/tz/技能数据/迁移包_日期）")

    p_validate = sub.add_parser("validate", help="跨表一致性检查")
    p_validate.add_argument("--source", choices=["tech", "nonfin"], default="tech")
    p_validate.add_argument(
        "--strict-refs",
        action="store_true",
        help="额外报告未进入events主表的候选/外部引用（仅提示，不计结构错误）",
    )

    p_sync = sub.add_parser("sync-pool", help="手动重跑公司池同步（正常由apply自动触发，仅用于补录/调试）")
    p_sync.add_argument("--source", choices=["tech", "nonfin"], default="tech")
    p_sync.add_argument("--changes", required=True, help="变更JSON文件路径（读取其中的mapping新增行）")

    p_reconcile = sub.add_parser("reconcile-pool", help="用最新mapping全量对账并回填公司池历史缺口")
    p_reconcile.add_argument("--source", choices=["tech", "nonfin"], default="tech")
    p_reconcile.add_argument("--sector", help="仅对账指定一级赛道关键词；不传则全量")

    p_ids = sub.add_parser("next-ids", help="生成下一批ID")
    p_ids.add_argument("--source", choices=["tech", "nonfin"], default="tech")
    p_ids.add_argument("--table", required=True, help="表名（events/mapping/forward等）")
    p_ids.add_argument("--count", type=int, default=5, help="生成数量（默认5）")
    p_ids.add_argument("--sector", help="赛道名（仅events需要，如AI算力）")
    p_ids.add_argument("--date", help="日期YYYYMMDD（默认今日）")

    args = parser.parse_args()

    if args.cmd == "status":
        cmd_status(args.source)
    elif args.cmd == "apply":
        cmd_apply(args.source, args.date, args.label, args.changes)
    elif args.cmd == "export-excel":
        cmd_export_excel(args.source, output_path=args.output)
    elif args.cmd == "migrate":
        cmd_migrate(args.output)
    elif args.cmd == "validate":
        cmd_validate(args.source, strict_refs=args.strict_refs)
    elif args.cmd == "sync-pool":
        with open(args.changes, encoding="utf-8") as f:
            changes = json.load(f)
        result = sync_company_pool(args.source, mapping_rows=changes.get("additions", {}).get("mapping"))
        print(f"公司池同步: +{result['added']}家新增, {result['already_exists']}家已存在, "
              f"{len(result['skipped_no_code'])}家无A股代码跳过")
        if result["skipped_no_code"]:
            print("  无代码跳过: " + "、".join(result["skipped_no_code"]))
    elif args.cmd == "reconcile-pool":
        result = reconcile_company_pool(args.source, sector=args.sector)
        print(f"公司池全量对账: source={args.source}, sector={result['sector']}, "
              f"扫描mapping {result['mapping_rows_scanned']}行, +{result['added']}条公司角色, "
              f"{result['already_exists']}条已存在, 清理{result['deduped']}条精确重复, "
              f"{len(result['skipped_no_code'])}条无A股代码跳过")
        if result["skipped_no_code"]:
            print("  无代码跳过: " + "、".join(sorted(set(result["skipped_no_code"]))))
    elif args.cmd == "next-ids":
        cmd_next_ids(args.source, args.table, args.count, args.sector, args.date)


if __name__ == "__main__":
    main()
