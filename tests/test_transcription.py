from types import SimpleNamespace

from librivox_trimmer.transcription import TimedWord, transcribe_words, uncovered_speech


class FakeModel:
    def transcribe(self, _audio_path, **options):
        assert options["word_timestamps"] is True
        assert options["language"] == "en"
        segment = SimpleNamespace(
            start=0.2,
            end=1.0,
            words=[
                SimpleNamespace(word=" Hello", start=0.2, end=0.55, probability=0.93),
                SimpleNamespace(word=" world.", start=0.56, end=1.0, probability=0.91),
            ],
        )
        return iter([segment]), SimpleNamespace(language="en")


def test_transcribe_words_applies_absolute_window_offset(tmp_path):
    result = transcribe_words(tmp_path / "slice.wav", model=FakeModel(), offset_seconds=420)

    assert result[0].text == " Hello"
    assert result[0].start == 420.2
    assert result[1].end == 421.0
    assert result[0].probability == 0.93


def test_uncovered_speech_reports_speech_without_words():
    words = [TimedWord("Chapter", 0.5, 1.0), TimedWord("one.", 1.0, 1.4)]
    assert uncovered_speech(words, [(0.4, 1.5), (5.0, 9.0)]) == [(5.0, 9.0)]


def test_uncovered_speech_ignores_short_gaps_and_complete_coverage():
    words = [TimedWord(str(index), 5 + index * 0.5, 5.4 + index * 0.5) for index in range(8)]
    assert uncovered_speech(words, [(5.0, 8.9)]) == []


def test_stretched_word_cannot_hide_omitted_speech():
    words = [TimedWord("Chapter", 4.0, 12.0)]
    gaps = uncovered_speech(words, [(5.0, 12.0)])
    assert len(gaps) == 1
    assert gaps[0][0] == 5.0 and 10.2 < gaps[0][1] < 10.4