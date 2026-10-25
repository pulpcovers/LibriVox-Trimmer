from pathlib import Path
from tempfile import TemporaryDirectory
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace

from librivox_trimmer.audio import (
    extract_analysis_slice,
    pause_after_heading,
    refine_cut_into_pause,
    refine_intro_resume_start,
    refine_outro_boundary_with_silence,
)
from librivox_trimmer.boundary_detection import (
    BoundaryDetection,
    detect_boundaries,
    detect_outro_boundary,
    detect_final_chapter_outro,
    detect_intro_boundary,
)
from mutagen.mp3 import MP3
from librivox_trimmer.metadata import read_track
from librivox_trimmer.model_manager import load_whisper_model
from librivox_trimmer.transcription import detect_speech_regions, transcribe_words, uncovered_speech
from librivox_trimmer.settings import DetectionSettings


# Notices sit near the start; untranscribed speech later in the window does not affect the intro cut.
COVERAGE_SEARCH_SECONDS = 40.0


@dataclass(frozen=True)
class TrackBoundaryResult:
    path: Path
    detection: BoundaryDetection | None = None
    error: str | None = None


def _relevant_gaps(words, regions, safety_gap_seconds):
    notice = detect_intro_boundary(words, safety_gap_seconds=safety_gap_seconds)
    limit = notice.notice_end_seconds + 6 if notice is not None else COVERAGE_SEARCH_SECONDS
    return [gap for gap in uncovered_speech(words, regions) if gap[0] < limit]


def _total(gaps):
    return sum(end - start for start, end in gaps)


def _merge_gaps(gaps, *, join_seconds=2.0, max_seconds=15.0):
    merged = []
    for start, end in gaps:
        if merged and start - merged[-1][1] <= join_seconds and end - merged[-1][0] <= max_seconds:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _recover_uncovered_speech(
    track_path, temporary_path, words, window, *, model, find_gaps, relevant_gaps, name,
):
    window_start, window_end = window
    attempted = set()
    for attempt in range(2):
        gaps = relevant_gaps(words)
        # Long merged crops can fail again, so the second pass retries gaps individually.
        groups = _merge_gaps(gaps) if attempt == 0 else gaps
        for index, (start, end) in enumerate(groups):
            crop_start = max(window_start, start - 0.5)
            crop_end = min(window_end, end + 0.5)
            if crop_end - crop_start < 3:
                crop_end = min(window_end, crop_start + 3)
                crop_start = max(window_start, crop_end - 3)
            key = (round(crop_start, 1), round(crop_end, 1))
            if key in attempted or crop_end <= crop_start:
                continue
            attempted.add(key)
            audio = extract_analysis_slice(
                track_path, temporary_path / f"{name}_{attempt}_{index}.wav", crop_start, crop_end,
            )
            inside = lambda word: start - 0.3 <= (word.start + word.end) / 2 <= end + 0.3
            recovered = [
                word for word in transcribe_words(audio, model=model, offset_seconds=crop_start)
                if inside(word)
            ]
            if not recovered:
                continue
            # Replace rather than append: misplaced words in this range would otherwise duplicate.
            candidate = sorted(
                [word for word in words if not inside(word)] + recovered, key=lambda word: word.start,
            )
            if _total(find_gaps(candidate)) < _total(find_gaps(words)):
                words = candidate
    return words, relevant_gaps(words)


def _with_review_reason(detection, section, reason):
    field = f"{section}_review_reason"
    combined = " ".join(filter(None, (getattr(detection, field), reason)))
    return replace(detection, **{field: combined})


