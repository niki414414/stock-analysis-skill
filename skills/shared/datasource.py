"""Shared configuration and Tushare client construction; no market decisions.

The project token overrides a stale inherited token. Other keys remain
process-first. Workspace/database selection comes from the launch environment,
so loading .env cannot silently redirect another module after imports.
"""
import os
import re
from pathlib import Path
from typing import Optional

from skills.shared.paths import config_file

LOCATION_KEYS = {"TZ_CODEX_HOME", "EVENT_MAP_DB"}
_project_token: Optional[str] = None
_inherited_token: Optional[str] = None


def load_env(path: Optional[Path] = None) -> None:
    global _project_token, _inherited_token
    # Undo only our own previous token injection. A subsequent explicit process
    # change remains authoritative when the new project has no configured token.
    if _project_token is not None and os.environ.get("TUSHARE_TOKEN") == _project_token:
        if _inherited_token is None:
            os.environ.pop("TUSHARE_TOKEN", None)
        else:
            os.environ["TUSHARE_TOKEN"] = _inherited_token
    _project_token = None
    _inherited_token = None
    target = Path(path) if path is not None else config_file()
    if not target.exists():
        return
    for number, raw in enumerate(target.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"项目配置第{number}行格式错误: {target}")
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
                raise ValueError(f"项目配置第{number}行引号错误: {target}")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if key in LOCATION_KEYS or not value:
            continue
        if key == "TUSHARE_TOKEN":
            if _project_token is None:
                _inherited_token = os.environ.get(key)
            _project_token = value
            os.environ[key] = value
        elif key not in os.environ:
            os.environ[key] = value


def get_token(env_path: Optional[Path] = None) -> str:
    load_env(env_path)
    return os.environ.get("TUSHARE_TOKEN", "").strip()


def get_pro(env_path: Optional[Path] = None, *, fallback_token: str = ""):
    """Create lazily, without a cached client/token leaking across workspaces."""
    token = get_token(env_path) or fallback_token.strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN未配置；请检查项目repo/.env或启动环境")
    import tushare as ts
    return ts.pro_api(token)
