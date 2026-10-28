from pathlib import Path

import pytest

from librivox_trimmer import detection
from librivox_trimmer.transcription import TimedWord
from librivox_trimmer.settings import DetectionSettings
from librivox_trimmer.boundary_detection import BoundaryDetection, BoundarySuggestion


@pytest.fixture(autouse=True)
def mock_intro_resume_audio(monkeypatch):
    monkeypatch.setattr(detection, "refine_intro_resume_start", lambda *_args: None)
    monkeypatch.setattr(detection, "pause_after_heading", lambda *_args: None)
    monkeypatch.setattr(detection, "detect_speech_regions", lambda *_args, **_kwargs: [])


def _timed(text, start, step=0.75):
    return [TimedWord(word, start + index * step, start + index * step + 0.5, 0.95)
            for index, word in enumerate(text.split())]


@pytest.mark.parametrize("confirmed_pause", [11.75, None])
@pytest.mark.parametrize("cue_start", [8.4, 9.0])
def test_credit_retry_protects_narration_without_chapter_metadata(tmp_path, monkeypatch, confirmed_pause, cue_start):
    notice = _timed("This LibriVox recording is in the public domain", 5, 0.4)
    initial = notice + [
        TimedWord("Recording", 8.72, 9.9), TimedWord("by", 9.9, 10.44),
        TimedWord("Nigel", 10.44, 10.68), TimedWord("Fisher", 10.68, 11.06),
    ] + _timed("Our reception in Kalnagar The Royal City never contained such splendor", 11.06, 0.4)
    focused = [
        TimedWord("Recording", cue_start, 9.9), TimedWord("by", 9.9, 10.44),
        TimedWord("Nigel", 10.44, 10.68), TimedWord("Fisher.", 10.68, 11.06),
    ] + _timed("Our reception in Kalnagar", 12.3, 0.4)
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: focused)
    monkeypatch.setattr(detection, "refine_intro_resume_start", lambda *_args: confirmed_pause)
    monkeypatch.setattr(detection, "reader_credit_pause", lambda *_args: (11.25, 12.26))
    original = detection.detect_boundaries(initial, [])

    result = detection._confirm_reader_credit(
        tmp_path / "chapter.mp3", original, initial, 100, object(), 0.6, lambda _message: None,
    )

    if confirmed_pause is not None:
        assert result.intro.keep_time_seconds == confirmed_pause
        assert result.intro.notice_end_seconds == 11.06
        assert result.intro_review_reason is None
    else:
        assert result.intro.keep_time_seconds <= 8.72
        assert "protect narration" in result.intro_review_reason


def test_credit_retry_without_supported_ending_keeps_credit_and_flags_review(tmp_path, monkeypatch):
    words = _timed("This is a LibriVox recording.", 0, 0.3)
    words += _timed("Recording by A Reader the story begins without a pause", 2.7, 0.3)
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: words[6:])
    result = detection._confirm_reader_credit(
        tmp_path / "chapter.mp3", detection.detect_boundaries(words, []), words,
        100, object(), 0.6, lambda _message: None,
    )

    assert result.intro.reader_credit_end_seconds is None
    assert result.intro.keep_time_seconds < 2.7
    assert "protect narration" in result.intro_review_reason


@pytest.mark.parametrize("first_pause", [(4.8, 5.8), None])
def test_credit_retry_cannot_skip_a_pause_to_remove_following_narration(tmp_path, monkeypatch, first_pause):
    notice = _timed("This is a LibriVox recording.", 0, 0.3)
    credit = _timed("Recording by A Reader", 2.7, 0.3)
    story = _timed("The story begins.", 6, 0.3)
    words = notice + credit + story
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: credit + story)
    monkeypatch.setattr(detection, "reader_credit_pause", lambda *_args: first_pause)
    monkeypatch.setattr(detection, "refine_intro_resume_start", lambda *_args: 7.5)

    result = detection._confirm_reader_credit(
        tmp_path / "chapter.mp3", detection.detect_boundaries(words, []), words,
        100, object(), 0.6, lambda _message: None,
    )

    assert result.intro.keep_time_seconds < credit[0].start
    assert result.intro.reader_credit_end_seconds is None
    assert "protect narration" in result.intro_review_reason


