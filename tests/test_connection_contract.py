"""Integration regressions for independent processes and relocated checkouts."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from skills.shared import datasource, paths

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = [
    'scripts/event_db.py',
    'scripts/event_db_cutover.py',
    'scripts/daily_decision_journal.py',
    'scripts/integrated_research.py',
    'scripts/stock_strategy_router.py',
    'scripts/create_local_backup.py',
    'skills/market-outlook/scripts/market_brief_v2.py',
    'skills/market-outlook/scripts/market_daily_review.py',
    'skills/market-outlook/scripts/market_opportunity_radar.py',
    'skills/market-outlook/scripts/limit_up_trend_scanner.py',
    'skills/market-outlook/scripts/earnings_forecast_radar.py',
    'skills/market-outlook/scripts/catalyst_dossier.py',
    'skills/market-outlook/scripts/catalyst_timeline.py',
    'skills/stock-analysis/scripts/event_map_query.py',
    'skills/stock-analysis/scripts/market_state_fetcher.py',
    'skills/stock-analysis/scripts/daily_snapshot.py',
    'skills/stock-analysis/references/stock_data_fetcher.py',
    'skills/top-picks/references/catalyst_left_side_scanner.py',
    'skills/quality-compounder/references/quality_compounder_screener.py',
    'skills/market-outlook/scripts/market_structure_v2_replay.py',
    'skills/market-outlook/scripts/market_framework_backtest.py',
    'skills/market-outlook/scripts/ma5_short_backtest.py',
    'skills/market-outlook/scripts/pushdozer_variant_backtest.py',
    'skills/shared/daily_notify.py',
]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'data space'
    (root / 'repo').mkdir(parents=True)
    monkeypatch.setenv('TZ_CODEX_HOME', str(root))
    monkeypatch.delenv('EVENT_MAP_DB', raising=False)
    monkeypatch.delenv('TUSHARE_TOKEN', raising=False)
    return root


def test_locations_do_not_depend_on_cwd_or_env_file(workspace, monkeypatch, tmp_path):
    (workspace / 'repo/.env').write_text('TZ_CODEX_HOME=/wrong\nEVENT_MAP_DB=/wrong.db\n')
    monkeypatch.chdir(tmp_path)
    datasource.load_env()
    assert paths.repo_root() == ROOT
    assert paths.workspace_root() == workspace.resolve()
    assert paths.event_db_path() == workspace / '技能数据/event_map_shadow.db'
    monkeypatch.setenv('EVENT_MAP_DB', 'isolated/events.db')
    assert paths.event_db_path() == workspace / 'isolated/events.db'
    monkeypatch.setenv('TZ_CODEX_HOME', 'relative-workspace')
    with pytest.raises(ValueError, match='绝对路径'):
        paths.workspace_root()


def test_default_config_belongs_to_checkout(monkeypatch):
    monkeypatch.delenv('TZ_CODEX_HOME', raising=False)
    assert paths.config_file() == ROOT / '.env'


def test_config_priority_quotes_and_token_refresh(workspace, monkeypatch):
    monkeypatch.setenv('TUSHARE_TOKEN', 'obsolete-process-token')
    monkeypatch.setenv('SERVERCHAN_KEY', 'process-notification-key')
    env = workspace / 'repo/.env'
    env.write_text('export TUSHARE_TOKEN="first-token" # comment\n'
                   'SERVERCHAN_KEY=file-key\nTAVILY_API_KEY=search-key # comment\n')
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    tokens = []
    monkeypatch.setitem(sys.modules, 'tushare', SimpleNamespace(
        pro_api=lambda token: tokens.append(token) or SimpleNamespace(token=token)))
    assert datasource.get_pro().token == 'first-token'
    assert os.environ['SERVERCHAN_KEY'] == 'process-notification-key'
    assert os.environ['TAVILY_API_KEY'] == 'search-key'
    env.write_text("TUSHARE_TOKEN='second-token'\n")
    assert datasource.get_pro().token == 'second-token'
    assert tokens == ['first-token', 'second-token']


def test_missing_and_malformed_configuration_are_explicit(workspace, monkeypatch):
    with pytest.raises(RuntimeError, match='TUSHARE_TOKEN'):
        datasource.get_pro()
    secret = 'do-not-print-this-token'
    (workspace / 'repo/.env').write_text(f'TUSHARE_TOKEN="{secret}\n')
    with pytest.raises(ValueError) as error:
        datasource.get_pro()
    assert secret not in str(error.value)


def test_project_token_does_not_leak_into_another_workspace(workspace, monkeypatch, tmp_path):
    monkeypatch.setenv('TUSHARE_TOKEN', 'inherited-fixture')
    (workspace / 'repo/.env').write_text('TUSHARE_TOKEN=project-fixture\n')
    assert datasource.get_token() == 'project-fixture'
    monkeypatch.setenv('TZ_CODEX_HOME', str(tmp_path / 'another-workspace'))
    assert datasource.get_token() == 'inherited-fixture'
    monkeypatch.setenv('TUSHARE_TOKEN', 'explicitly-replaced-fixture')
    assert datasource.get_token() == 'explicitly-replaced-fixture'


@pytest.fixture(scope='module')
def relocated(tmp_path_factory):
    root = tmp_path_factory.mktemp('relocated') / 'checkout'
    root.mkdir()
    for name in ('scripts', 'skills'):
        shutil.copytree(ROOT / name, root / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.env', 'cache'))
    return root


@pytest.mark.parametrize('entry', ENTRIES)
def test_direct_entrypoints_without_editable_install(relocated, tmp_path, entry):
    env = os.environ.copy()
    env['TZ_CODEX_HOME'] = str(tmp_path / 'external-data')
    env.pop('PYTHONPATH', None)
    env.pop('EVENT_MAP_DB', None)
    result = subprocess.run([sys.executable, '-I', str(relocated / entry), '--help'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()


def test_all_event_consumers_share_override_in_clean_process(relocated, tmp_path):
    env = os.environ.copy()
    env['TZ_CODEX_HOME'] = str(tmp_path / 'workspace')
    env['EVENT_MAP_DB'] = 'isolated/event-map.db'
    program = '''
import importlib.util, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
rows = {}
for relative, attr in [
    ('scripts/event_db.py', 'DEFAULT_DB'),
    ('skills/shared/event_store.py', 'DEFAULT_DB'),
    ('skills/market-outlook/scripts/market_opportunity_radar.py', 'EVENT_DB'),
    ('skills/market-outlook/scripts/catalyst_timeline.py', 'EVENT_DB'),
    ('skills/market-outlook/scripts/catalyst_dossier.py', 'EVENT_DB'),
    ('skills/stock-analysis/scripts/event_map_query.py', 'EVENT_DB'),
    ('skills/top-picks/references/catalyst_left_side_scanner.py', 'EVENT_DB'),
]:
    spec = importlib.util.spec_from_file_location('probe_' + str(len(rows)), root / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    rows[relative] = str(getattr(module, attr))
print(json.dumps(rows))
'''
    result = subprocess.run([sys.executable, '-I', '-c', program, str(relocated)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert set(rows.values()) == {str(tmp_path / 'workspace/isolated/event-map.db')}


@pytest.mark.parametrize('relative,function', [
    ('skills/market-outlook/scripts/market_brief_v2.py', '_get_pro'),
    ('skills/market-outlook/scripts/market_opportunity_radar.py', 'get_pro'),
    ('skills/market-outlook/scripts/catalyst_dossier.py', '_load_pro'),
    ('skills/stock-analysis/scripts/market_state_fetcher.py', '_get_pro'),
    ('skills/stock-analysis/scripts/daily_snapshot.py', '_get_pro'),
    ('skills/top-picks/references/catalyst_left_side_scanner.py', '_get_pro'),
    ('skills/quality-compounder/references/quality_compounder_screener.py', 'get_pro'),
    ('skills/stock-analysis/references/stock_data_fetcher.py', '_get_tushare_pro'),
])
def test_consumers_use_same_project_token(workspace, monkeypatch, relative, function):
    (workspace / 'repo/.env').write_text('TUSHARE_TOKEN="project-fixture"\n')
    monkeypatch.setenv('TUSHARE_TOKEN', 'obsolete-fixture')
    monkeypatch.setitem(sys.modules, 'tushare', SimpleNamespace(pro_api=lambda token: token))
    spec = importlib.util.spec_from_file_location('consumer_probe', ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert getattr(module, function)() == 'project-fixture'
