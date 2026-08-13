import importlib.util
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_formal_contract_is_activation_candidate_but_replayed_rumor_is_not():
    model = load(ROOT / "skills/stock-analysis/scripts/catalyst_window_model.py")
    formal = model.qualify_evidence_activation(
        "2026-08-03", "7月24日已由重大供货协议验证需求", today=date(2026, 8, 6)
    )
    rumor = model.qualify_evidence_activation(
        "2026-08-03", "媒体转述的缺口数据尚缺原始报告，待核验", today=date(2026, 8, 6)
    )
    assert formal["eligible"] is True
    assert rumor["eligible"] is False


def test_confirmed_activation_ledger_keeps_latest_date(tmp_path):
    activation = load(ROOT / "skills/shared/catalyst_activation.py")
    ledger = tmp_path / "ledger.json"
    activation.record_activation({
        "source": "tech", "event_id": "SEMI-1", "activation_date": "2026-08-03",
        "status": "confirmed",
    }, ledger)
    activation.record_activation({
        "source": "tech", "event_id": "SEMI-1", "activation_date": "2026-08-06",
        "status": "confirmed",
    }, ledger)
    assert activation.confirmed_activation_dates(ledger)[("tech", "SEMI-1")] == date(2026, 8, 6)


def test_confirmed_activation_reanchors_effective_date_without_overwriting_origin():
    model = load(ROOT / "skills/stock-analysis/scripts/catalyst_window_model.py")
    result = model.score_event(
        event_id="SEMI-1", event_name="材料缺货涨价", event_time="2026-01-01",
        event_status="进行中", importance="★★★★★", expectation_gap="有",
        today=date(2026, 8, 6), latest_update="2026-08-03",
        evidence_text="正式供货协议", confirmed_activation_date=date(2026, 8, 5),
    )
    assert result["event_date"] == date(2026, 1, 1)
    assert result["effective_date"] == date(2026, 8, 5)
    assert result["t_current"] == 1
