import json
from pathlib import Path

import pytest

from librivox_trimmer import project as project_module
from librivox_trimmer.config import projects_dir
from librivox_trimmer.settings import DetectionSettings
from librivox_trimmer.project import (
    TrackReview,
    cleanup_project,
    load_project,
    new_project_path_for_url,
    project_assets_dir,
    save_project,
)


def stub_track_metadata(path: Path) -> tuple[float, str, str]:
    return 120.0, path.stem, "Reader"


def test_project_round_trip_stores_paths_metadata_and_review_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr(project_module, "read_track", stub_track_metadata)
    project_path = tmp_path / "Book.lvproj"
    assets_dir = project_assets_dir(project_path)
    chapter_path = assets_dir / "chapters" / "01 - Chapter One.mp3"
    chapter_path.parent.mkdir(parents=True)
    chapter_path.write_bytes(b"mp3")
    external_path = tmp_path.parent / "local chapter.mp3"
    external_path.write_bytes(b"local mp3")

    saved = save_project(
        project_path,
        [chapter_path, external_path],
        source_url="https://librivox.org/book/",
        managed_audio_dir=assets_dir,
        reviews={chapter_path.resolve(): TrackReview(2.5, 118.0)},
    )
    reopened = load_project(project_path)

    assert saved.created_at == reopened.created_at
    assert reopened.source_url == "https://librivox.org/book/"
    assert reopened.tracks[0].path == chapter_path.resolve()
    assert reopened.tracks[0].title == "01 - Chapter One"
    assert reopened.tracks[0].review.keep_range == (2.5, 118.0)
    assert reopened.tracks[1].path == external_path.resolve()
    assert reopened.managed_audio_dir == assets_dir.resolve()
    assert reopened.tracks[0].review.intro_review_reason is None


def test_intro_review_reasons_round_trip_with_or_without_cut_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr(project_module, "read_track", stub_track_metadata)
    project_path = tmp_path / "Book.lvproj"
    first, second = tmp_path / "01.mp3", tmp_path / "02.mp3"
    for path in (first, second):
        path.write_bytes(b"mp3")

    save_project(
        project_path, [first, second],
        reviews={
            first.resolve(): TrackReview(9.5, 118.0, intro_review_reason="No clear pause; preview this cut."),
            second.resolve(): TrackReview(
                intro_review_reason="Speech at 5.0-15.0s could not be transcribed.",
                outro_review_reason="Speech at 110.0-115.0s could not be transcribed.",
            ),
        },
    )
    reopened = load_project(project_path)
    first_review, second_review = (track.review for track in reopened.tracks)

    assert first_review.keep_start_seconds == 9.5
    assert first_review.intro_review_reason == "No clear pause; preview this cut."
    assert first_review.outro_review_reason is None
    assert second_review.keep_range is None
    assert second_review.intro_review_reason == "Speech at 5.0-15.0s could not be transcribed."
    assert second_review.outro_review_reason == "Speech at 110.0-115.0s could not be transcribed."


def test_cleanup_removes_managed_downloads_but_not_external_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(project_module, "read_track", stub_track_metadata)
    project_path = tmp_path / "Book.lvproj"
    assets_dir = project_assets_dir(project_path)
    managed_track = assets_dir / "chapters" / "01.mp3"
    managed_track.parent.mkdir(parents=True)
    managed_track.write_bytes(b"downloaded")
    external_track = tmp_path / "user audio.mp3"
    external_track.write_bytes(b"user owned")
    save_project(project_path, [managed_track, external_track], managed_audio_dir=assets_dir)

    cleanup_project(project_path)

    assert not project_path.exists()
    assert not assets_dir.exists()
    assert external_track.exists()


def test_load_project_rejects_relative_path_traversal(tmp_path):
    project_path = tmp_path / "Unsafe.lvproj"
    project_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "tracks": [
                    {
                        "file": {"type": "relative", "path": "../outside.mp3"},
                        "review": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsafe relative"):
        load_project(project_path)


def test_new_project_path_uses_url_slug_and_avoids_existing_projects(tmp_path):
    projects_directory = tmp_path / "Documents" / "LibriVox Projects"
    existing_project = projects_directory / "the-book.lvproj"
    existing_project.parent.mkdir(parents=True)
    existing_project.touch()

    new_path = new_project_path_for_url(
        "https://librivox.org/the-book/",
        projects_directory,
    )

    assert new_path.name == "the-book (2).lvproj"


def test_new_project_path_sanitizes_windows_filename_characters(tmp_path):
    project_path = new_project_path_for_url(
        "https://librivox.org/a%3Fb/",
        tmp_path / "projects",
    )

    assert project_path.name == "a-b.lvproj"


def test_projects_dir_uses_per_user_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local App Data"))

    assert projects_dir() == tmp_path / "Local App Data" / "LibriVoxTrimmer" / "Projects"


def test_project_preserves_detection_preferences_on_resave(tmp_path, monkeypatch):
    monkeypatch.setattr(project_module, "read_track", stub_track_metadata)
    chapter = tmp_path / "chapter.mp3"
    chapter.write_bytes(b"audio")
    project_path = tmp_path / "Book.lvproj"
    preferences = DetectionSettings(model_size="tiny.en", intro_window_seconds=90)
    save_project(
        project_path, [chapter], detection_settings=preferences,
        reviews={chapter.resolve(): TrackReview(3, 110)},
    )
    reopened = load_project(project_path)
    assert reopened.detection_settings == preferences
    assert reopened.tracks[0].review.keep_start_seconds == 3
    save_project(project_path, [chapter])
    assert load_project(project_path).detection_settings == preferences


def test_project_round_trips_interior_notice_start(tmp_path, monkeypatch):
    monkeypatch.setattr(project_module, "read_track", stub_track_metadata)
    chapter = tmp_path / "chapter.mp3"
    chapter.write_bytes(b"audio")
    path = tmp_path / "Book.lvproj"
    save_project(
        path, [chapter], reviews={chapter.resolve(): TrackReview(15, 110, intro_removal_start_seconds=4.5)},
    )
    track = load_project(path).tracks[0]
    assert track.review.intro_removal_start_seconds == 4.5
    assert track.review.keep_start_seconds == 15


def test_project_round_trips_retained_book_ending(tmp_path, monkeypatch):
    monkeypatch.setattr(project_module, "read_track", stub_track_metadata)
    chapter = tmp_path / "chapter.mp3"
    chapter.write_bytes(b"audio")
    path = tmp_path / "Book.lvproj"
    save_project(
        path, [chapter], reviews={chapter.resolve(): TrackReview(9, 100, outro_resume_start_seconds=110)},
    )
    track = load_project(path).tracks[0]
    assert track.review.keep_end_seconds == 100
    assert track.review.outro_resume_start_seconds == 110