def test_failed_credit_retry_keeps_uncertain_credit_for_review(tmp_path, monkeypatch):
    words = _timed("This is a LibriVox recording.", 0, 0.3)
    words += _timed("Recording by A Reader.", 2.7, 0.3)

    def fail(*_args, **_kwargs):
        raise RuntimeError("Retry failed")

    monkeypatch.setattr(detection, "extract_analysis_slice", fail)
    result = detection._confirm_reader_credit(
        tmp_path / "chapter.mp3", detection.detect_boundaries(words, []), words,
        100, object(), 0.6, lambda _message: None,
    )

    assert result.intro.keep_time_seconds < 2.7
    assert "protect narration" in result.intro_review_reason


def test_first_track_credit_is_not_retried_or_removed(tmp_path, monkeypatch):
    words = _timed("This is a LibriVox recording.", 0, 0.3)
    words += _timed("Recording by A Reader.", 2.7, 0.3)
    original = detection.detect_boundaries(words, [], is_first_chapter=True)
    monkeypatch.setattr(
        detection, "extract_analysis_slice",
        lambda *_args: pytest.fail("First-track credit must be retained without a retry"),
    )

    result = detection._confirm_reader_credit(
        tmp_path / "chapter.mp3", original, words,
        100, object(), 0.6, lambda _message: None,
    )

    assert result is original


def _coverage_case(tmp_path, monkeypatch, recovered_words, initial_words):
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Author"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "detect_speech_regions", lambda *_args, **_kwargs: [(0.0, 1.6), (5.0, 15.0)])
    crops = []

    def transcribe(path, *, model, offset_seconds=0):
        if path.name.startswith("coverage"):
            crops.append((path.name, offset_seconds))
            return recovered_words
        return initial_words

    analyzed = []
    original = detection.detect_boundaries

    def capture(words, *args, **kwargs):
        analyzed.extend(words)
        return original(words, *args, **kwargs)

    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    monkeypatch.setattr(detection, "detect_boundaries", capture)
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(), settings=DetectionSettings(detect_outro=False),
    )
    return result, crops, analyzed


def test_untranscribed_speech_is_retranscribed_and_replaces_misplaced_words(tmp_path, monkeypatch):
    header = _timed("Chapter one of The Book.", 0.0, 0.3)
    misplaced = [TimedWord("Chapter", 9.8, 10.2, 0.5)]
    notice = _timed("This is a LibriVox recording. All LibriVox recordings are in the public domain.", 5.0)
    result, crops, analyzed = _coverage_case(tmp_path, monkeypatch, notice, header + misplaced)
    assert crops == [("coverage_0_0.wav", 4.5)]
    assert result.intro is not None
    assert result.intro.keep_time_seconds > notice[-1].end
    assert "could not be transcribed" not in (result.intro_review_reason or "")
    assert misplaced[0] not in analyzed


def test_unrecoverable_speech_flags_review_even_without_a_notice(tmp_path, monkeypatch):
    header = _timed("Chapter one of The Book.", 0.0, 0.3)
    result, crops, _analyzed = _coverage_case(tmp_path, monkeypatch, [], header)
    assert crops == [("coverage_0_0.wav", 4.5)]
    assert result.intro is None
    assert "5.0-15.0s could not be transcribed" in result.intro_review_reason


def _outro_coverage_case(tmp_path, monkeypatch, recovered_words, initial_words):
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Author"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "refine_outro_boundary_with_silence", lambda _path, candidate: candidate)
    monkeypatch.setattr(
        detection, "detect_speech_regions",
        lambda _path, offset_seconds=0: [(572.0, 588.0), (591.0, 594.0)] if offset_seconds else [],
    )
    crops = []

    def transcribe(path, *, model, offset_seconds=0):
        if path.name.startswith("outro_coverage"):
            crops.append((path.name, offset_seconds))
            return recovered_words
        return initial_words if path.name.startswith("outro") else []

    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(), settings=DetectionSettings(detect_intro=False),
    )
    return result, crops


