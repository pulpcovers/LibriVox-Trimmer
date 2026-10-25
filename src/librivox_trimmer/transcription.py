import re
from dataclasses import dataclass
from pathlib import Path

from librivox_trimmer.model_manager import load_whisper_model


@dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float
    probability: float | None = None


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def detect_speech_regions(
    audio_path: Path, *, offset_seconds: float = 0,
) -> list[tuple[float, float]]:
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    sample_rate = 16000
    audio = decode_audio(str(audio_path), sampling_rate=sample_rate)
    # Tighter than the defaults (2 s silence, 400 ms padding) so sentences form separate regions.
    options = VadOptions(min_silence_duration_ms=300, speech_pad_ms=100)
    return [
        (offset_seconds + chunk["start"] / sample_rate, offset_seconds + chunk["end"] / sample_rate)
        for chunk in get_speech_timestamps(audio, options)
    ]


def uncovered_speech(
    words: list[TimedWord],
    regions: list[tuple[float, float]],
    *,
    minimum_seconds: float = 1.0,
    max_word_seconds: float = 1.5,
    tolerance_seconds: float = 0.2,
) -> list[tuple[float, float]]:
    # Cap word spans because Whisper can stretch a word across silence or omitted speech.
    covered = sorted(
        (max(word.start, word.end - max_word_seconds) - tolerance_seconds, word.end + tolerance_seconds)
        for word in words
    )
    gaps = []
    for region_start, region_end in regions:
        cursor = region_start
        for start, end in covered:
            if end <= cursor or start >= region_end:
                continue
            if start - cursor >= minimum_seconds:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if region_end - cursor >= minimum_seconds:
            gaps.append((cursor, region_end))
    return gaps


def transcribe_words(
    audio_path: Path,
    *,
    model=None,
    offset_seconds: float = 0,
) -> list[TimedWord]:
    model = model or load_whisper_model()
    segments, _info = model.transcribe(
        str(audio_path),
        language="en",
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=False,
    )

    timed_words: list[TimedWord] = []
    for segment in segments:
        segment_words = getattr(segment, "words", None) or []
        if segment_words:
            for word in segment_words:
                start = getattr(word, "start", None)
                end = getattr(word, "end", None)
                if start is None or end is None or end < start:
                    continue
                timed_words.append(
                    TimedWord(
                        text=str(getattr(word, "word", "")),
                        start=float(start) + offset_seconds,
                        end=float(end) + offset_seconds,
                        probability=(
                            float(word.probability)
                            if getattr(word, "probability", None) is not None
                            else None
                        ),
                    )
                )
            continue

        tokens = _word_tokens(str(getattr(segment, "text", "")))
        segment_start = float(segment.start)
        segment_end = float(segment.end)
        if not tokens or segment_end <= segment_start:
            continue
        token_duration = (segment_end - segment_start) / len(tokens)
        for index, token in enumerate(tokens):
            start = segment_start + token_duration * index
            timed_words.append(
                TimedWord(
                    text=token,
                    start=start + offset_seconds,
                    end=start + token_duration + offset_seconds,
                    probability=None,
                )
            )
    return timed_words