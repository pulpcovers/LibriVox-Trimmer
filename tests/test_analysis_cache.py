import json
from types import SimpleNamespace

import pytest

from librivox_trimmer import audio, detection
from librivox_trimmer.analysis_cache import AnalysisCache, active_cache, analysis_session, use_cache
from librivox_trimmer.settings import DetectionSettings
from librivox_trimmer.transcription import TimedWord


WORDS = [TimedWord("This", 0.0, 0.3, 0.9), TimedWord("is", 0.3, 0.5, None)]


def test_cache_round_trips_transcripts_regions_and_silences_through_a_snapshot():
    cache = AnalysisCache(model_size="base.en")
    cache.put_words(0, 20.0, WORDS)
    cache.put_regions(0, 20.0, [(1.0, 2.5)])
    cache.put_silences(10, 20.0, 0.35, [(11.0, 12.0)])

    restored = AnalysisCache.from_snapshot(cache.snapshot())

    assert restored.get_words(0, 20.0) == WORDS
    assert restored.get_regions(0, 20.0) == [(1.0, 2.5)]
    assert restored.get_silences(10, 20.0, 0.35) == [(11.0, 12.0)]
    assert restored.get_words(0, 30.0) is None
    assert restored.get_silences(10, 20.0, 0.5) is None


def test_speech_regions_reuse_a_longer_same_start_measurement_without_losing_edge_speech():
    cache = AnalysisCache()
    cache.put_regions(0, 30, [(1.0, 2.5), (19.0, 22.0), (25.0, 28.0)])

    assert cache.get_regions(0, 20) == [(1.0, 2.5), (19.0, 20)]
    assert cache.get_regions(0, 40) is None
    assert cache.get_regions(1, 20) is None
    cache.put_regions(0, 25, [(3.0, 4.0)])
    assert cache.get_regions(0, 20) == [(3.0, 4.0)]


def test_hinted_transcripts_bypass_and_do_not_replace_baseline_cache(tmp_path, monkeypatch):
    cache = AnalysisCache()
    cache.put_words(8, 16, WORDS)
    snapshot = cache.snapshot()
    calls = []
    hinted = [TimedWord("Nigel", 9.0, 9.4, 0.95)]
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)

    def transcribe(_path, **options):
        calls.append(options["hotwords"])
        return hinted

    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    audio = detection._AnalysisAudio(tmp_path / "chapter.mp3", tmp_path, object())

    with use_cache(cache):
        assert audio.words("reader_credit", 8, 16) == WORDS
        assert audio.words("reader_credit", 8, 16, hotwords="Nigel Fisher") == hinted
        assert audio.words("reader_credit", 8, 16, hotwords="Other Reader") == hinted
        assert audio.words("reader_credit", 8, 16) == WORDS

    assert calls == ["Nigel Fisher", "Other Reader"]
    assert cache.snapshot() == snapshot


def test_disk_cache_keeps_audio_measurements_but_not_transcripts(tmp_path):
    track = tmp_path / "chapter.mp3"
    track.write_bytes(b"a" * 5000)
    cache = AnalysisCache.for_track(track, "base.en")
    cache.put_words(0, 20.0, WORDS)
    cache.put_regions(0, 20.0, [(1.0, 2.5)])
    cache.put_silences(0, 20.0, 0.35, [(3.0, 4.0)])
    cache.save()

    assert "transcripts" not in json.loads(cache._path.read_text(encoding="utf-8"))
    restored = AnalysisCache.for_track(track, "base.en")
    assert restored.get_words(0, 20.0) is None
    assert restored.get_regions(0, 20.0) == [(1.0, 2.5)]
    assert restored.get_silences(0, 20.0, 0.35) == [(3.0, 4.0)]
    assert AnalysisCache.for_track(tmp_path / "chapter.mp3", "small.en").get_words(0, 20.0) is None
    track.write_bytes(b"b" * 5000)
    assert AnalysisCache.for_track(track, "base.en").get_words(0, 20.0) is None
    assert AnalysisCache.for_track(tmp_path / "missing.mp3", "base.en") is None