def test_untranscribed_closing_speech_is_recovered(tmp_path, monkeypatch):
    narration = _timed("and so the long night ended at last for everyone in the quiet house that winter", 572.0, 1.0)
    closing = _timed("End of chapter one.", 591.0)
    result, crops = _outro_coverage_case(tmp_path, monkeypatch, closing, narration)
    assert crops == [("outro_coverage_0_0.wav", 590.5)]
    assert result.outro is not None
    assert result.outro.keep_time_seconds == closing[0].start
    assert result.outro_review_reason is None


def test_unrecoverable_closing_speech_flags_outro_review(tmp_path, monkeypatch):
    narration = _timed("and so the long night ended at last for everyone in the quiet house that winter", 572.0, 1.0)
    result, _crops = _outro_coverage_case(tmp_path, monkeypatch, [], narration)
    assert result.outro is None
    assert "591.0-594.0s could not be transcribed" in result.outro_review_reason


def test_numbered_volume_marker_is_detected_near_track_end(tmp_path, monkeypatch):
    monkeypatch.setattr(detection, "read_track", lambda _path: (100, "Chapter", "Reader"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: [
        TimedWord(word, 95 + index * 0.5, 95.3 + index * 0.5, 0.95)
        for index, word in enumerate("End of Volume XX".split())
    ])
    monkeypatch.setattr(detection, "refine_outro_boundary_with_silence", lambda _path, candidate: candidate)

    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(),
        settings=DetectionSettings(detect_intro=False),
    )

    assert result.outro is not None
    assert result.outro.keep_time_seconds == 95
    assert result.outro.matched_text == "end of volume xx"


def test_analysis_windows_are_transcribed_and_outro_times_are_offset(tmp_path, monkeypatch):
    track_path = tmp_path / "chapter.mp3"
    track_path.touch()
    calls: list[tuple[Path, float, float]] = []

    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Reader"))

    def extract(_source, output, start, end):
        output.touch()
        calls.append((output, start, end))
        return output

    def transcribe(audio_path, *, model, offset_seconds=0):
        if audio_path.name == "intro.wav":
            return [
                TimedWord(word, index * 0.4, index * 0.4 + 0.25, 0.95)
                for index, word in enumerate("This is a LibriVox recording".split())
            ]
        assert audio_path.name == "outro.wav"
        assert offset_seconds == 570
        return [
            TimedWord(word, offset_seconds + index * 0.4, offset_seconds + index * 0.4 + 0.25, 0.95)
            for index, word in enumerate("End of chapter three".split())
        ]

    monkeypatch.setattr(detection, "extract_analysis_slice", extract)
    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    monkeypatch.setattr(
        detection,
        "refine_outro_boundary_with_silence",
        lambda _path, candidate: candidate,
    )

    result = detection.analyze_track_boundaries(track_path, model=object())

    assert [(path.name, start, end) for path, start, end in calls] == [
        ("intro.wav", 0, 20),
        ("outro.wav", 570, 600),
    ]
    assert result.intro is not None
    assert result.outro is not None
    assert 569 < result.outro.keep_time_seconds < 571


@pytest.mark.parametrize("refined,expected,needs_review", [(7.1, 7.1, False), (None, 6.45, True)])
def test_intro_resume_prefers_measured_pause_and_flags_missing_pause(tmp_path, monkeypatch, refined, expected, needs_review):
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Reader"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    words = [TimedWord(word, 4 + index * 0.4, 4.25 + index * 0.4, 0.95)
             for index, word in enumerate("This is a LibriVox recording.".split())]
    words += [TimedWord("Chapter", 7.5, 8), TimedWord("one.", 8, 8.5)]
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: words)
    calls = []

    def refine(path, notice_end, following_end):
        calls.append((notice_end, following_end))
        return refined

    monkeypatch.setattr(detection, "refine_intro_resume_start", refine)
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(), settings=DetectionSettings(detect_outro=False),
    )
    assert calls[0] == pytest.approx((5.85, 8.1))
    assert result.intro.keep_time_seconds == pytest.approx(expected)
    assert bool(result.intro_review_reason) == needs_review


