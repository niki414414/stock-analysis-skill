#!/usr/bin/env python3
"""event_map_updater.py — 产业事件地图写入/导出/迁移工具。

与 event_map_query.py（只读）配合，本脚本负责所有写入操作：
  status    显示当前数据库状态
  apply     从JSON增量更新CSV
  export-excel  从CSV生成合并Excel
  migrate   生成迁移包（科技+非科技+公司池+脚本）
  validate  跨表一致性检查
  next-ids  生成下一批事件ID

用法：
  python3 event_map_updater.py status
  python3 event_map_updater.py apply --date 0621 --label weekly --changes /tmp/changes.json
  python3 event_map_updater.py export-excel --output ~/Desktop/tz/科技产业事件/621.xlsx
  python3 event_map_updater.py migrate --output ~/Desktop/tz/迁移包_20260621
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
TECH_DIR = os.path.expanduser("~/Desktop/tz/科技产业事件")
NONFIN_DIR = os.path.expanduser("~/Desktop/tz")
COMPANY_POOL = os.path.expanduser("~/Desktop/tz/公司.xlsx")
CODE_MAP_CSV = os.path.expanduser("~/Desktop/tz/company_code_map.csv")
CHATGPT_MIGRATE = os.path.expanduser("~/Desktop/tz/chatgpt迁移包")
SKILL_DIR = os.path.expanduser("~/.claude/skills/update-event-map")
QUERY_SCRIPT = os.path.expanduser("~/.claude/skills/stock-analysis/scripts/event_map_query.py")

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
        "cols": ["sector_id", "theme", "sub_sector", "stage", "wave_number",
                 "wave_position", "signal_stock_code", "catalyst_quality",
                 "ai_correlation", "priority", "style_position", "event_map_status",
                 "last_updated", "note"],
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

    return new_dir, summary


# ═══════════════════════════════════════════════════════════════════════
# PART 3b: 公司池同步（原STEP 5.5，从prompt指令改为代码函数，2026-07-10）
# ═══════════════════════════════════════════════════════════════════════

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
    existing_names = set(existing["公司名称"].astype(str))

    new_rows = []
    skipped = []
    already = 0
    batch_label = date_short or datetime.now().strftime("%m%d")

    for row in mapping_rows:
        rep = str(row.get("代表公司/公司类型", "") or "")
        if not rep or rep == "nan":
            continue
        for name in re.split(r'[、，,;；/]', rep):
            name = name.strip()
            if not name or len(name) < 2:
                continue
            if name in existing_names:
                already += 1
                continue
            code = code_dict.get(name)
            if not code:
                skipped.append(name)
                continue
            new_rows.append({
                "大类":         da_lei,
                "一级赛道":     row.get("一级赛道", ""),
                "二级环节":     row.get("二级环节", ""),
                "三级环节/定位": row.get("三级零部件/材料/设备", ""),
                "公司名称":     name,
                "市场/属性":    "A股",
                "角色":        "代表公司/公司类型",
                "来源批次":     f"{batch_label}公司池自动同步",
                "关联事件ID":   row.get("事件ID", ""),
                "入库建议":     "mapping自动同步",
                "置信度":      "中",
                "备注":        "",
            })
            existing_names.add(name)  # 防止本批次内重复添加

    if new_rows:
        combined = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
        with pd.ExcelWriter(COMPANY_POOL, engine="openpyxl", mode="a",
                            if_sheet_exists="replace") as writer:
            combined.to_excel(writer, sheet_name=sheet_name, index=False)

    return {"added": len(new_rows), "already_exists": already, "skipped_no_code": skipped}


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

def cmd_validate(source="tech"):
    csv_dir = find_latest_csv_dir(source)
    tables = get_tables(source)
    warnings = []

    events_df, _ = load_csv(csv_dir, "events")
    if events_df is None:
        warnings.append("events表缺失")
        print(f"验证完成: {len(warnings)} 个问题")
        for w in warnings:
            print(f"  ⚠️ {w}")
        return warnings

    event_ids = set(events_df["事件ID"].astype(str).tolist())

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
            ref_ids = str(row[eid_col]).split(";")
            for rid in ref_ids:
                rid = rid.strip()
                if rid and rid != "nan" and rid not in event_ids:
                    warnings.append(f"{tname}.{eid_col}={rid} 在events表中不存在")

    for tname, tdef in tables.items():
        id_col = tdef.get("id_col")
        if not id_col:
            continue
        df, _ = load_csv(csv_dir, tdef["prefix"])
        if df is None:
            continue
        dups = df[df[id_col].duplicated(keep=False)]
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
        output_dir = os.path.join(os.path.expanduser("~/Desktop/tz"), f"迁移包_{date}")

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
    ):
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(output_dir, "07_技能代码"))

    # 08: 框架版本快照
    fw_versions_dir = os.path.expanduser("~/Desktop/tz/framework_versions")
    if os.path.isdir(fw_versions_dir):
        dst_fw = os.path.join(output_dir, "08_框架版本快照")
        for vdir in sorted(os.listdir(fw_versions_dir)):
            src_vdir = os.path.join(fw_versions_dir, vdir)
            if os.path.isdir(src_vdir):
                shutil.copytree(src_vdir, os.path.join(dst_fw, vdir), dirs_exist_ok=True)
        print(f"框架版本快照已打包")
    # Also copy current analysis template directly
    analysis_tmpl = os.path.expanduser(
        "~/.claude/skills/stock-analysis/references/analysis-prompt-template.md")
    if os.path.exists(analysis_tmpl):
        shutil.copy2(analysis_tmpl, os.path.join(output_dir, "07_技能代码"))
    scanner = os.path.expanduser(
        "~/.claude/skills/top-picks/references/signal_scanner.py")
    if os.path.exists(scanner):
        shutil.copy2(scanner, os.path.join(output_dir, "07_技能代码"))
    snapshot_script = os.path.expanduser(
        "~/.claude/skills/stock-analysis/scripts/framework_snapshot.py")
    if os.path.exists(snapshot_script):
        shutil.copy2(snapshot_script, os.path.join(output_dir, "07_技能代码"))

    # 01: README
    readme_content = f"""# 产业事件地图迁移包
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## 恢复步骤

