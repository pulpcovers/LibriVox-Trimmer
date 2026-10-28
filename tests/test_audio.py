from pathlib import Path
from types import SimpleNamespace

import pytest

from librivox_trimmer import audio
from librivox_trimmer.audio import (
    build_trim_command,
    extract_analysis_slice,
    refine_outro_boundary_with_silence,
)


@pytest.mark.parametrize("platform,expected_flags", [("win32", 0x08000000), ("linux", 0)])
def test_ffmpeg_launcher_suppresses_windows_console_and_captures_errors(monkeypatch, platform, expected_flags):
    captured = {}
    result = SimpleNamespace(returncode=0, stderr="diagnostics", stdout="")

    def run(command, **options):
        captured.update(command=command, **options)
        return result

    monkeypatch.setattr(audio.sys, "platform", platform)
    monkeypatch.setattr(audio.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(audio.subprocess, "run", run)

    assert audio._run_ffmpeg(["ffmpeg", "-version"], check=True) is result
    assert captured["creationflags"] == expected_flags
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["check"] is True
    assert captured["stdin"] == audio.subprocess.DEVNULL


def test_build_trim_command_uses_stream_copy_and_requested_interval(tmp_path):
    command = build_trim_command(
        "ffmpeg.exe",
        tmp_path / "chapter.mp3",
        tmp_path / "chapter_trimmed.mp3",
        12.25,
        120.5,
    )

    assert command[0] == "ffmpeg.exe"
    assert command[command.index("-ss") + 1] == "12.250"
    assert command[command.index("-t") + 1] == "108.250"
    assert command[command.index("-c:a") + 1] == "copy"
    assert command[-1] == str(tmp_path / "chapter_trimmed.mp3")


@pytest.mark.parametrize("stderr,expected", [
    ("silence_start: 1.93551\nsilence_end: 2.647392", 15.511451),
    ("silence_start: 1.7\nsilence_end: 2.8", 15.47),
    ("silence_start: 0.9\nsilence_end: 1.4", None),
    ("silence_start: 1.8\nsilence_end: 1.9", None),
    ("silence_start: 0\nsilence_end: 2", None),
    ("silence_start: 1.8", None),
    ("", None),
])
def test_intro_resume_uses_complete_pause_after_credit(tmp_path, monkeypatch, stderr, expected):
    monkeypatch.setattr(audio, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audio, "_run_ffmpeg", lambda *_args: SimpleNamespace(returncode=0, stderr=stderr))
    result = audio.refine_intro_resume_start(tmp_path / "chapter.mp3", 14.72, 16.68)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_interior_notice_trim_preserves_two_segments_and_tags(tmp_path):
    from mutagen.mp3 import MP3

    source = tmp_path / "source.mp3"
    target = tmp_path / "trimmed.mp3"
    audio.subprocess.run(
        [audio.resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
         "-metadata", "title=Chapter Three", "-c:a", "libmp3lame", str(source)],
        check=True, capture_output=True,
    )
    audio.trim_mp3(source, target, 5, 7, intro_removal_start=2)
    output = MP3(target)
    assert output.info.length == pytest.approx(4, abs=0.3)
    assert str(output.tags["TIT2"]) == "Chapter Three"
    assert MP3(source).info.length > 7.9


def test_trim_can_keep_opening_narration_and_book_ending(tmp_path):
    from mutagen.mp3 import MP3

    source = tmp_path / "source.mp3"
    target = tmp_path / "joined.mp3"
    audio.subprocess.run(
        [audio.resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-metadata", "title=Book Ending", "-c:a", "libmp3lame", str(source)],
        check=True, capture_output=True,
    )
    audio.trim_mp3(source, target, 4, 7, intro_removal_start=2, outro_resume_start=9)
    result = MP3(target)
    assert result.info.length == pytest.approx(6, abs=0.4)
    assert str(result.tags["TIT2"]) == "Book Ending"


@pytest.mark.parametrize("stderr,expected", [
    ("silence_start: 1.63\nsilence_end: 2.47 | silence_duration: 0.84", 3.89),
    ("", 3.34),
    ("silence_start: 0\nsilence_end: 1.8 | silence_duration: 1.8", 3.34),
    ("silence_start: 1.6\nsilence_end: 1.7 | silence_duration: 0.1", 3.34),
])
def test_notice_cut_uses_complete_pause_without_clipping_announcement(tmp_path, monkeypatch, stderr, expected):
    monkeypatch.setattr(audio, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        audio.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=stderr),
    )
    assert audio.refine_cut_into_pause(tmp_path / "chapter.mp3", 3.34, 5.36) == pytest.approx(expected)


@pytest.mark.parametrize("pauses,expected", [
    ([(9.0, 9.85), (11.25, 12.26), (14.02, 15.08)], (11.25, 12.26)),
    ([(11.1, 11.6)], None),
    ([(11.25, 16.0)], None),
    (None, None),
])
def test_reader_credit_uses_first_complete_substantial_pause(tmp_path, monkeypatch, pauses, expected):
    monkeypatch.setattr(audio, "_detect_silences", lambda *_args: pauses)

    assert audio.reader_credit_pause(tmp_path / "chapter.mp3", 10.44, 16.0) == expected


@pytest.mark.parametrize("start,end", [(-1, 4), (4, 4), (5, 2)])
def test_build_trim_command_rejects_invalid_interval(tmp_path, start, end):
    with pytest.raises(ValueError):
        build_trim_command("ffmpeg", Path("input.mp3"), tmp_path / "out.mp3", start, end)


def test_refine_outro_boundary_moves_to_speech_after_aligned_pause(tmp_path, monkeypatch):
    source = tmp_path / "chapter.mp3"
    source.touch()
    monkeypatch.setattr(audio, "read_track", lambda _path: (200, "Chapter", "Reader"))
    monkeypatch.setattr(audio, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stderr=("silence_start: 1.9\\n" "silence_end: 3.9 | silence_duration: 2.0"),
        ),
    )

    refined = refine_outro_boundary_with_silence(source, 100.0)

    assert refined == pytest.approx(101.9)


def test_refine_outro_boundary_ignores_pause_not_aligned_to_candidate(tmp_path, monkeypatch):
    source = tmp_path / "chapter.mp3"
    source.touch()
    monkeypatch.setattr(audio, "read_track", lambda _path: (200, "Chapter", "Reader"))
    monkeypatch.setattr(audio, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stderr="silence_start: 0.0\\nsilence_end: 1.0 | silence_duration: 1.0",
        ),
    )

    assert refine_outro_boundary_with_silence(source, 100.0) == 100.0

def test_extract_analysis_slice_creates_mono_16khz_wav(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"wav data")

    monkeypatch.setattr("librivox_trimmer.audio.resolve_ffmpeg", lambda: "ffmpeg.exe")
    monkeypatch.setattr("librivox_trimmer.audio.subprocess.run", fake_run)
    output_path = tmp_path / "intro.wav"

    result = extract_analysis_slice(
        tmp_path / "chapter.mp3",
        output_path,
        0,
        180,
    )

    command = captured["command"]
    assert result == output_path
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-c:a") + 1] == "pcm_s16le"