def test_rechecks_final_fifteen_seconds_when_long_outro_window_misses_marker(tmp_path, monkeypatch):
    track_path = tmp_path / "chapter.mp3"
    track_path.touch()
    calls: list[tuple[str, float, float]] = []

    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Reader"))

    def extract(_source, output, start, end):
        calls.append((output.name, start, end))
        return output

    def transcribe(audio_path, *, model, offset_seconds=0):
        if audio_path.name == "outro.wav":
            return [
                TimedWord(word, offset_seconds + index, offset_seconds + index + 0.5, 0.95)
                for index, word in enumerate("the final narrated sentence ends".split())
            ]
        if audio_path.name.startswith("intro"):
            return []
        if audio_path.name == "outro_tail_10.wav":
            assert offset_seconds == 590
            return [
                TimedWord("You", offset_seconds, offset_seconds + 0.5, 0.95)
            ]
        assert audio_path.name == "outro_tail_15.wav"
        assert offset_seconds == 585
        return [
            TimedWord(word, offset_seconds + index * 0.4, offset_seconds + index * 0.4 + 0.25, 0.95)
            for index, word in enumerate("End of chapter one".split())
        ]

    monkeypatch.setattr(detection, "extract_analysis_slice", extract)
    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    monkeypatch.setattr(
        detection,
        "refine_outro_boundary_with_silence",
        lambda _path, candidate: candidate,
    )

    result = detection.analyze_track_boundaries(track_path, model=object())

    assert calls == [
        ("intro.wav", 0, 20),
        ("intro_retry_10.wav", 0, 10),
        ("intro_notice.wav", 5, 18),
        ("intro_retry_30.wav", 0, 30),
        ("intro_retry_60.wav", 0, 60),
        ("outro.wav", 570, 600),
        ("outro_tail_10.wav", 590, 600),
        ("outro_tail_15.wav", 585, 600),
    ]
    assert result.outro is not None
    assert abs(result.outro.keep_time_seconds - 585) < 0.001


def test_progress_reports_chapter_analysis_after_model_load(tmp_path, monkeypatch):
    track_path = tmp_path / "chapter.mp3"
    track_path.touch()
    events: list[str] = []

    monkeypatch.setattr(
        detection,
        "read_track",
        lambda _path: (600, "The Prisoner of Zenda", "Reader"),
    )

    def load_model(_model_size):
        events.append("model-loaded")
        return object()

    monkeypatch.setattr(detection, "load_whisper_model", load_model)
    monkeypatch.setattr(
        detection,
        "extract_analysis_slice",
        lambda _source, output, _start, _end: output,
    )
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: [])

    detection.analyze_track_boundaries(track_path, progress_callback=events.append)

    assert events == [
        "model-loaded",
        "Model ready. Analyzing chapter: The Prisoner of Zenda (intro)...",
        "Checking first 10 seconds: The Prisoner of Zenda...",
        "Checking opening notice separately: The Prisoner of Zenda...",
        "Checking first 30 seconds: The Prisoner of Zenda...",
        "Checking first 60 seconds: The Prisoner of Zenda...",
        "Analyzing chapter: The Prisoner of Zenda (outro)...",
        "Checking final 10 seconds: The Prisoner of Zenda...",
        "Checking final 15 seconds: The Prisoner of Zenda...",
    ]


def test_batch_detection_continues_after_a_chapter_failure(tmp_path, monkeypatch):
    paths = [tmp_path / "chapter01.mp3", tmp_path / "chapter02.mp3", tmp_path / "chapter03.mp3"]
    for path in paths:
        path.touch()
    monkeypatch.setattr(
        detection,
        "read_track",
        lambda path: (600, path.stem, "Reader"),
    )
    calls: list[Path] = []
    progress: list[str | detection.TrackBoundaryResult] = []

    def analyze(path, *, progress_callback, settings, is_first_chapter, is_last_chapter):
        calls.append(path)
        assert is_first_chapter == (path == paths[0])
        assert is_last_chapter == (path == paths[-1])
        progress_callback("Model ready; intro analysis")
        if path == paths[1]:
            raise RuntimeError("transcription failed")
        return detection.BoundaryDetection(intro=None, outro=None)

    monkeypatch.setattr(detection, "analyze_track_boundaries", analyze)

    results = detection.analyze_tracks_boundaries(paths, progress_callback=progress.append)

    assert calls == paths
    assert [result.error for result in results] == [None, "transcription failed", None]
    assert results[0].detection is not None
    assert results[1].detection is None
    assert results[2].detection is not None
    messages = [message for message in progress if isinstance(message, str)]
    streamed_results = [
        result for result in progress if isinstance(result, detection.TrackBoundaryResult)
    ]
    assert any("Chapter 3 of 3: chapter03" in message for message in messages)
    assert streamed_results == results


