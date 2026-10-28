import json
from pathlib import Path

import pytest

from librivox_trimmer import audio, detection
from librivox_trimmer.analysis_cache import AnalysisCache, use_cache
from librivox_trimmer.settings import DetectionSettings
from replay_support import summarize


FIXTURES = sorted((Path(__file__).parent / "fixtures" / "analysis").glob("*.json"))


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=lambda path: path.stem)
def test_recorded_analysis_still_produces_the_same_cuts(fixture_path, monkeypatch):
    """Re-run the detection logic on recorded transcripts and silences, with no audio or Whisper."""
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    def unavailable(*_args, **_kwargs):
        pytest.fail(
            "The analysis asked for audio that was not recorded. If the window strategy changed on "
            "purpose, re-record with scripts/record_analysis_fixture.py."
        )

    class RecordedTags(dict):
        pass

    class RecordedMP3:
        def __init__(self, _path):
            self.tags = RecordedTags({"TALB": data["album"]})

    track_info = lambda _path: (data["duration"], data["title"], "Reader")
    for name in ("extract_analysis_slice", "transcribe_words", "detect_speech_regions"):
        monkeypatch.setattr(detection, name, unavailable)
    monkeypatch.setattr(audio, "_run_ffmpeg", unavailable)
    monkeypatch.setattr(detection, "read_track", track_info)
    monkeypatch.setattr(audio, "read_track", track_info)
    monkeypatch.setattr(detection, "MP3", RecordedMP3)

    settings = DetectionSettings()
    with use_cache(AnalysisCache.from_snapshot(data["audio"], model_size=settings.model_size)):
        result = detection.analyze_track_boundaries(
            Path("recorded") / f"{data['source']}.mp3", model=object(), settings=settings,
            is_first_chapter=data["is_first"], is_last_chapter=data["is_last"],
        )

    assert summarize(result) == data["expected"]