def _transcribe_intro(
    track_path, temporary_path, intro_end, *, model, safety_gap, is_first_chapter, report, title,
):
    """Transcribe the opening, retrying until the notice is found and complete; return (words, untranscribed gaps)."""
    analyzed_intro_end = min(20.0, intro_end)
    intro_audio = extract_analysis_slice(track_path, temporary_path / "intro.wav", 0, analyzed_intro_end)
    coverage_audio = intro_audio
    intro_words = transcribe_words(intro_audio, model=model)
    original_intro_words = intro_words
    if analyzed_intro_end > 10 and detect_intro_boundary(intro_words, safety_gap_seconds=safety_gap) is None:
        report(f"Checking first 10 seconds: {title}...")
        retry_audio = extract_analysis_slice(track_path, temporary_path / "intro_retry_10.wav", 0, 10.0)
        intro_words = transcribe_words(retry_audio, model=model) + [
            word for word in original_intro_words if word.start >= 10.0
        ]
    if intro_end > 10 and detect_intro_boundary(intro_words, safety_gap_seconds=safety_gap) is None:
        focus_start = 5.0
        focus_end = min(18.0, intro_end)
        report(f"Checking opening notice separately: {title}...")
        focused_audio = extract_analysis_slice(
            track_path, temporary_path / "intro_notice.wav", focus_start, focus_end,
        )
        focused_words = transcribe_words(focused_audio, model=model, offset_seconds=focus_start)
        notice = detect_intro_boundary(focused_words, safety_gap_seconds=safety_gap)
        if notice is not None:
            intro_words = (
                [word for word in intro_words if word.end <= focus_start]
                + [word for word in focused_words if word.start < notice.keep_time_seconds]
                + [word for word in original_intro_words if word.start >= notice.keep_time_seconds]
            )
    for extended_end in sorted({min(30.0, intro_end), intro_end}):
        if extended_end <= analyzed_intro_end:
            continue
        notice = detect_intro_boundary(intro_words, safety_gap_seconds=safety_gap)
        unfinished_speech = bool(
            intro_words
            and intro_words[-1].end >= analyzed_intro_end - 1
            and not intro_words[-1].text.rstrip().endswith((".", "!", "?"))
        )
        needs_chapter_context = (
            is_first_chapter
            and notice is not None
            and notice.removal_start_seconds > 0
            and not any(
                word.start >= notice.keep_time_seconds
                and word.text.strip(" .,:;!?").casefold() in {"chapter", "section"}
                for word in intro_words
            )
        )
        if (
            notice is not None
            and notice.keep_time_seconds < analyzed_intro_end - 3
            and not unfinished_speech
            and not needs_chapter_context
        ):
            break
        report(f"Checking first {int(extended_end)} seconds: {title}...")
        extended_audio = extract_analysis_slice(
            track_path, temporary_path / f"intro_retry_{int(extended_end)}.wav", 0, extended_end,
        )
        extended_words = transcribe_words(extended_audio, model=model)
        if notice is None:
            intro_words = extended_words
        else:
            merge_end = min(analyzed_intro_end - 1, max((word.end for word in intro_words), default=0))
            intro_words = (
                [word for word in intro_words if word.end <= merge_end]
                + [word for word in extended_words if word.end > merge_end]
            )
        analyzed_intro_end = extended_end
        coverage_audio = extended_audio

    regions = detect_speech_regions(coverage_audio)
    if not _relevant_gaps(intro_words, regions, safety_gap):
        return intro_words, []
    report(f"Re-checking untranscribed speech: {title}...")
    return _recover_uncovered_speech(
        track_path, temporary_path, intro_words, (0.0, analyzed_intro_end),
        model=model, name="coverage",
        find_gaps=lambda words: uncovered_speech(words, regions),
        relevant_gaps=lambda words: _relevant_gaps(words, regions, safety_gap),
    )