def test_a_projects_cache_lives_in_its_files_folder_and_is_removed_with_it(tmp_path, monkeypatch):
    from librivox_trimmer.project import cleanup_project, project_assets_dir, save_project
    from librivox_trimmer import project as project_module

    project_path = tmp_path / "Book.lvproj"
    assets = project_assets_dir(project_path)
    track = assets / "chapters" / "01.mp3"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"c" * 5000)
    # An older project folder has no marker file; the cache must not make it look foreign.
    monkeypatch.setattr(project_module, "read_track", lambda _path: (120.0, "One", "Reader"))
    save_project(project_path, [track], managed_audio_dir=assets)

    cache = AnalysisCache.for_track(track, "base.en")
    cache.put_words(0, 20.0, WORDS)
    cache.save()

    assert [path.parent for path in (assets / "analysis-cache").glob("*.json")] == [assets / "analysis-cache"]
    assert AnalysisCache.for_track(track, "base.en").get_words(0, 20.0) is None

    cleanup_project(project_path)

    assert not assets.exists()


def test_silences_come_from_the_cache_without_running_ffmpeg(tmp_path, monkeypatch):
    def unavailable(*_args, **_kwargs):
        pytest.fail("FFmpeg should not run for a cached window")

    monkeypatch.setattr(audio, "_run_ffmpeg", unavailable)
    cache = AnalysisCache()
    cache.put_silences(5.0, 15.0, 0.35, [(6.0, 7.0)])

    with use_cache(cache):
        assert audio._detect_silences(tmp_path / "chapter.mp3", 5.0, 15.0, 0.35) == [(6.0, 7.0)]


def test_an_already_active_cache_is_kept_by_analysis_sessions(tmp_path):
    cache = AnalysisCache()

    with use_cache(cache), analysis_session(tmp_path / "chapter.mp3", "base.en") as session:
        assert session is cache
        assert active_cache() is cache
    assert active_cache() is None


@pytest.mark.parametrize("bulk", [False, True])
def test_every_analysis_reruns_transcription(tmp_path, monkeypatch, bulk):
    track = tmp_path / "chapter.mp3"
    track.write_bytes(b"x" * 5000)
    transcriptions = []
    notice = [
        TimedWord(word, 0.3 * index, 0.3 * index + 0.25, 0.95)
        for index, word in enumerate("This is a LibriVox recording.".split())
    ]

    def transcribe(path, *, model, offset_seconds=0):
        transcriptions.append(path.name)
        return notice

    def offline(*_args, **_kwargs):
        raise OSError("no ffmpeg in this test")

    monkeypatch.setattr(detection, "read_track", lambda _path: (600.0, "Chapter", "Reader"))
    monkeypatch.setattr(detection, "extract_analysis_slice", lambda _source, output, *_times: output)
    monkeypatch.setattr(detection, "transcribe_words", transcribe)
    monkeypatch.setattr(detection, "detect_speech_regions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(audio, "_run_ffmpeg", offline)
    monkeypatch.setattr(detection, "load_whisper_model", lambda _size: object())
    monkeypatch.setattr(detection, "MP3", lambda _path: SimpleNamespace(tags={}))
    settings = DetectionSettings(detect_outro=False)

    def analyze():
        if bulk:
            return detection.analyze_tracks_boundaries([track], settings=settings)
        return detection.analyze_track_boundaries(track, model=object(), settings=settings)

    first = analyze()
    transcribed = len(transcriptions)
    last_word = notice[-1]
    notice[-1] = TimedWord("recording!", last_word.start, last_word.end, last_word.probability)
    second = analyze()

    assert transcribed > 0
    assert len(transcriptions) == transcribed * 2
    assert second == first
    assert AnalysisCache.for_track(track, settings.model_size).get_words(0, 20) is None
    assert analyze() == second
    assert len(transcriptions) == transcribed * 3


def test_legacy_disk_transcripts_are_ignored_without_losing_audio_measurements(tmp_path):
    track = tmp_path / "chapter.mp3"
    track.write_bytes(b"legacy" * 1000)
    cache = AnalysisCache.for_track(track, "base.en")
    cache.put_words(0, 20, WORDS)
    cache.put_words(100, 120, WORDS)
    cache.put_regions(0, 20, [(1.0, 2.5)])
    cache.put_silences(0, 20, 0.35, [(3.0, 4.0)])

    cache._path.write_text(json.dumps(cache.snapshot()), encoding="utf-8")
    cache = AnalysisCache.for_track(track, "base.en")

    assert cache.get_words(0, 20) is None
    assert cache.get_words(100, 120) is None
    assert cache.get_regions(0, 20) == [(1.0, 2.5)]
    assert cache.get_silences(0, 20, 0.35) == [(3.0, 4.0)]
