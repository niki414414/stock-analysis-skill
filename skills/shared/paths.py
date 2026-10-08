"""One location contract for code, workspace data and the event primary store.

Code always belongs to this checkout. TZ_CODEX_HOME relocates data/configuration,
not Python imports. Relative overrides resolve against the workspace, never cwd.
"""
import os
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def workspace_root() -> Path:
    value = os.environ.get("TZ_CODEX_HOME", "").strip()
    if not value:
        return repo_root().parent
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("TZ_CODEX_HOME必须是绝对路径，避免不同入口读取不同工作区")
    return path.resolve()


def config_file() -> Path:
    if os.environ.get("TZ_CODEX_HOME", "").strip():
        return workspace_root() / "repo" / ".env"
    return repo_root() / ".env"


def event_db_path() -> Path:
    value = os.environ.get("EVENT_MAP_DB", "").strip()
    if not value:
        return workspace_root() / "技能数据" / "event_map_shadow.db"
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (workspace_root() / path).resolve()
