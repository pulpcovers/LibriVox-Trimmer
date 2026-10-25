import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from librivox_trimmer.config import default_output_dir, projects_dir, user_data_dir


MODEL_CHOICES = ("tiny.en", "base.en", "small.en", "medium.en")


@dataclass(frozen=True)
class DetectionSettings:
    model_size: str = "base.en"
    detect_intro: bool = True
    detect_outro: bool = True
    intro_window_seconds: float = 60.0
    outro_window_seconds: float = 30.0
    intro_safety_gap_seconds: float = 0.6
    refine_outro: bool = True

    def validate(self) -> None:
        if self.model_size not in MODEL_CHOICES:
            raise ValueError("Unsupported speech model.")
        for value in (self.detect_intro, self.detect_outro, self.refine_outro):
            if type(value) is not bool:
                raise ValueError("Detection switches must be true or false.")
        if not self.detect_intro and not self.detect_outro:
            raise ValueError("Enable intro detection, outro detection, or both.")
        for value in (self.intro_window_seconds, self.outro_window_seconds):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 5 <= value <= 600:
                raise ValueError("Search windows must be between 5 and 600 seconds.")
        value = self.intro_safety_gap_seconds
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 3:
            raise ValueError("Intro margin must be between 0 and 3 seconds.")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict) -> "DetectionSettings":
        settings = cls(**values)
        settings.validate()
        return settings


@dataclass(frozen=True)
class AppSettings:
    projects_directory: str = ""
    output_directory: str = ""
    download_quality: str = "best"
    detection: DetectionSettings = field(default_factory=DetectionSettings)

    def validate(self) -> None:
        self.detection.validate()
        if self.download_quality not in ("best", "smaller"):
            raise ValueError("Unsupported download quality.")
        for value in (self.projects_directory, self.output_directory):
            if not isinstance(value, str) or (value and not Path(value).expanduser().is_absolute()):
                raise ValueError("Storage folders must be absolute paths.")

    def project_folder(self) -> Path:
        return Path(self.projects_directory).expanduser() if self.projects_directory else projects_dir()

    def output_folder(self) -> Path:
        return Path(self.output_directory).expanduser() if self.output_directory else default_output_dir()


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    path = path or settings_path()
    if not path.exists():
        return AppSettings()
    with path.open(encoding="utf-8") as source:
        values = json.load(source)
    if not isinstance(values, dict):
        raise ValueError("Preferences must contain a JSON object.")
    if not isinstance(values.get("detection", {}), dict):
        raise ValueError("Invalid detection preferences.")
    detection = DetectionSettings.from_dict(values.pop("detection", {}))
    result = AppSettings(detection=detection, **values)
    result.validate()
    return result


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    settings.validate()
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as target:
            temporary_path = Path(target.name)
            json.dump(asdict(settings), target, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)