1. 将本迁移包放入 ~/Desktop/tz/
2. 解压 02_科技主线数据库/CSV包_{date}.zip 到 ~/Desktop/tz/科技产业事件/csv{date[-4:]}/
3. 解压 03_非科技主线数据库/CSV包_{date}.zip 到 ~/Desktop/tz/非科技主线产业事件地图_CSV包_{date}/
4. 将 04_代表公司分类池/ 下的文件复制到 ~/Desktop/tz/
5. 将 07_技能代码/event_map_updater.py 放入 ~/.claude/skills/update-event-map/scripts/
6. 将 07_技能代码/SKILL.md 放入 ~/.claude/skills/update-event-map/
7. 将 07_技能代码/event_map_query.py 放入 ~/.claude/skills/stock-analysis/scripts/
8. 将 07_技能代码/analysis-prompt-template.md 放入 ~/.claude/skills/stock-analysis/references/
9. 将 07_技能代码/signal_scanner.py 放入 ~/.claude/skills/top-picks/references/
10. 将 08_框架版本快照/ 复制到 ~/Desktop/tz/framework_versions/
11. 将 05_历史材料/ 中的memory/*.md 复制到 ~/.claude/projects/对应目录/memory/

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
    p_migrate.add_argument("--output", help="输出目录（默认~/Desktop/tz/迁移包_日期）")

    p_validate = sub.add_parser("validate", help="跨表一致性检查")
    p_validate.add_argument("--source", choices=["tech", "nonfin"], default="tech")

    p_sync = sub.add_parser("sync-pool", help="手动重跑公司池同步（正常由apply自动触发，仅用于补录/调试）")
    p_sync.add_argument("--source", choices=["tech", "nonfin"], default="tech")
    p_sync.add_argument("--changes", required=True, help="变更JSON文件路径（读取其中的mapping新增行）")

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
        cmd_validate(args.source)
    elif args.cmd == "sync-pool":
        with open(args.changes, encoding="utf-8") as f:
            changes = json.load(f)
        result = sync_company_pool(args.source, mapping_rows=changes.get("additions", {}).get("mapping"))
        print(f"公司池同步: +{result['added']}家新增, {result['already_exists']}家已存在, "
              f"{len(result['skipped_no_code'])}家无A股代码跳过")
        if result["skipped_no_code"]:
            print("  无代码跳过: " + "、".join(result["skipped_no_code"]))
    elif args.cmd == "next-ids":
        cmd_next_ids(args.source, args.table, args.count, args.sector, args.date)


if __name__ == "__main__":
    main()
