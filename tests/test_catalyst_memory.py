import importlib.util
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / "skills/shared/catalyst_memory.py"
    spec = importlib.util.spec_from_file_location("catalyst_memory_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observation_ledger_is_append_only_and_idempotent(tmp_path):
    mod = load()
    con = mod.connect(tmp_path / "memory.db")
    record = dict(source="tech", event_id="E-1", observed_at="2026-08-01",
                  kind="thesis", summary="需求将改善", source_grade="primary",
                  source_ref="announcement")
    assert mod.record_observation(con, **record) is True
    assert mod.record_observation(con, **record) is False
    assert mod.record_observation(con, **{**record, "observed_at": "2026-08-05",
                                           "kind": "invalidation", "summary": "订单延期"}) is True
    rows = mod.observations(con, "tech", "E-1")
    assert [r["kind"] for r in rows] == ["thesis", "invalidation"]


def test_price_response_uses_pre_anchor_close_and_does_not_fill_future_horizon():
    mod = load()
    stock = [
        {"trade_date": "20260731", "close": 100}, {"trade_date": "20260803", "close": 110},
        {"trade_date": "20260804", "close": 121}, {"trade_date": "20260805", "close": 115},
        {"trade_date": "20260806", "close": 120},
    ]
    bench = [
        {"trade_date": "20260731", "close": 100}, {"trade_date": "20260803", "close": 102},
        {"trade_date": "20260804", "close": 104}, {"trade_date": "20260805", "close": 103},
        {"trade_date": "20260806", "close": 105},
    ]
    result = mod.calculate_price_response(stock, bench, "2026-08-01")
    assert result["first_trade_date"] == "20260803"
    assert result["horizons"]["t0"]["return_pct"] == 10.0
    assert result["horizons"]["t1"]["excess_pct"] == 17.0
    assert result["horizons"]["t3"]["return_pct"] == 20.0
    assert result["horizons"]["t5"] is None


def test_basket_summary_uses_excess_return_median():
    mod = load()
    rows = [
        {"response": {"horizons": {"t0": {"excess_pct": 2.0}}}},
        {"response": {"horizons": {"t0": {"excess_pct": 6.0}}}},
    ]
    assert mod.basket_medians(rows)["t0_median_excess_pct"] == 4.0


def test_response_summary_keeps_latest_snapshot_per_anchor_and_company(tmp_path):
    mod = load()
    con = mod.connect(tmp_path / "memory.db")
    base = dict(source="tech", event_id="E-1", anchor_label="首次提出",
                anchor_date="2026-08-01", ts_code="000001.SZ", company_name="示例",
                benchmark_code="000300.SH")
    mod.store_price_response(con, **base, as_of="2026-08-05",
                             response={"horizons": {"t0": {"excess_pct": 1}}})
    mod.store_price_response(con, **base, as_of="2026-08-10",
                             response={"horizons": {"t0": {"excess_pct": 2}}})
    rows = mod.response_summary(con, "tech", "E-1")
    assert len(rows) == 1
    assert rows[0]["as_of"] == "2026-08-10"


def test_unmatched_material_survives_in_intake_queue_until_resolved(tmp_path):
    mod = load()
    con = mod.connect(tmp_path / "memory.db")
    intake_id = mod.record_intake(con, received_at="2026-08-31",
        raw_summary="博主暗语提到某金属", candidate_source="nonfin")
    assert mod.intake_queue(con)[0]["intake_id"] == intake_id
    assert mod.resolve_intake(con, intake_id, status="linked", candidate_source="nonfin",
                              candidate_event_id="METAL-1", resolution_notes="用户确认指钨")
    assert mod.intake_queue(con) == []
    assert mod.intake_queue(con, "linked")[0]["candidate_event_id"] == "METAL-1"
