import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote, urlparse

from librivox_trimmer.metadata import read_track
from librivox_trimmer.settings import DetectionSettings


PROJECT_FORMAT_VERSION = 1
PROJECT_SUFFIX = ".lvproj"


@dataclass
class TrackReview:
    keep_start_seconds: float | None = None
    keep_end_seconds: float | None = None
    intro_removal_start_seconds: float = 0.0
    outro_resume_start_seconds: float | None = None
    intro_review_reason: str | None = None
    outro_review_reason: str | None = None

    @property
    def keep_range(self) -> tuple[float, float] | None:
        if self.keep_start_seconds is None or self.keep_end_seconds is None:
            return None
        return self.keep_start_seconds, self.keep_end_seconds


@dataclass(frozen=True)
class ProjectTrack:
    path: Path
    title: str
    artist: str
    duration_seconds: float
    size_bytes: int
    modified_ns: int
    review: TrackReview


@dataclass(frozen=True)
class ProjectDocument:
    path: Path
    name: str
    source_url: str
    created_at: str
    updated_at: str
    managed_audio_dir: Path | None
    tracks: tuple[ProjectTrack, ...]
    detection_settings: DetectionSettings | None = None


def project_assets_dir(project_path: Path) -> Path:
    project_path = project_path.resolve()
    return project_path.with_name(f"{project_path.stem}_files")


def new_project_path_for_url(project_url: str, projects_directory: Path) -> Path:
    url_path = urlparse(project_url).path.rstrip("/")
    name = unquote(url_path.rsplit("/", 1)[-1]) or "librivox-project"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip(" .")
    name = name[:100] or "librivox-project"
    projects_directory = projects_directory.expanduser()
    projects_directory.mkdir(parents=True, exist_ok=True)

    candidate = projects_directory / f"{name}{PROJECT_SUFFIX}"
    suffix = 2
    while candidate.exists() or project_assets_dir(candidate).exists():
        candidate = projects_directory / f"{name} ({suffix}){PROJECT_SUFFIX}"
        suffix += 1
    return candidate


def _path_reference(path: Path, project_directory: Path) -> dict[str, str]:
    resolved_path = path.resolve(strict=True)
    try:
        relative_path = resolved_path.relative_to(project_directory)
    except ValueError:
        return {"type": "absolute", "path": str(resolved_path)}
    return {"type": "relative", "path": relative_path.as_posix()}


def _resolve_reference(reference: dict[str, str], project_directory: Path) -> Path:
    path_type = reference.get("type")
    value = reference.get("path")
    if not isinstance(value, str) or not value:
        raise ValueError("Project contains an invalid file path.")
    if path_type == "absolute":
        return Path(value)
    if path_type != "relative":
        raise ValueError(f"Unsupported project path type: {path_type}")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Project contains an unsafe relative file path.")
    resolved = (project_directory / relative).resolve()
    if not resolved.is_relative_to(project_directory):
        raise ValueError("Project file path escapes its project directory.")
    return resolved


def save_project(
    project_path: Path,
    tracks: list[Path],
    source_url: str = "",
    managed_audio_dir: Path | None = None,
    reviews: Mapping[Path, TrackReview] | None = None,
    detection_settings: DetectionSettings | None = None,
) -> ProjectDocument:
    project_path = project_path.resolve()
    project_directory = project_path.parent.resolve()
    project_directory.mkdir(parents=True, exist_ok=True)
    expected_assets_dir = project_assets_dir(project_path)
    if managed_audio_dir is not None:
        managed_audio_dir = managed_audio_dir.resolve()
        if managed_audio_dir != expected_assets_dir:
            raise ValueError("Managed audio must be stored in the project's own files directory.")

    now = datetime.now(timezone.utc).isoformat()
    created_at = now
    if project_path.exists():
        existing = load_project(project_path)
        created_at = existing.created_at
        if detection_settings is None:
            detection_settings = existing.detection_settings
    if detection_settings is not None:
        detection_settings.validate()

    serialized_tracks: list[dict[str, object]] = []
    for track_path in tracks:
        track_path = track_path.resolve(strict=True)
        if track_path.suffix.lower() != ".mp3":
            raise ValueError(f"Project tracks must be MP3 files: {track_path}")
        duration, title, artist = read_track(track_path)
        file_stat = track_path.stat()
        review: dict[str, object] = {}
        track_review = (reviews or {}).get(track_path)
        if track_review is not None and track_review.keep_range is not None:
            keep_start, keep_end = track_review.keep_range
            if keep_start < 0 or keep_end <= keep_start or keep_end > duration:
                raise ValueError(f"Invalid saved keep range for {track_path.name}.")
            review = {
                "keep_start_seconds": float(keep_start),
                "keep_end_seconds": float(keep_end),
            }
            removal_start = track_review.intro_removal_start_seconds
            if not math.isfinite(removal_start) or removal_start < 0 or (
                removal_start > 0 and removal_start >= keep_start
            ):
                raise ValueError(f"Invalid intro notice range for {track_path.name}.")
            review["intro_removal_start_seconds"] = float(removal_start)
            resume = track_review.outro_resume_start_seconds
            if resume is not None:
                if not math.isfinite(resume) or not keep_end < resume < duration:
                    raise ValueError(f"Invalid retained ending for {track_path.name}.")
                review["outro_resume_start_seconds"] = float(resume)
        # Saved even without a cut range: a missed notice or marker leaves nothing else to record.
        if track_review is not None and track_review.intro_review_reason:
            review["intro_review_reason"] = str(track_review.intro_review_reason)
        if track_review is not None and track_review.outro_review_reason:
            review["outro_review_reason"] = str(track_review.outro_review_reason)
        serialized_tracks.append(
            {
                "file": _path_reference(track_path, project_directory),
                "title": title,
                "artist": artist,
                "duration_seconds": duration,
                "size_bytes": file_stat.st_size,
                "modified_ns": file_stat.st_mtime_ns,
                "review": review,
            }
        )

    managed_reference = None
    if managed_audio_dir is not None:
        managed_reference = _path_reference(managed_audio_dir, project_directory)

    payload = {
        "format_version": PROJECT_FORMAT_VERSION,
        "name": project_path.stem,
        "source_url": source_url,
        "created_at": created_at,
        "updated_at": now,
        "managed_audio_dir": managed_reference,
        "tracks": serialized_tracks,
        "detection_settings": detection_settings.to_dict() if detection_settings else None,
    }
    project_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=project_path.parent,
            prefix=f".{project_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, project_path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return load_project(project_path)