def test_batch_progress_names_the_chapter_only_once(tmp_path, monkeypatch):
    path = tmp_path / "chapter01.mp3"
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "The Long Title", "Reader"))

    def analyze(path, *, progress_callback, settings, is_first_chapter, is_last_chapter):
        progress_callback("Analyzing chapter: The Long Title (outro)...")
        progress_callback("Checking first 10 seconds: The Long Title...")
        return detection.BoundaryDetection(intro=None, outro=None)

    monkeypatch.setattr(detection, "analyze_track_boundaries", analyze)
    progress: list = []

    detection.analyze_tracks_boundaries([path], progress_callback=progress.append)

    messages = [item for item in progress if isinstance(item, str) and "Analyzing" in item or "Checking first" in str(item)]
    assert messages == [
        "Chapter 1 of 1: The Long Title: Analyzing chapter (outro)...",
        "Chapter 1 of 1: The Long Title: Checking first 10 seconds...",
    ]


def test_cancelling_track_batch_keeps_finished_chapters_only(tmp_path, monkeypatch):
    paths = [tmp_path / f"chapter0{index}.mp3" for index in range(1, 4)]
    monkeypatch.setattr(detection, "read_track", lambda path: (600, path.stem, "Reader"))
    cancel = {"requested": False}
    calls: list[Path] = []

    def analyze(path, *, progress_callback, settings, is_first_chapter, is_last_chapter):
        calls.append(path)
        if path == paths[1]:
            cancel["requested"] = True
        progress_callback("Transcribing")
        return detection.BoundaryDetection(intro=None, outro=None)

    monkeypatch.setattr(detection, "analyze_track_boundaries", analyze)
    progress: list = []

    results = detection.analyze_tracks_boundaries(
        paths, progress_callback=progress.append, should_cancel=lambda: cancel["requested"],
    )

    assert calls == paths[:2]
    assert [result.path for result in results] == paths[:1]
    assert [item for item in progress if isinstance(item, detection.TrackBoundaryResult)] == results


def test_detection_preferences_skip_outro_and_select_model(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Reader"))
    monkeypatch.setattr(detection, "load_whisper_model", lambda model: calls.append(model) or object())

    def extract(_source, output, start, end):
        calls.append((output.name, start, end))
        return output

    monkeypatch.setattr(detection, "extract_analysis_slice", extract)
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: [])
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3",
        settings=DetectionSettings(model_size="tiny.en", detect_outro=False, intro_window_seconds=90),
    )
    assert calls == ["tiny.en", ("intro.wav", 0, 20), ("intro_retry_10.wav", 0, 10), ("intro_notice.wav", 5, 18), ("intro_retry_30.wav", 0, 30), ("intro_retry_90.wav", 0, 90)]
    assert result.outro is None


def test_interior_intro_cut_is_refined_into_pause(tmp_path, monkeypatch):
    source = tmp_path / "chapter.mp3"
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Reader"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: [])
    suggestion = BoundarySuggestion(
        13.84, 95, "This is a LibriVox recording", removal_refinement_limit_seconds=5.36,
        removal_start_seconds=3.34,
    )
    monkeypatch.setattr(detection, "detect_boundaries", lambda *_args, **_kwargs: BoundaryDetection(suggestion, None))
    calls = []

    def refine(path, start, phrase_end):
        calls.append((path, start, phrase_end))
        return 3.89

    monkeypatch.setattr(detection, "refine_cut_into_pause", refine)
    result = detection.analyze_track_boundaries(
        source, model=object(), settings=DetectionSettings(detect_outro=False),
    )
    assert calls == [(source, 3.34, 5.36)]
    assert result.intro.removal_start_seconds == 3.89
    assert result.intro.keep_time_seconds == 13.84
    assert result.intro.confidence == 95


