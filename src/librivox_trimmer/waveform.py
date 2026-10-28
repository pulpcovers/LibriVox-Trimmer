"""Short waveform windows and MP3 frame alignment for placing cuts precisely."""

import math
import re
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path

from librivox_trimmer.audio import _run_ffmpeg, resolve_ffmpeg


WAVEFORM_SAMPLE_RATE = 8000


@dataclass(frozen=True)
class FrameGrid:
    """MP3 frame boundaries: origin + n * frame_seconds on the source timeline."""

    origin: float
    frame_seconds: float

    def boundary_at_or_before(self, time: float) -> float:
        index = math.floor((time - self.origin) / self.frame_seconds + 1e-9)
        return self.origin + index * self.frame_seconds

    def boundary_at_or_after(self, time: float) -> float:
        index = math.ceil((time - self.origin) / self.frame_seconds - 1e-9)
        return self.origin + index * self.frame_seconds

    def nearest_boundary(self, time: float) -> float:
        return self.origin + round((time - self.origin) / self.frame_seconds) * self.frame_seconds

    def exported_cut(self, value: float, *, starts_kept_audio: bool) -> float:
        # Stream-copy export keeps every frame that overlaps the kept interval.
        if starts_kept_audio:
            return self.boundary_at_or_before(value)
        return self.boundary_at_or_after(value)

    def value_for_cut(self, boundary: float, *, starts_kept_audio: bool) -> float:
        # A mid-frame value stays on the intended side of the boundary after rounding.
        half = self.frame_seconds / 2
        return boundary + half if starts_kept_audio else boundary - half


@dataclass(frozen=True)
class WaveformWindow:
    start: float
    sample_rate: int
    samples: array

    @property
    def end(self) -> float:
        return self.start + len(self.samples) / self.sample_rate


def read_frame_grid(source_path: Path) -> FrameGrid | None:
    command = [
        resolve_ffmpeg(), "-hide_banner", "-loglevel", "error",
        "-t", "0.5", "-i", str(source_path),
        "-map", "0:a:0", "-c:a", "copy", "-f", "framecrc", "-",
    ]
    try:
        result = _run_ffmpeg(command)
    except OSError:
        return None
    timebase = re.search(r"^#tb 0: (\d+)/(\d+)", result.stdout or "", re.MULTILINE)
    rows = [
        line.split(",") for line in (result.stdout or "").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if result.returncode != 0 or timebase is None or not rows:
        return None
    try:
        unit = int(timebase.group(1)) / int(timebase.group(2))
        first_pts = int(rows[0][2])
        frame_seconds = int(rows[0][3]) * unit
    except (IndexError, ValueError):
        return None
    if frame_seconds <= 0:
        return None
    return FrameGrid(origin=(first_pts * unit) % frame_seconds, frame_seconds=frame_seconds)


def read_waveform(
    source_path: Path, start: float, end: float, *, sample_rate: int = WAVEFORM_SAMPLE_RATE,
) -> WaveformWindow:
    if start < 0 or end <= start:
        raise ValueError("Waveform window must have a non-negative start before its end.")
    start = round(start, 3)
    command = [
        resolve_ffmpeg(), "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(source_path),
        "-t", f"{end - start:.3f}", "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-f", "s16le", "-",
    ]
    result = _run_ffmpeg(command, binary=True)
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(message or "FFmpeg could not decode the waveform.")
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        raise RuntimeError("FFmpeg returned no audio for the waveform window.")
    return WaveformWindow(start=start, sample_rate=sample_rate, samples=samples)


def column_peaks(samples: array, columns: int) -> list[tuple[int, int]]:
    """Minimum and maximum sample for each drawing column."""
    count = len(samples)
    if columns <= 0 or count == 0:
        return []
    peaks = []
    for column in range(columns):
        first = column * count // columns
        last = max(first + 1, (column + 1) * count // columns)
        chunk = samples[first:last]
        peaks.append((min(chunk), max(chunk)))
    return peaks