def load_project(project_path: Path) -> ProjectDocument:
    project_path = project_path.resolve(strict=True)
    with project_path.open("r", encoding="utf-8") as project_file:
        payload = json.load(project_file)
    if not isinstance(payload, dict):
        raise ValueError("Project file must contain a JSON object.")
    if payload.get("format_version") != PROJECT_FORMAT_VERSION:
        raise ValueError("Unsupported project file version.")

    project_directory = project_path.parent.resolve()
    managed_reference = payload.get("managed_audio_dir")
    managed_audio_dir = None
    if managed_reference is not None:
        if not isinstance(managed_reference, dict):
            raise ValueError("Project contains an invalid managed audio directory.")
        managed_audio_dir = _resolve_reference(managed_reference, project_directory)
        if managed_audio_dir != project_assets_dir(project_path):
            raise ValueError("Project managed audio directory is not a recognized project folder.")

    raw_tracks = payload.get("tracks")
    if not isinstance(raw_tracks, list):
        raise ValueError("Project file has no valid track list.")
    tracks: list[ProjectTrack] = []
    for raw_track in raw_tracks:
        if not isinstance(raw_track, dict) or not isinstance(raw_track.get("file"), dict):
            raise ValueError("Project contains an invalid track entry.")
        path = _resolve_reference(raw_track["file"], project_directory)
        review = raw_track.get("review", {})
        if not isinstance(review, dict):
            raise ValueError("Project contains invalid review settings.")
        tracks.append(
            ProjectTrack(
                path=path,
                title=str(raw_track.get("title", path.stem)),
                artist=str(raw_track.get("artist", "")),
                duration_seconds=float(raw_track.get("duration_seconds", 0)),
                size_bytes=int(raw_track.get("size_bytes", 0)),
                modified_ns=int(raw_track.get("modified_ns", 0)),
                review=TrackReview(
                    keep_start_seconds=(
                        float(review["keep_start_seconds"])
                        if "keep_start_seconds" in review else None
                    ),
                    keep_end_seconds=(
                        float(review["keep_end_seconds"])
                        if "keep_end_seconds" in review else None
                    ),
                    intro_removal_start_seconds=float(review.get("intro_removal_start_seconds", 0.0)),
                    outro_resume_start_seconds=(
                        float(review["outro_resume_start_seconds"])
                        if review.get("outro_resume_start_seconds") is not None else None
                    ),
                    intro_review_reason=(
                        str(review["intro_review_reason"])
                        if review.get("intro_review_reason") else None
                    ),
                    outro_review_reason=(
                        str(review["outro_review_reason"])
                        if review.get("outro_review_reason") else None
                    ),
                ),
            )
        )
    return ProjectDocument(
        path=project_path,
        name=str(payload.get("name", project_path.stem)),
        source_url=str(payload.get("source_url", "")),
        created_at=str(payload.get("created_at", "")),
        updated_at=str(payload.get("updated_at", "")),
        managed_audio_dir=managed_audio_dir,
        tracks=tuple(tracks),
        detection_settings=(
            DetectionSettings.from_dict(payload["detection_settings"])
            if payload.get("detection_settings") is not None else None
        ),
    )


def cleanup_project(project_path: Path) -> None:
    project = load_project(project_path)
    if project.managed_audio_dir is not None:
        assets_dir = project.managed_audio_dir
        if assets_dir.is_symlink():
            raise ValueError("Refusing to clean up a project files directory that is a symbolic link.")
        if assets_dir.exists():
            shutil.rmtree(assets_dir)
    project.path.unlink()