def _transcribe_outro(
    track_path, temporary_path, outro_start, duration, *, model, is_last_chapter, book_title, report, title,
):
    """Transcribe the closing, retrying shorter tails until a marker is found.

    Returns (words, last-chapter retry transcripts, untranscribed gaps).
    """
    report(f"Analyzing chapter: {title} (outro)...")
    outro_audio = extract_analysis_slice(track_path, temporary_path / "outro.wav", outro_start, duration)
    outro_words = transcribe_words(outro_audio, model=model, offset_seconds=outro_start)
    retries = []

    def find_marker(words):
        if is_last_chapter:
            return detect_final_chapter_outro(
                words, book_title, other_transcripts=retries, duration_seconds=duration,
            )
        return detect_outro_boundary(words, duration_seconds=duration)

    tail_windows = (10.0, 15.0, 12.0, 20.0, 18.0, 22.0) if is_last_chapter else (10.0, 15.0)
    for tail_seconds in tail_windows:
        if find_marker(outro_words) is not None:
            break
        tail_start = duration - tail_seconds
        if tail_start <= outro_start:
            continue
        report(f"Checking final {int(tail_seconds)} seconds: {title}...")
        tail_audio = extract_analysis_slice(
            track_path, temporary_path / f"outro_tail_{int(tail_seconds)}.wav", tail_start, duration,
        )
        tail_words = transcribe_words(tail_audio, model=model, offset_seconds=tail_start)
        if is_last_chapter:
            retries.append(tail_words)
        else:
            outro_words = [word for word in outro_words if word.end <= tail_start] + tail_words

    outro_regions = detect_speech_regions(outro_audio, offset_seconds=outro_start)

    def find_gaps(words):
        # Speech covered by any final-chapter retry transcript counts as transcribed.
        return uncovered_speech(words + [word for retry in retries for word in retry], outro_regions)

    def relevant_gaps(words):
        marker = find_marker(words)
        # Without a marker, closing credits can push the book ending anywhere in the window.
        limit = marker.keep_time_seconds - 6 if marker is not None else outro_start
        return [gap for gap in find_gaps(words) if gap[1] > limit]

    if not relevant_gaps(outro_words):
        return outro_words, retries, []
    report(f"Re-checking untranscribed speech near the ending: {title}...")
    outro_words, remaining = _recover_uncovered_speech(
        track_path, temporary_path, outro_words, (outro_start, duration),
        model=model, name="outro_coverage", find_gaps=find_gaps, relevant_gaps=relevant_gaps,
    )
    return outro_words, retries, remaining


def _refine_intro(track_path, detection, intro_words, safety_gap, report, title):
    intro = detection.intro
    if intro is None:
        return detection
    notice_end = intro.notice_end_seconds
    if notice_end is None:
        notice_end = intro.keep_time_seconds - safety_gap
    following_words = [word for word in intro_words if word.end > notice_end + 0.05]
    refined_resume = (
        refine_intro_resume_start(track_path, notice_end, following_words[0].end + 0.1)
        if following_words else None
    )
    if refined_resume is not None:
        intro = replace(intro, keep_time_seconds=refined_resume)
    else:
        reason = "No clear pause after the intro notice or reader credit; preview this cut."
        detection = _with_review_reason(detection, "intro", reason)
        report(f"Review intro cut: {title}. {reason}")
    if intro.heading_cut_seconds is not None:
        heading_cut = pause_after_heading(track_path, intro.heading_cut_seconds, intro.removal_start_seconds)
        if heading_cut is not None:
            intro = replace(intro, removal_start_seconds=heading_cut, removal_refinement_limit_seconds=None)
    if intro.removal_start_seconds > 0 and intro.removal_refinement_limit_seconds is not None:
        intro = replace(intro, removal_start_seconds=refine_cut_into_pause(
            track_path, intro.removal_start_seconds, intro.removal_refinement_limit_seconds,
        ))
    return replace(detection, intro=intro)


def _refine_outro(track_path, detection, duration):
    outro = detection.outro
    if outro is None:
        return detection
    resume = outro.resume_after_outro_seconds
    refined_time = refine_outro_boundary_with_silence(track_path, outro.keep_time_seconds)
    if refined_time > outro.keep_time_seconds and (resume is None or refined_time < resume):
        outro = replace(outro, keep_time_seconds=refined_time)
    if resume is not None and outro.retained_ending_match_end_seconds is not None:
        refined_resume = refine_cut_into_pause(track_path, resume, outro.retained_ending_match_end_seconds)
        if outro.keep_time_seconds < refined_resume < duration:
            outro = replace(outro, resume_after_outro_seconds=refined_resume)
    return replace(detection, outro=outro)