@pytest.mark.parametrize("pause,expected", [(1.9, 1.9), (None, 4.4)])
def test_heading_keeps_only_number_when_a_clear_pause_follows_it(tmp_path, monkeypatch, pause, expected):
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Reader"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(detection, "refine_cut_into_pause", lambda _path, candidate, _end: candidate)
    suggestion = BoundarySuggestion(
        13.84, 95, "This LibriVox recording", removal_refinement_limit_seconds=6.0,
        removal_start_seconds=4.4, heading_cut_seconds=1.5,
    )
    monkeypatch.setattr(detection, "detect_boundaries", lambda *_args, **_kwargs: BoundaryDetection(suggestion, None))
    calls = []
    monkeypatch.setattr(detection, "pause_after_heading", lambda *args: calls.append(args[1:]) or pause)
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(), settings=DetectionSettings(detect_outro=False),
    )
    assert calls == [(1.5, 4.4)]
    assert result.intro.removal_start_seconds == expected


def test_selected_last_chapter_combines_complementary_tail_transcripts(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(detection, "read_track", lambda _path: (200, "Final Chapter", "Author"))
    monkeypatch.setattr(detection, "MP3", lambda _path: SimpleNamespace(tags={"TALB": "The Mad King"}))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "refine_outro_boundary_with_silence", lambda _path, candidate: candidate)
    monkeypatch.setattr(detection, "refine_cut_into_pause", lambda _path, candidate, _end: candidate)
    calls = []

    def transcribe(path, **_kwargs):
        calls.append(path.name)
        if path.name == "outro_tail_10.wav":
            return [TimedWord(word, 195 + index * 0.3, 195.2 + index * 0.3, 0.95)
                    for index, word in enumerate("End of The Mad King by Author".split())]
        if path.name == "outro_tail_12.wav":
            return [TimedWord(word, 190 + index * 0.3, 190.2 + index * 0.3, 0.95)
                    for index, word in enumerate("End of chapter twenty eight".split())]
        return []

    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    result = detection.analyze_track_boundaries(
        tmp_path / "final.mp3", model=object(), is_last_chapter=True,
    )
    assert result.outro.keep_time_seconds == 190
    assert result.outro.resume_after_outro_seconds == 195
    assert "outro_tail_20.wav" not in calls


def test_intro_retry_recovers_notice_and_retains_later_chapter_words(tmp_path, monkeypatch):
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Author"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    calls = []

    def transcribe(path, **_kwargs):
        calls.append(path.name)
        if path.name == "intro_retry_10.wav":
            return [TimedWord(word, index * 0.4, index * 0.4 + 0.25, 0.95)
                    for index, word in enumerate("This is a LibriVox recording".split())]
        return [TimedWord("Chapter", 15, 15.4, 0.95), TimedWord("one.", 15.4, 16, 0.95)]

    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(),
        settings=DetectionSettings(detect_outro=False), is_first_chapter=True,
    )
    assert calls == ["intro.wav", "intro_retry_10.wav"]
    assert result.intro is not None
    assert result.intro.keep_time_seconds < 29


def test_focused_notice_retry_preserves_original_heading_and_source_offsets(tmp_path, monkeypatch):
    source = tmp_path / "chapter.mp3"
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Author"))
    slices = []

    def extract(_source, output, start, end):
        slices.append((output.name, start, end))
        return output

    def words(text, start):
        return [TimedWord(word, start + index * 0.3, start + index * 0.3 + 0.2, 0.95)
                for index, word in enumerate(text.split())]

    def transcribe(path, *, model, offset_seconds=0):
        if path.name == "intro_notice.wav":
            assert offset_seconds == 5
            return words("This LibriVox recording is in the public domain", 5.5)
        if path.name == "intro_retry_10.wav":
            return words("Chapter one The Hour of the Dragon by Robert Howard", 0)
        return words("Chapter one O Sleeper Awake.", 15)

    monkeypatch.setattr(detection, "extract_analysis_slice", extract)
    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    monkeypatch.setattr(detection, "refine_cut_into_pause", lambda _path, candidate, _end: candidate)
    result = detection.analyze_track_boundaries(
        source, model=object(), settings=DetectionSettings(detect_outro=False), is_first_chapter=True,
    )
    assert slices[-1] == ("intro_notice.wav", 5, 18)
    assert result.intro is not None
    assert 4 < result.intro.removal_start_seconds < 5.5
    assert 8 < result.intro.keep_time_seconds < 20


