import os
from pathlib import Path


APP_DIRECTORY_NAME = "LibriVoxTrimmer"


def user_data_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif os.name == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / APP_DIRECTORY_NAME


def projects_dir() -> Path:
    return user_data_dir() / "Projects"


def model_cache_dir() -> Path:
    return user_data_dir() / "models"


def analysis_cache_dir() -> Path:
    override = os.environ.get("LIBRIVOX_ANALYSIS_CACHE_DIR")
    return Path(override) if override else user_data_dir() / "analysis-cache"


def default_output_dir() -> Path:
    return Path.home() / "Music" / "LibriVox Trimmer"