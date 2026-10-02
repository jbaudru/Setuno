"""Tiny local JSON settings store (theme preference, library folders, etc.), next to the library data."""
import json
from pathlib import Path

from .database import default_data_dir

_DEFAULTS = {
    "theme": "light", "library_folders": [],
    "show_level_meter": True,
    "library_visible_columns": None,
    "auto_mix": True,
    "auto_mix_bpm_sync": True,
    "auto_mix_energy_match": False,
    "auto_mix_duration_ms": 16000,
}


def _settings_path() -> Path:
    return default_data_dir() / "settings.json"


def load_settings() -> dict:
    path = _settings_path()
    if path.exists():
        try:
            return {**_DEFAULTS, **json.loads(path.read_text(encoding="utf-8"))}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS)


def save_settings(settings: dict):
    path = _settings_path()
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
