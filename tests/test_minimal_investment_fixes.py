"""Regression cases for the six issues reproduced in the workspace audit."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brief_fixture = load("fix_brief_fixture", "tests/test_market_brief_v2.py")
brief = brief_fixture.MODULE
short_fixture = load("fix_short_fixture", "tests/test_limit_up_trend_scanner.py")
backtest = load("fix_backtest", "skills/market-outlook/scripts/ma5_short_backtest.py")
journal = load("fix_journal", "scripts/daily_decision_journal.py")
quality = load("fix_quality", "skills/quality-compounder/references/quality_compounder_screener.py")
swing = load("fix_swing", "skills/top-picks/references/catalyst_left_side_scanner.py")
fetcher = load("fix_fetcher", "skills/stock-analysis/references/stock_data_fetcher.py")
from skills.shared.price_data import adjust_to_latest


def corporate_action_bars():
    return pd.DataFrame([
        dict(ts_code="600001.SH", trade_date="20260918", open=20, high=21,
             low=19, close=20, amount=20000, vol=1000),
        dict(ts_code="600001.SH", trade_date="20260921", open=10, high=10.5,
             low=9.5, close=10, amount=20000, vol=2000),
    ])


def factors():
    return pd.DataFrame({"trade_date": ["20260918", "20260921", "20260922"],
                         "adj_factor": [1., 2., 4.]})


def test_adjustment_removes_split_gap_but_preserves_actual_quote_and_turnover():
    result = adjust_to_latest(corporate_action_bars(), factors())
    assert result.close.tolist() == [10, 10]
    assert result.raw_close.tolist() == [20, 10]
    assert result.high.tolist() == [10.5, 10.5]
    assert result.amount.tolist() == [20000, 20000]
    # Future factors must not change the historical quotation scale.
    assert result.price_basis.eq("qfq_latest_bar").all()


@pytest.mark.parametrize("factor", [None, 0, float("inf")])
def test_missing_or_invalid_factor_cannot_fall_back_to_raw(factor):
    adj = factors().iloc[:2].copy()
    adj.loc[0, "adj_factor"] = factor
    with pytest.raises(ValueError, match="复权因子"):
        adjust_to_latest(corporate_action_bars(), adj)


def test_stock_and_brief_fetchers_both_consume_adjusted_prices(monkeypatch):
    pro = SimpleNamespace(daily=lambda **kw: corporate_action_bars(),
                          adj_factor=lambda **kw: factors())
    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_api=lambda token: pro))
    monkeypatch.setenv("TUSHARE_TOKEN", "offline-fixture")
    monkeypatch.setenv("TZ_CODEX_HOME", str(ROOT.parent))
    rows, _ = fetcher._fetch_tushare_a("600001", 30)
    assert [r["close"] for r in rows] == [10, 10]
    assert rows[0]["raw_close"] == 20
    result = brief._fetch_bars(pro, "daily", "600001.SH", "20260901", "20260921")
    assert result.close.tolist() == [10, 10]


def test_swing_fetch_uses_same_adjusted_price_basis(monkeypatch):
    monkeypatch.setattr(swing.time, "sleep", lambda _: None)
    pro = SimpleNamespace(daily=lambda **kw: corporate_action_bars(),
                          adj_factor=lambda **kw: factors())
    rows = swing.fetch_price_batch(pro, ["600001"], "20260921")["600001"]
    assert [row["close"] for row in rows] == [10, 10]
    assert all(row["price_basis"] == "qfq_latest_bar" for row in rows)


def test_swing_fetch_excludes_stale_stock_without_requesting_factors(monkeypatch):
    monkeypatch.setattr(swing.time, "sleep", lambda _: None)
    pro = SimpleNamespace(daily=lambda **kw: corporate_action_bars(),
                          adj_factor=lambda **kw: pytest.fail("过期股票不应请求复权因子"))
    assert swing.fetch_price_batch(pro, ["600001"], "20260922") == {"600001": []}


def test_swing_split_does_not_create_a_false_ma60_rejection(monkeypatch):
    monkeypatch.setattr(swing.time, "sleep", lambda _: None)
    days = pd.date_range("2026-06-01", periods=60, freq="B").strftime("%Y%m%d")
    raw = pd.DataFrame([
        dict(ts_code="600001.SH", trade_date=day, open=close, close=close,
             high=high, low=low, pct_chg=0, vol=100)
        for day, close, high, low in (
            (day, 20, 20.2, 19.8) if idx < 50 else (day, 10.5, 11.8, 10.2)
            for idx, day in enumerate(days)
        )
    ])
    factors = pd.DataFrame({"ts_code": "600001.SH", "trade_date": days,
                            "adj_factor": [1] * 50 + [2] * 10})
    pro = SimpleNamespace(daily=lambda **kw: raw, adj_factor=lambda **kw: factors)
    adjusted = swing.fetch_price_batch(pro, ["600001"], days[-1])["600001"]
    assert not swing.analyze_price_freshness(raw.to_dict("records"))["passes"]
    assert swing.analyze_price_freshness(adjusted)["passes"]


def test_missing_qfq_does_not_silently_retry_unadjusted(monkeypatch):
    calls = []
    def failing(**kwargs):
        calls.append(kwargs["adjust"])
        raise RuntimeError("offline failure")
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist=failing))
    with pytest.raises(RuntimeError):
        fetcher._fetch_akshare_a("600001", 30)
    assert calls == ["qfq"]


def test_stale_short_candidate_is_excluded_and_reported():
    bars = short_fixture.make_bars([10 + i * .05 for i in range(25)], limit_index=16)
    basics = pd.DataFrame([dict(ts_code="600001.SH", name="样本", list_date="20200101")])
    fresh = short_fixture.MOD.scan_frames(bars, basics, "20260725")
    assert len(fresh) == 1
    stale = short_fixture.MOD.scan_frames(bars, basics, "20260804")
    assert stale.empty
    assert stale.attrs["excluded_stale"][0]["latest_date"] == "20260725"


def test_mixed_date_brief_is_degraded_without_stale_sector_or_stock_plan():
    bundle = brief_fixture.bundle()
    bundle["sectors"][0]["bars"] = bundle["sectors"][0]["bars"][:-10]
    bundle["sectors"][1]["representatives"][0]["bars"] = bundle["sectors"][1]["representatives"][0]["bars"][:-5]
    result = brief.build_brief(bundle)
    assert result["status"] == "degraded"
    assert "证券" not in [r["name"] for r in result["sectors"]]
    assert next(r for r in result["sectors"] if r["name"] == "小金属")["representative"] is None
    assert len(result["data_gaps"]) == 2


def test_unknown_stock_adjustment_cannot_generate_trade_levels():
    bundle = brief_fixture.bundle()
    bundle["sectors"][0]["representatives"][0].pop("price_basis")
    result = brief.build_brief(bundle)
    assert result["status"] == "degraded"
    assert next(r for r in result["sectors"] if r["name"] == "证券")["representative"] is None


def test_live_fetch_rejects_stale_index():
    pro = SimpleNamespace(index_daily=lambda **kw: corporate_action_bars())
    with pytest.raises(RuntimeError, match="日期与目标"):
        brief._fetch_bars(pro, "index_daily", "000001.SH", "20260901", "20260922")


def test_premarket_resolves_previous_session(monkeypatch):
    actual_timestamp = pd.Timestamp
    class FixedTimestamp:
        @staticmethod
        def now(tz=None):
            return actual_timestamp("2026-09-22 08:30", tz=tz)
    monkeypatch.setattr(brief.pd, "Timestamp", FixedTimestamp)
    pro = SimpleNamespace(trade_cal=lambda **kw: pd.DataFrame({
        "cal_date": ["20260918", "20260921", "20260922"], "is_open": [1, 1, 1]}))
    assert brief._latest_trade_date(pro, None) == "20260921"
    assert brief._latest_trade_date(pro, "20260918") == "20260918"


def test_red_or_missing_gate_not_counted_in_returns():
    rows = pd.DataFrame([dict(signal_date=d, executable=True, next_day_open_exit_pct=r)
                         for d, r in [("20260918", 10), ("20260921", -1), ("20260922", 20)]])
    activity = pd.DataFrame([dict(trade_date=d, market_activity="normal", pushdozer_gate=g,
        advance_pct=50, turnover_ratio_20d=1, limit_up_count=20, limit_down_count=downs)
        for d, g, downs in [("20260918", "red", 30), ("20260921", "green", 1)]])
    result = backtest.attach_market_activity(rows, activity)
    main = backtest.summarize(result)
    assert main["executed"] == 1
    assert main["next_day_open_exit"]["mean_pct"] == -1
    assert backtest.summarize(result, "executable")["executed"] == 3


def test_v2_journal_keeps_full_snapshot_without_manual_date(tmp_path):
    result = brief.build_brief(brief_fixture.bundle())
    source = tmp_path / "review.json"
    source.write_text(json.dumps(result), encoding="utf-8")
    row = journal.record_review(source, tmp_path / "journal.jsonl")
    assert row["trade_date"] == result["as_of_date"]
    assert row["market_judgment"]["current"] == result["market"]["current"]
    assert len(row["directions"]) == len(result["sectors"])
    assert row["review_snapshot"] == result


@pytest.mark.parametrize("no_events", [False, True])
def test_empty_swing_scan_is_valid_json(monkeypatch, capsys, no_events):
    monkeypatch.setattr(sys, "argv", ["scanner", "--json"])
    monkeypatch.setattr(swing, "_get_pro", lambda: object())
    monkeypatch.setattr(swing, "resolve_trade_date", lambda p: "20260921")
    monkeypatch.setattr(swing, "load_active_events", lambda: pd.DataFrame() if no_events else
                        pd.DataFrame([dict(catalyst_score=4, activation_pending=False)]))
    monkeypatch.setattr(swing, "load_company_candidates", lambda *a, **kw: pd.DataFrame())
    swing.main()
    assert json.loads(capsys.readouterr().out) == []


def test_empty_reactivation_scan_preserves_its_summary_protocol(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["scanner", "--json", "--reactivation-only"])
    monkeypatch.setattr(swing, "_get_pro", lambda: object())
    monkeypatch.setattr(swing, "resolve_trade_date", lambda p: "20260921")
    monkeypatch.setattr(swing, "load_active_events", lambda: pd.DataFrame())
    swing.main()
    assert json.loads(capsys.readouterr().out) == {
        "trade_date": "20260921", "pending_events": 0,
        "pending_companies": 0, "confirmed_events": [],
    }


def test_swing_json_candidates_include_the_scan_trade_date(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["scanner", "--json"])
    monkeypatch.setattr(swing, "_get_pro", lambda: object())
    monkeypatch.setattr(swing, "resolve_trade_date", lambda p: "20260921")
    monkeypatch.setattr(swing, "load_active_events", lambda: pd.DataFrame([
        {"catalyst_score": 4, "activation_pending": False}
    ]))
    monkeypatch.setattr(swing, "load_company_candidates", lambda *a, **kw: pd.DataFrame([
        {"code": "600001", "activation_pending": False}
    ]))
    monkeypatch.setattr(swing, "apply_event_reactivation", lambda *a: pd.DataFrame())
    monkeypatch.setattr(swing, "fetch_price_batch", lambda *a, **kw: {})
    monkeypatch.setattr(swing, "score_candidates", lambda *a: pd.DataFrame([{"code": "600001"}]))
    monkeypatch.setattr(swing, "compute_sector_repair", lambda *a: {})
    monkeypatch.setattr(swing, "attach_sector_repair", lambda rows, _: rows)
    monkeypatch.setattr(swing, "log_prospective", lambda *a: None)
    swing.main()
    assert json.loads(capsys.readouterr().out)[0]["trade_date"] == "20260921"


@pytest.mark.parametrize("empty", [True, False])
def test_quality_provider_failure_cannot_look_like_no_opportunities(empty):
    def fetch(**kw):
        if empty:
            return pd.DataFrame()
        raise RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="不能解释为无候选"):
        quality._filter_by_pe(SimpleNamespace(daily_basic=fetch), [{"ts_code": "600001.SH"}], 30, "20260921")


def test_valid_but_expensive_or_unprofitable_stocks_can_produce_empty_pool():
    pro = SimpleNamespace(daily_basic=lambda **kw: pd.DataFrame([
        dict(ts_code="600001.SH", pe_ttm=50), dict(ts_code="600002.SH", pe_ttm=None)]))
    assert quality._filter_by_pe(pro, [{"ts_code": "600001.SH"}, {"ts_code": "600002.SH"}], 30, "20260921") == []


def test_quality_annual_data_outage_is_not_a_failed_quality_filter():
    pro = SimpleNamespace(income_vip=lambda **kw: pd.DataFrame())
    with pytest.raises(RuntimeError, match="质量筛选未完成"):
        quality._fetch_annual_netprofit(pro, [{"ts_code": "600001.SH"}], 10)


def test_short_whole_market_staleness_fails_instead_of_backdating_scan():
    pro = SimpleNamespace(
        trade_cal=lambda **kw: pd.DataFrame({"cal_date": ["20260918", "20260921"], "is_open": [1, 1]}),
        daily=lambda **kw: corporate_action_bars().iloc[:1] if kw["trade_date"] == "20260918" else pd.DataFrame(),
        adj_factor=lambda **kw: pd.DataFrame({"ts_code": ["600001.SH"], "trade_date": [kw["trade_date"]], "adj_factor": [1.]}),
    )
    with pytest.raises(RuntimeError, match="全市场行情过期"):
        short_fixture.MOD.fetch_data(pro, "20260921")


def test_new_adjustment_anchor_does_not_reuse_incomparable_previous_box(monkeypatch):
    stock = brief_fixture.bundle()["sectors"][0]["representatives"][0]
    for bar in stock["bars"]:
        bar["adj_factor"] = 2.
    observed = []
    original = brief.build_price_map
    def spy(item, previous_snapshot=None):
        observed.append(previous_snapshot)
        return original(item, previous_snapshot)
    monkeypatch.setattr(brief, "build_price_map", spy)
    prior = {"code": stock["code"], "price_basis": "qfq_latest_bar",
             "adjustment_anchor_factor": 1., "structure": {"must_not_reuse": True}}
    brief._representative_map({"representatives": [stock]}, prior)
    assert observed == [None]