def analyze_track_boundaries(
    track_path: Path,
    *,
    model=None,
    progress_callback: Callable[[str], None] | None = None,
    settings: DetectionSettings | None = None,
    is_first_chapter: bool = False,
    is_last_chapter: bool = False,
) -> BoundaryDetection:
    settings = settings or DetectionSettings()
    settings.validate()
    duration, title, _artist = read_track(track_path)
    if duration <= 0:
        raise ValueError("Cannot analyze a track with no duration.")
    book_title = ""
    if is_last_chapter:
        try:
            audio_metadata = MP3(track_path)
            book_title = str(audio_metadata.tags.get("TALB", "")) if audio_metadata.tags else ""
        except (OSError, ValueError):
            pass

    intro_end = min(duration, settings.intro_window_seconds)
    outro_start = max(0.0, duration - settings.outro_window_seconds)
    if settings.detect_intro and settings.detect_outro and intro_end > outro_start:
        # Overlapping windows on a short track would let one passage be both intro and outro.
        split = duration * 0.65
        intro_end = min(intro_end, split)
        outro_start = max(outro_start, split)

    def report(message):
        if progress_callback is not None:
            progress_callback(message)

    model = model or load_whisper_model(settings.model_size)
    report(f"Model ready. Analyzing chapter: {title} ({'intro' if settings.detect_intro else 'outro'})...")
    intro_words, intro_gaps = [], []
    outro_words, outro_retries, outro_gaps = [], [], []
    with TemporaryDirectory(prefix="librivox-analysis-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        if settings.detect_intro:
            intro_words, intro_gaps = _transcribe_intro(
                track_path, temporary_path, intro_end, model=model,
                safety_gap=settings.intro_safety_gap_seconds, is_first_chapter=is_first_chapter,
                report=report, title=title,
            )
        if settings.detect_outro:
            outro_words, outro_retries, outro_gaps = _transcribe_outro(
                track_path, temporary_path, outro_start, duration, model=model,
                is_last_chapter=is_last_chapter, book_title=book_title, report=report, title=title,
            )

    detection = detect_boundaries(
        intro_words,
        outro_words,
        intro_safety_gap_seconds=settings.intro_safety_gap_seconds,
        is_first_chapter=is_first_chapter,
        is_last_chapter=is_last_chapter,
        book_title=book_title,
        outro_transcripts=outro_retries,
        duration_seconds=duration,
    )
    detection = _refine_intro(
        track_path, detection, intro_words, settings.intro_safety_gap_seconds, report, title,
    )
    for section, gaps, missed in (("intro", intro_gaps, "a notice"), ("outro", outro_gaps, "a closing marker")):
        if not gaps:
            continue
        spans = ", ".join(f"{start:.1f}-{end:.1f}s" for start, end in gaps)
        reason = (
            f"Speech at {spans} could not be transcribed, so the {section} cut may be "
            f"incomplete or {missed} may have been missed. Preview this chapter."
        )
        detection = _with_review_reason(detection, section, reason)
        report(f"Review {section}: {title}. {reason}")
    if settings.refine_outro:
        detection = _refine_outro(track_path, detection, duration)
    return detection


def analyze_tracks_boundaries(
    track_paths: list[Path],
    *,
    progress_callback: Callable[[str | TrackBoundaryResult], None] | None = None,
    settings: DetectionSettings | None = None,
) -> list[TrackBoundaryResult]:
    results: list[TrackBoundaryResult] = []
    total = len(track_paths)
    for index, path in enumerate(track_paths, start=1):
        try:
            _duration, title, _artist = read_track(path)
        except Exception:
            title = path.name
        prefix = f"Chapter {index} of {total}: {title}"
        if progress_callback is not None:
            progress_callback(f"{prefix} (checking speech model)...")

        def report_progress(message: str) -> None:
            if progress_callback is not None:
                progress_callback(f"{prefix}: {message}")

        try:
            options = {
                "is_first_chapter": index == 1,
                "is_last_chapter": index == total,
            }
            result = analyze_track_boundaries(
                path, progress_callback=report_progress, settings=settings, **options,
            )
        except Exception as error:
            track_result = TrackBoundaryResult(path=path, error=str(error))
        else:
            track_result = TrackBoundaryResult(path=path, detection=result)
        results.append(track_result)
        if progress_callback is not None:
            progress_callback(track_result)
    return results