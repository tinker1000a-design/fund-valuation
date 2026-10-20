"""Relocatable paths for Fund System.

Set FUND_SYSTEM_DATA_DIR to move runtime data away from the application folder.
Without it, data remains beside the scripts for backward compatibility.
"""
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
_configured_data_dir = os.environ.get("FUND_SYSTEM_DATA_DIR", "").strip()
if not _configured_data_dir:
    settings_file = APP_DIR / "fund_system_paths.json"
    try:
        import json
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        _configured_data_dir = str(settings.get("data_dir", "")).strip()
    except (OSError, ValueError, AttributeError):
        _configured_data_dir = ""
DATA_DIR = Path(_configured_data_dir).expanduser() if _configured_data_dir else APP_DIR
if not DATA_DIR.is_absolute():
    DATA_DIR = (APP_DIR / DATA_DIR).resolve()


def data_path(filename):
    """Return a runtime data path and create its parent directory."""
    path = DATA_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