def test_notice_near_short_window_edge_extends_to_include_remaining_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Author"))
    slices = []

    def extract(_source, output, start, end):
        slices.append((output.name, start, end))
        return output

    def transcribe(path, **_kwargs):
        words = [TimedWord(word, 17 + index * 0.4, 17.25 + index * 0.4, 0.95)
                 for index, word in enumerate("This is a LibriVox recording.".split())]
        if path.name != "intro.wav":
            words += [TimedWord(word, 20 + index * 0.4, 20.25 + index * 0.4, 0.95)
                      for index, word in enumerate("All LibriVox recordings are in the public domain.".split())]
        return words

    monkeypatch.setattr(detection, "extract_analysis_slice", extract)
    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(), settings=DetectionSettings(detect_outro=False),
    )
    assert slices == [("intro.wav", 0, 20), ("intro_retry_30.wav", 0, 30)]
    assert result.intro.keep_time_seconds > 23


def test_intro_slices_never_exceed_configured_maximum_or_track_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(detection, "read_track", lambda _path: (12, "Chapter", "Author"))
    slices = []

    def extract(_source, output, start, end):
        slices.append((start, end))
        return output

    monkeypatch.setattr(detection, "extract_analysis_slice", extract)
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: [])
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(),
        settings=DetectionSettings(detect_outro=False, intro_window_seconds=8),
    )
    assert slices == [(0, 8)]
    assert result.intro is None


def test_short_track_splits_overlapping_intro_and_outro_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(detection, "read_track", lambda _path: (40, "Chapter", "Author"))
    monkeypatch.setattr(detection, "refine_outro_boundary_with_silence", lambda _path, candidate: candidate)
    slices = []

    def extract(_source, output, start, end):
        slices.append((output.name, start, end))
        return output

    monkeypatch.setattr(detection, "extract_analysis_slice", extract)
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: [])
    detection.analyze_track_boundaries(tmp_path / "chapter.mp3", model=object())

    intro_end = max(end for name, _start, end in slices if name.startswith("intro"))
    outro_start = min(start for name, start, _end in slices if name.startswith("outro"))
    assert intro_end == pytest.approx(26)
    assert outro_start == pytest.approx(26)


def test_first_chapter_expansion_preserves_short_pass_book_title(tmp_path, monkeypatch):
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Author"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "refine_cut_into_pause", lambda _path, candidate, _end: candidate)
    calls = []

    def words(text, start):
        return [TimedWord(word, start + index * 0.3, start + index * 0.3 + 0.2, 0.95)
                for index, word in enumerate(text.split())]

    def transcribe(path, **_kwargs):
        calls.append(path.name)
        if path.name == "intro.wav":
            return (words("Chapter one of Beyond the Black River", 0)
                    + words("This is a LibriVox recording.", 5)
                    + words("Beyond the Black River.", 14))
        return words("Chapter one.", 23)

    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(),
        settings=DetectionSettings(detect_outro=False), is_first_chapter=True,
    )
    assert calls == ["intro.wav", "intro_retry_30.wav"]
    assert result.intro.removal_start_seconds == 0


def test_long_volunteer_information_sentence_is_included_in_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(detection, "read_track", lambda _path: (600, "Chapter", "Author"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    text = ("This is a LibriVox recording. All LibriVox recordings are in the public domain. "
            "For further information and to find out how you could volunteer please go to LibriVox.org.")
    words = [TimedWord(word, index * 0.4, index * 0.4 + 0.3, 0.95)
             for index, word in enumerate(text.split())]
    monkeypatch.setattr(detection, "transcribe_words", lambda *_args, **_kwargs: words)
    result = detection.analyze_track_boundaries(
        tmp_path / "chapter.mp3", model=object(), settings=DetectionSettings(detect_outro=False),
    )
    assert result.intro.keep_time_seconds > words[-1].end