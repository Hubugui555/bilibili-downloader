"""Configuration, credentials, and user settings persistence."""

import json
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from .constants import DEFAULT_SETTINGS, HEADERS

_CONFIG_LOCK = threading.RLock()


def app_root() -> Path:
    """Return the application root directory (exe dir if frozen, repo root otherwise)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Persistent non-credential state directory (settings, history DB)."""
    path = app_root() / "bilidown_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    return app_root() / "downloads"


def config_path() -> Path:
    """config.json sits next to the app root so Windows & Linux deployments match."""
    return app_root() / "config.json"


def cloudreve_config_path() -> Path:
    return app_root() / "cloudreve_config.json"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def _atomic_write_json(path: Path, payload: dict, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    if private and os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _read_json_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ─── Credentials (config.json) ───────────────────────────────────────────

def load_config(path: Optional[Path] = None) -> dict:
    with _CONFIG_LOCK:
        return _read_json_dict(path or config_path())


def save_config(cfg: dict, path: Optional[Path] = None) -> None:
    with _CONFIG_LOCK:
        _atomic_write_json(path or config_path(), cfg, private=True)


def clear_credentials(path: Optional[Path] = None) -> None:
    with _CONFIG_LOCK:
        target = path or config_path()
        if target.exists():
            try:
                target.unlink()
            except OSError:
                _atomic_write_json(target, {}, private=True)


def get_sessdata(path: Optional[Path] = None) -> str:
    return load_config(path).get("sessdata", "")


def get_cookie_string(path: Optional[Path] = None) -> str:
    data = load_config(path)
    if data.get("cookie"):
        return data["cookie"]
    if data.get("cookies"):
        return "; ".join(f"{k}={v}" for k, v in data["cookies"].items())
    if data.get("sessdata"):
        return f"SESSDATA={data['sessdata']}"
    return ""


def save_cookie_string(cookie: str, path: Optional[Path] = None) -> dict:
    cookie = (cookie or "").strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    cookies = {}
    for item in cookie.split(";"):
        if "=" not in item:
            continue
        key, value = item.strip().split("=", 1)
        if key:
            cookies[key] = value
    sessdata = cookies.get("SESSDATA", "")
    if not sessdata:
        raise ValueError("Cookie 中未找到 SESSDATA，请粘贴已登录的 bilibili.com 请求 Cookie")
    cfg = {"sessdata": sessdata, "cookie": cookie, "cookies": cookies}
    save_config(cfg, path=path)
    return cfg


def build_headers(cookie_override: Optional[str] = None, path: Optional[Path] = None) -> dict:
    h = HEADERS.copy()
    cookie = cookie_override if cookie_override is not None else get_cookie_string(path)
    if cookie:
        h["Cookie"] = cookie
    return h


# ─── Cloudreve Config ────────────────────────────────────────────────────

def load_cloudreve_config() -> Optional[dict]:
    """Load server cloudreve_config.json if present; return None in local mode."""
    cfg = _read_json_dict(cloudreve_config_path())
    if cfg.get("base_url") and cfg.get("username"):
        return {
            "base_url": cfg["base_url"],
            "username": cfg["username"],
            "password": cfg.get("password", ""),
            "remote_dir": cfg.get("remote_dir", "/视频/B站"),
        }
    return None


# ─── User Settings (bilidown_data/settings.json) ─────────────────────────

def load_settings() -> dict:
    with _CONFIG_LOCK:
        merged = dict(DEFAULT_SETTINGS)
        merged["output_dir"] = str(default_output_dir())
        cloud = load_cloudreve_config()
        if cloud:
            merged["cloudreve_enabled"] = True
            merged["cloudreve_url"] = cloud["base_url"]
            merged["cloudreve_username"] = cloud["username"]
            merged["cloudreve_password"] = cloud["password"]
            merged["cloudreve_remote_dir"] = cloud["remote_dir"]

        saved = _read_json_dict(settings_path())
        for key, value in saved.items():
            if key in merged:
                merged[key] = value
        if not merged.get("output_dir"):
            merged["output_dir"] = str(default_output_dir())
        return merged


def save_settings(updates: dict) -> dict:
    with _CONFIG_LOCK:
        current = load_settings()
        for key, value in updates.items():
            if key in DEFAULT_SETTINGS:
                current[key] = value
        if not current.get("output_dir"):
            current["output_dir"] = str(default_output_dir())
        _atomic_write_json(settings_path(), current, private=True)
        return current


def effective_cloudreve_config(settings: Optional[dict] = None) -> Optional[dict]:
    """Resolve active Cloudreve configuration from file or user settings."""
    s = settings or load_settings()
    if s.get("cloudreve_enabled") and s.get("cloudreve_url") and s.get("cloudreve_username"):
        return {
            "base_url": s["cloudreve_url"],
            "username": s["cloudreve_username"],
            "password": s.get("cloudreve_password", ""),
            "remote_dir": s.get("cloudreve_remote_dir") or "/视频/B站",
        }
    file_cfg = load_cloudreve_config()
    if file_cfg and s.get("cloudreve_enabled", True):
        return file_cfg
    return None
