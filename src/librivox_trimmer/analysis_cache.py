"""Remembers the slow, deterministic audio work (transcripts, speech regions, silences) per track.

Re-analysing after a logic change then reuses the recorded audio results instead of running Whisper again.
"""

import hashlib
import importlib.metadata
import json
import os
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from librivox_trimmer.config import analysis_cache_dir
from librivox_trimmer.download_state import ANALYSIS_CACHE_FOLDER, is_managed_assets_dir
from librivox_trimmer.transcription import TimedWord


FORMAT_VERSION = 1
MAX_AGE_SECONDS = 90 * 24 * 3600
_active: ContextVar["AnalysisCache | None"] = ContextVar("analysis_cache", default=None)
_pruned = False


def active_cache() -> "AnalysisCache | None":
    return _active.get()


def _asr_identity() -> str:
    try:
        return importlib.metadata.version("faster-whisper")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _fingerprint(path: Path) -> str:
    """Identify a file by size and its first and last megabyte, which is cheap and survives renames."""
    size = path.stat().st_size
    digest = hashlib.sha1(str(size).encode())
    with path.open("rb") as source:
        digest.update(source.read(1 << 20))
        if size > 2 << 20:
            source.seek(-(1 << 20), os.SEEK_END)
            digest.update(source.read(1 << 20))
    return digest.hexdigest()


def _cache_directory(track_path: Path) -> tuple[Path, bool]:
    """Where a track's cache lives, and whether that is inside its project.

    A project keeps the cache in its own files folder so Clean Up removes it; tracks outside any project
    share one app-wide folder.
    """
    try:
        parents = list(Path(track_path).resolve().parents)
    except OSError:
        parents = []
    for parent in parents:
        if parent.name.endswith("_files") and is_managed_assets_dir(parent):
            return parent / ANALYSIS_CACHE_FOLDER, True
    return analysis_cache_dir(), False


def _prune(directory: Path) -> None:
    global _pruned
    if _pruned:
        return
    _pruned = True
    cutoff = time.time() - MAX_AGE_SECONDS
    for entry in directory.glob("*.json"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def _window_key(start: float, end: float) -> str:
    return f"{start:.3f}-{end:.3f}"


class AnalysisCache:
    def __init__(self, data: dict | None = None, path: Path | None = None, *, model_size: str = "") -> None:
        self._path = path
        self.model_size = model_size
        self._data = data or {}
        for section in ("transcripts", "regions", "silences"):
            self._data.setdefault(section, {})
        self._data["version"] = FORMAT_VERSION
        self._data["asr"] = _asr_identity()
        self._dirty = False

    @classmethod
    def for_track(cls, track_path: Path, model_size: str) -> "AnalysisCache | None":
        """The on-disk cache for a track, or None when the file cannot be read."""
        try:
            fingerprint = _fingerprint(Path(track_path))
        except OSError:
            return None
        directory, in_project = _cache_directory(track_path)
        path = directory / f"{fingerprint}-{model_size}.json"
        data = None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("version") == FORMAT_VERSION and loaded.get("asr") == _asr_identity():
                data = loaded
        except (OSError, ValueError):
            pass
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if not in_project:
                _prune(directory)
        except OSError:
            return None
        return cls(data, path, model_size=model_size)

    @classmethod
    def from_snapshot(cls, snapshot: dict, *, model_size: str = "") -> "AnalysisCache":
        return cls(json.loads(json.dumps(snapshot)), None, model_size=model_size)

    def snapshot(self) -> dict:
        return json.loads(json.dumps(self._data))

    def get_words(self, start: float, end: float) -> list[TimedWord] | None:
        stored = self._data["transcripts"].get(_window_key(start, end))
        if stored is None:
            return None
        return [TimedWord(text, word_start, word_end, probability) for text, word_start, word_end, probability in stored]

    def put_words(self, start: float, end: float, words: list[TimedWord]) -> None:
        self._data["transcripts"][_window_key(start, end)] = [
            [word.text, word.start, word.end, word.probability] for word in words
        ]
        self._dirty = True

    def get_regions(self, start: float, end: float) -> list[tuple[float, float]] | None:
        stored = self._data["regions"].get(_window_key(start, end))
        return None if stored is None else [(region_start, region_end) for region_start, region_end in stored]

    def put_regions(self, start: float, end: float, regions: list[tuple[float, float]]) -> None:
        self._data["regions"][_window_key(start, end)] = [list(region) for region in regions]
        self._dirty = True

    def get_silences(self, start: float, end: float, minimum: float) -> list[tuple[float, float]] | None:
        stored = self._data["silences"].get(f"{_window_key(start, end)}-{minimum:.2f}")
        return None if stored is None else [(silence_start, silence_end) for silence_start, silence_end in stored]

    def put_silences(self, start: float, end: float, minimum: float, silences: list[tuple[float, float]]) -> None:
        self._data["silences"][f"{_window_key(start, end)}-{minimum:.2f}"] = [list(silence) for silence in silences]
        self._dirty = True

    def save(self) -> None:
        if self._path is None or not self._dirty:
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._path.parent, suffix=".tmp", delete=False,
            ) as target:
                temporary = Path(target.name)
                json.dump(self._data, target)
            os.replace(temporary, self._path)
            self._dirty = False
        except OSError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


@contextmanager
def use_cache(cache: "AnalysisCache | None"):
    token = _active.set(cache)
    try:
        yield cache
    finally:
        _active.reset(token)


@contextmanager
def analysis_session(track_path: Path, model_size: str):
    """Use the track's on-disk cache for the duration of one analysis, unless a cache is already active."""
    existing = _active.get()
    if existing is not None:
        yield existing
        return
    cache = AnalysisCache.for_track(track_path, model_size)
    with use_cache(cache):
        try:
            yield cache
        finally:
            if cache is not None:
                cache.save()
