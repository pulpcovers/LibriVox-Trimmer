import os
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from imageio_ffmpeg import get_ffmpeg_exe

from librivox_trimmer.metadata import read_track, restore_id3_tags


def _run_ffmpeg(command: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(
        command, check=check, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, creationflags=creation_flags,
    )


def resolve_ffmpeg() -> str:
    configured_path = os.environ.get("LIBRIVOX_FFMPEG")
    if configured_path:
        path = Path(configured_path)
        if path.is_file():
            return str(path)
        raise FileNotFoundError(f"LIBRIVOX_FFMPEG does not point to a file: {path}")
    return shutil.which("ffmpeg") or get_ffmpeg_exe()


def extract_analysis_slice(
    source_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> Path:
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("Audio slice must have a non-negative start before its end.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        resolve_ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{end_seconds - start_seconds:.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    try:
        _run_ffmpeg(command, check=True)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg produced an empty analysis audio slice.")
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return output_path


def _detect_silences(
    source_path: Path, start: float, end: float, minimum_seconds: float,
) -> list[tuple[float, float]] | None:
    """Return complete pauses between start and end in source time, or None if FFmpeg fails."""
    command = [
        resolve_ffmpeg(), "-hide_banner", "-nostats", "-loglevel", "info",
        "-ss", f"{start:.3f}", "-i", str(source_path),
        "-t", f"{end - start:.3f}", "-vn", "-af",
        f"silencedetect=noise=-35dB:d={minimum_seconds}", "-f", "null", "-",
    ]
    try:
        result = _run_ffmpeg(command)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    silences = []
    silence_start = None
    for kind, raw_time in re.findall(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)", result.stderr):
        event_time = start + float(raw_time)
        if kind == "start":
            silence_start = event_time
        elif silence_start is not None:
            silences.append((silence_start, event_time))
            silence_start = None
    return silences


def reader_credit_pause(source_path: Path, cue_end: float, window_end: float) -> tuple[float, float] | None:
    pauses = _detect_silences(source_path, max(0.0, cue_end - 0.5), window_end, 0.75)
    return next((
        (start, end) for start, end in pauses or []
        if start >= cue_end and end < window_end - 0.05 and end - start >= 0.75
    ), None)


def refine_outro_boundary_with_silence(
    source_path: Path,
    candidate_time: float,
    *,
    alignment_tolerance_seconds: float = 0.5,
    minimum_silence_seconds: float = 0.35,
    maximum_lookahead_seconds: float = 4.0,
) -> float:
    """Move an outro cut to speech onset only when a pause aligns with its match."""
    if candidate_time < 0:
        return candidate_time

    source_duration, _, _ = read_track(source_path)
    window_start = max(0.0, candidate_time - max(2.0, alignment_tolerance_seconds * 4))
    window_end = min(source_duration, candidate_time + maximum_lookahead_seconds)
    if window_end <= window_start:
        return candidate_time

    for silence_start, silence_end in _detect_silences(source_path, window_start, window_end, 0.25) or []:
        if (
            abs(silence_start - candidate_time) <= alignment_tolerance_seconds
            and silence_end - silence_start >= minimum_silence_seconds
            and silence_end > candidate_time
        ):
            return silence_end
    return candidate_time


def refine_cut_into_pause(
    source_path: Path,
    candidate_time: float,
    speech_bound: float,
) -> float:
    """Move a cut into the nearest complete pause, staying before speech_bound."""
    if candidate_time <= 0 or speech_bound <= candidate_time:
        return candidate_time
    refined = _nearby_pause_midpoint(source_path, candidate_time, speech_bound)
    return candidate_time if refined is None else refined


def pause_after_heading(
    source_path: Path,
    number_end: float,
    speech_bound: float,
) -> float | None:
    """Midpoint of a clear pause just after a spoken chapter number, or None."""
    return _nearby_pause_midpoint(
        source_path, number_end, speech_bound, earliest_pause_start=number_end - 0.15,
    )


def refine_intro_resume_start(
    source_path: Path,
    notice_end: float,
    following_word_end: float,
) -> float | None:
    if notice_end < 0 or following_word_end <= notice_end:
        return None
    return _nearby_pause_midpoint(
        source_path, notice_end, following_word_end,
        lookahead_seconds=4.0, earliest_pause_start=notice_end - 0.15,
    )


def _nearby_pause_midpoint(
    source_path: Path,
    candidate_time: float,
    speech_bound: float,
    *,
    lookahead_seconds: float = 1.5,
    earliest_pause_start: float | None = None,
) -> float | None:
    window_start = max(0.0, candidate_time - 1.5)
    window_end = min(speech_bound, candidate_time + lookahead_seconds)
    candidates = []
    for silence_start, silence_end in _detect_silences(source_path, window_start, window_end, 0.35) or []:
        distance = max(silence_start - candidate_time, candidate_time - silence_end, 0.0)
        if (
            silence_start > window_start + 0.05
            and silence_end < window_end - 0.05
            and silence_end - silence_start >= 0.35
            and distance <= 0.6
            and (earliest_pause_start is None or silence_start >= earliest_pause_start)
        ):
            candidates.append((distance, (silence_start + silence_end) / 2))
    return min(candidates)[1] if candidates else None


def build_trim_command(
    ffmpeg_path: str,
    source_path: Path,
    output_path: Path,
    keep_start: float,
    keep_end: float,
) -> list[str]:
    if keep_start < 0 or keep_end <= keep_start:
        raise ValueError("The kept interval must have a non-negative start before its end.")
    duration = keep_end - keep_start
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{keep_start:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0:a:0",
        "-c:a",
        "copy",
        "-map_metadata",
        "0",
        str(output_path),
    ]


def trim_mp3(
    source_path: Path,
    output_path: Path,
    keep_start: float,
    keep_end: float,
    *,
    intro_removal_start: float = 0.0,
    outro_resume_start: float | None = None,
) -> Path:
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if source_path == output_path:
        raise ValueError("Output must not overwrite the source MP3.")
    if output_path.exists():
        raise FileExistsError(f"Output already exists: {output_path}")

    source_duration, _, _ = read_track(source_path)
    if keep_end > source_duration:
        raise ValueError("The kept interval extends beyond the track duration.")
    if not math.isfinite(intro_removal_start) or intro_removal_start < 0 or (
        intro_removal_start > 0 and intro_removal_start >= keep_start
    ):
        raise ValueError("The notice start must be before its end.")
    if outro_resume_start is not None and (
        not math.isfinite(outro_resume_start)
        or not keep_end < outro_resume_start < source_duration
    ):
        raise ValueError("The retained ending must start after the outro cut and before EOF.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg()
    intervals = []
    if intro_removal_start > 0:
        intervals.append((0.0, intro_removal_start))
    intervals.append((keep_start, keep_end))
    if outro_resume_start is not None:
        intervals.append((outro_resume_start, source_duration))
    try:
        if len(intervals) == 1:
            _run_ffmpeg(build_trim_command(ffmpeg, source_path, output_path, keep_start, keep_end), check=True)
        else:
            with TemporaryDirectory(prefix="librivox-join-") as directory:
                temporary = Path(directory)
                lines = []
                for index, (start, end) in enumerate(intervals):
                    name = f"segment-{index}.mp3"
                    _run_ffmpeg(build_trim_command(ffmpeg, source_path, temporary / name, start, end), check=True)
                    lines.append(f"file '{name}'\n")
                listing = temporary / "segments.txt"
                listing.write_text("".join(lines), encoding="utf-8")
                _run_ffmpeg(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "concat", "-safe", "1", "-i", str(listing),
                     "-map", "0:a:0", "-c:a", "copy", str(output_path)],
                    check=True,
                )
        read_track(output_path)
        restore_id3_tags(source_path, output_path)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return output_path