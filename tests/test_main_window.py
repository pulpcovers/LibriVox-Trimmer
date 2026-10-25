import pytest
from PySide6.QtCore import Qt, QUrl, QPoint, QRect
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QFileDialog, QListWidgetItem

from librivox_trimmer import main_window
from librivox_trimmer.boundary_detection import BoundaryDetection, BoundarySuggestion
from librivox_trimmer.detection import TrackBoundaryResult
from librivox_trimmer.main_window import MainWindow
from librivox_trimmer.progress import DownloadProgress
from librivox_trimmer.settings import AppSettings, DetectionSettings


def test_fetch_chapters_starts_without_save_file_dialog(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(main_window, "projects_dir", lambda: tmp_path / "Projects")
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: pytest.fail("Fetch opened a save dialog"),
    )
    window = MainWindow()
    captured: dict[str, object] = {}

    def capture_worker(function, on_success, message, **options):
        captured["function"] = function
        captured["on_success"] = on_success
        captured["message"] = message
        captured["options"] = options

    monkeypatch.setattr(window, "_start_worker", capture_worker)
    window._url_input.setText("https://librivox.org/the-prisoner-of-zenda/")
    window._download_project()

    assert callable(captured["function"])
    assert captured["on_success"] == window._download_finished
    assert "the-prisoner-of-zenda.lvproj" in str(captured["message"])
    assert captured["options"] == {"with_progress": True}
    assert (tmp_path / "Projects" / "the-prisoner-of-zenda.lvproj").parent.exists()
    window.close()
    application.processEvents()


def test_open_project_dialog_starts_in_app_projects_folder(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    project_directory = tmp_path / "local app data" / "Projects"
    monkeypatch.setattr(main_window, "projects_dir", lambda: project_directory)
    captured: dict[str, object] = {}

    def capture_open_dialog(_parent, _title, directory, _filter):
        captured["directory"] = directory
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", capture_open_dialog)
    window = MainWindow()

    window._open_project()

    assert captured["directory"] == str(project_directory)
    window.close()
    application.processEvents()


def test_status_text_is_selectable_for_copying_paths():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    flags = window._status.textInteractionFlags()

    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
    assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard
    window.close()
    application.processEvents()


def test_help_menu_follows_settings_and_opens_workflow_guide(monkeypatch):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    menus = window.menuBar().actions()
    captured = {}
    monkeypatch.setattr(
        main_window.QMessageBox,
        "about",
        lambda _parent, title, text: captured.update(title=title, text=text),
    )

    assert [action.text().replace("&", "") for action in menus] == [
        "Project", "Settings", "Help",
    ]
    menus[-1].menu().actions()[0].trigger()

    assert captured["title"] == "Using LibriVox Trimmer"
    assert "Fetch chapters" in captured["text"]
    assert "Analyse all chapters" in captured["text"]
    assert "Export trimmed chapter" in captured["text"]
    window.close()
    application.processEvents()


def test_download_progress_displays_percentage_and_byte_fallback():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()

    window._on_worker_progress(DownloadProgress(2 * 1024 * 1024, 10 * 1024 * 1024))

    assert window._activity_progress.minimum() == 0
    assert window._activity_progress.maximum() == 100
    assert window._activity_progress.value() == 20
    assert window._status.text() == "Downloading audiobook: 20% (2.0 MB of 10.0 MB)"

    window._on_worker_progress(DownloadProgress(3 * 1024 * 1024, None))

    assert window._activity_progress.minimum() == 0
    assert window._activity_progress.maximum() == 0
    assert window._status.text() == "Downloading audiobook: 3.0 MB received"
    window.close()
    application.processEvents()


def test_startup_cleans_incomplete_download_and_reports_it(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    project_directory = tmp_path / "Projects"
    incomplete_directory = project_directory / "unfinished-book_files"
    incomplete_directory.mkdir(parents=True)
    (incomplete_directory / "partial.zip").write_bytes(b"partial")
    monkeypatch.setattr(main_window, "projects_dir", lambda: project_directory)

    window = MainWindow()

    assert not incomplete_directory.exists()
    assert window._status.text() == "Cleaned up 1 interrupted download(s)"
    window.close()
    application.processEvents()


def test_preview_buttons_toggle_stop_replay_and_switching(monkeypatch, tmp_path):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    source_path = tmp_path / "chapter.mp3"
    source_url = QUrl.fromLocalFile(str(source_path.resolve()))
    window._set_track_controls_enabled(True)
    monkeypatch.setattr(window, "_selected_path", lambda: source_path)

    class LoadedPlayer:
        def __init__(self):
            self.position = None
            self.play_calls = 0
            self.source_url = source_url

        def source(self):
            return self.source_url

        def mediaStatus(self):
            return QMediaPlayer.MediaStatus.LoadedMedia

        def stop(self):
            window._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        def setSource(self, source):
            self.source_url = source

        def setPosition(self, position):
            self.position = position

        def play(self):
            self.play_calls += 1

    player = LoadedPlayer()
    window._player = player
    window._keep_start.setValue(1.0)
    window._keep_end.setValue(4.0)

    intro_button = window._preview_intro_button
    intro_button.click()
    assert intro_button.text() == "Stop intro preview"
    assert player.position == 0
    assert player.play_calls == 1

    intro_button.click()
    assert intro_button.text() == "Preview intro cut"
    assert window._pending_preview is None
    assert not window._playback_timer.isActive()

    intro_button.click()
    assert player.play_calls == 2

    kept_button = window._preview_kept_button
    assert kept_button.text() == "Preview Kept Intro"
    assert window._intro_panel.layout().indexOf(kept_button) == (
        window._intro_panel.layout().indexOf(intro_button) + 1
    )
    kept_button.click()
    assert intro_button.text() == "Preview intro cut"
    assert kept_button.text() == "Stop kept-intro preview"
    assert player.position == 1000
    assert player.play_calls == 3

    kept_button.click()
    assert kept_button.text() == "Preview Kept Intro"
    assert window._pending_preview is None
    assert player.play_calls == 3

    window.close()
    application.processEvents()


@pytest.mark.parametrize("review_reason", [None, "No clear pause; preview this cut."])
def test_bulk_detection_applies_each_result_and_summarizes_failures(tmp_path, monkeypatch, review_reason):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    suggested_path = tmp_path / "chapter01.mp3"
    unmatched_path = tmp_path / "chapter02.mp3"
    failed_path = tmp_path / "chapter03.mp3"
    window._tracks = [suggested_path, unmatched_path, failed_path]
    for path in window._tracks:
        item = QListWidgetItem(path.name)
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        window._track_list.addItem(item)
    monkeypatch.setattr(
        main_window,
        "read_track",
        lambda path: (600.0, path.stem, "Reader"),
    )

    results = [
        TrackBoundaryResult(
            suggested_path,
            BoundaryDetection(
                BoundarySuggestion(8.0, 94.0, "LibriVox recording"),
                BoundarySuggestion(580.0, 93.0, "end of chapter"),
                intro_review_reason=review_reason,
            ),
        ),
        TrackBoundaryResult(unmatched_path, BoundaryDetection(None, None)),
        TrackBoundaryResult(failed_path, error="model error"),
    ]

    window._on_worker_progress(results[0])

    assert window._review(suggested_path).keep_range == (8.0, 580.0)
    assert "Keep 00:08-09:40" in window._track_list.item(0).text()

    window._bulk_detection_finished(results)

    assert window._review(suggested_path).keep_range == (8.0, 580.0)
    assert window._review(unmatched_path).keep_range is None
    assert "suggestions for 1 of 3 chapters" in window._status.text()
    assert "1 had no matching phrases" in window._status.text()
    assert "1 failed" in window._status.text()
    assert "model error" in window._status.text()
    assert "Keep 00:08-09:40" in window._track_list.item(0).text()
    assert "Keep " not in window._track_list.item(1).text()
    assert window._track_list.item(0).toolTip() == ("Intro: " + review_reason if review_reason else "")
    assert ("[Review intro]" in window._track_list.item(0).text()) == bool(review_reason)
    assert ("1 chapter(s) need review" in window._status.text()) == bool(review_reason)
    window.close()
    application.processEvents()


def test_bulk_unmatched_chapter_with_untranscribed_speech_is_marked_for_review(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    path = tmp_path / "chapter01.mp3"
    window._tracks = [path]
    item = QListWidgetItem(path.name)
    item.setData(Qt.ItemDataRole.UserRole, str(path))
    window._track_list.addItem(item)
    monkeypatch.setattr(main_window, "read_track", lambda path: (600.0, path.stem, "Reader"))
    reason = "Speech at 5.0-15.0s could not be transcribed."
    result = TrackBoundaryResult(path, BoundaryDetection(None, None, intro_review_reason=reason))

    window._bulk_detection_finished([result])

    assert window._review(path).keep_range is None
    assert window._track_list.item(0).toolTip() == "Intro: " + reason
    assert "[Review intro]" in window._track_list.item(0).text()
    assert "1 chapter(s) need review" in window._status.text()
    window.close()
    application.processEvents()


def test_review_warnings_are_restored_when_project_is_reopened(tmp_path, monkeypatch):
    from librivox_trimmer import project as project_module
    from librivox_trimmer.project import TrackReview, save_project

    application = QApplication.instance() or QApplication([])
    stub = lambda path: (600.0, path.stem, "Reader")
    monkeypatch.setattr(project_module, "read_track", stub)
    monkeypatch.setattr(main_window, "read_track", stub)
    chapters = [tmp_path / "chapter01.mp3", tmp_path / "chapter02.mp3"]
    for path in chapters:
        path.write_bytes(b"mp3")
    reason = "Speech at 5.0-15.0s could not be transcribed."
    outro_reason = "Speech at 590.0-595.0s could not be transcribed."
    project_path = tmp_path / "Book.lvproj"
    save_project(
        project_path, chapters,
        reviews={chapters[1].resolve(): TrackReview(
            intro_review_reason=reason, outro_review_reason=outro_reason,
        )},
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(project_path), ""))
    window = MainWindow()

    window._open_project()

    assert "[Review" not in window._track_list.item(0).text()
    assert "[Review intro] [Review outro]" in window._track_list.item(1).text()
    assert window._track_list.item(1).toolTip() == f"Intro: {reason}\nOutro: {outro_reason}"
    window.close()
    application.processEvents()


@pytest.mark.parametrize("detect_intro,expected", [(True, None), (False, "Earlier warning.")])
def test_rerun_clears_review_warning_only_when_intro_was_analyzed(tmp_path, monkeypatch, detect_intro, expected):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    path = tmp_path / "chapter01.mp3"
    window._tracks = [path]
    item = QListWidgetItem(path.name)
    item.setData(Qt.ItemDataRole.UserRole, str(path))
    window._track_list.addItem(item)
    monkeypatch.setattr(main_window, "read_track", lambda path: (600.0, path.stem, "Reader"))
    window._review(path).intro_review_reason = "Earlier warning."
    window._project_detection = DetectionSettings(detect_intro=detect_intro, detect_outro=True)
    outro = BoundarySuggestion(580.0, 93.0, "end of chapter")

    window._bulk_detection_finished([TrackBoundaryResult(path, BoundaryDetection(None, outro))])

    assert window._review(path).intro_review_reason == expected
    assert ("[Review intro]" in window._track_list.item(0).text()) == bool(expected)
    window.close()
    application.processEvents()


def test_detect_all_button_queues_all_tracks_with_progress(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    paths = [tmp_path / "chapter01.mp3", tmp_path / "chapter02.mp3"]
    window._tracks = paths
    window._set_track_controls_enabled(True)
    captured: dict[str, object] = {}

    def analyze_batch(track_paths, *, progress_callback, settings):
        captured["paths"] = track_paths
        captured["settings"] = settings
        progress_callback("Chapter 1 of 2: chapter01")
        return []

    def capture_worker(function, on_success, message, **options):
        captured.update(
            function=function,
            on_success=on_success,
            message=message,
            options=options,
        )

    monkeypatch.setattr(main_window, "analyze_tracks_boundaries", analyze_batch)
    monkeypatch.setattr(window, "_start_worker", capture_worker)

    window._detect_all_button.click()

    progress: list[str] = []
    assert captured["function"](progress.append) == []
    assert captured["paths"] == paths
    assert captured["settings"] == window._project_detection
    assert captured["on_success"] == window._bulk_detection_finished
    assert captured["options"] == {"with_progress": True}
    assert progress == ["Chapter 1 of 2: chapter01"]
    assert "2 chapters" in str(captured["message"])
    window.close()
    application.processEvents()


def test_export_all_requires_confirmation_before_opening_folder(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._tracks = [tmp_path / "chapter01.mp3"]
    captured = {}

    def decline(*args):
        captured["warning"] = args[2]
        captured["default"] = args[4]
        return main_window.QMessageBox.StandardButton.No

    monkeypatch.setattr(main_window.QMessageBox, "warning", decline)
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: pytest.fail("Folder dialog opened before confirmation"),
    )
    monkeypatch.setattr(
        window,
        "_start_worker",
        lambda *_args, **_kwargs: pytest.fail("Export started before confirmation"),
    )

    window._export_all_chapters()

    assert "all loaded chapters" in captured["warning"].lower()
    assert "validated every cut point" in captured["warning"].lower()
    assert captured["default"] == main_window.QMessageBox.StandardButton.No
    window.close()
    application.processEvents()


def test_export_all_uses_each_chapters_saved_cut_settings(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    paths = [tmp_path / "chapter01.mp3", tmp_path / "chapter02.mp3"]
    window._tracks = paths
    window._review(paths[0]).keep_start_seconds = 8
    window._review(paths[0]).keep_end_seconds = 90
    window._review(paths[0]).intro_removal_start_seconds = 3
    window._review(paths[1]).keep_start_seconds = 12
    window._review(paths[1]).keep_end_seconds = 95
    window._review(paths[1]).outro_resume_start_seconds = 105
    monkeypatch.setattr(main_window, "read_track", lambda _path: (120, "Chapter", "Reader"))
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        lambda *_args: main_window.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: str(tmp_path))
    calls = []
    monkeypatch.setattr(
        main_window, "trim_mp3",
        lambda source, target, start, end, **kwargs: calls.append((source, target, start, end, kwargs)) or target,
    )
    captured = {}
    monkeypatch.setattr(
        window,
        "_start_worker",
        lambda function, on_success, message, **kwargs: captured.update(
            function=function, on_success=on_success, message=message, options=kwargs,
        ),
    )

    window._export_all_chapters()
    progress = []
    result = captured["function"](progress.append)

    assert [call[2:] for call in calls] == [
        (8, 90, {"intro_removal_start": 3, "outro_resume_start": None}),
        (12, 95, {"intro_removal_start": 0.0, "outro_resume_start": 105}),
    ]
    assert [call[1].name for call in calls] == [
        "chapter01_trimmed.mp3", "chapter02_trimmed.mp3",
    ]
    assert len(result[0]) == 2 and result[1] == []
    assert len(progress) == 2
    assert captured["on_success"] == window._export_all_finished
    assert captured["options"] == {"with_progress": True}
    window.close()
    application.processEvents()


def test_detect_all_button_is_above_chapter_list():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(900, 590)
    window.show()
    application.processEvents()

    assert window._detect_all_button.geometry().bottom() < window._track_list.geometry().top()

    window.close()
    application.processEvents()


@pytest.mark.parametrize("size", [(900, 590), (1200, 760)])
def test_chapter_editor_separates_detection_cuts_review_and_export(size, monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(main_window, "cleanup_incomplete_downloads", lambda _folder: [])
    window = MainWindow()
    window.resize(*size)
    window._set_track_controls_enabled(True)
    window.show()
    application.processEvents()

    def rect(widget):
        return QRect(widget.mapTo(window.centralWidget(), QPoint(0, 0)), widget.size())

    intro = rect(window._intro_panel)
    outro = rect(window._outro_panel)
    assert rect(window._detect_button).bottom() < intro.top()
    assert intro.right() < outro.left()
    assert intro.contains(rect(window._notice_start))
    assert intro.contains(rect(window._keep_start))
    assert intro.contains(rect(window._preview_intro_button))
    assert intro.contains(rect(window._preview_kept_button))
    assert outro.contains(rect(window._keep_end))
    assert outro.contains(rect(window._ending_resume))
    assert outro.contains(rect(window._preview_outro_button))
    assert outro.contains(rect(window._preview_ending_button))
    assert rect(window._preview_outro_button).bottom() < rect(window._preview_ending_button).top()
    assert rect(window._preview_kept_button).top() > rect(window._preview_intro_button).bottom()
    assert rect(window._trim_button).top() > rect(window._preview_kept_button).bottom()
    assert window._ending_resume.isEnabled()

    original_rect = rect(window._preview_intro_button)
    window._preview_intro_button.setText("Stop intro preview")
    application.processEvents()
    assert rect(window._preview_intro_button) == original_rect
    assert not window._preview_intro_button.icon().isNull()
    window.close()
    application.processEvents()


def test_switching_to_shorter_chapter_does_not_save_previous_range(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    long_chapter = tmp_path / "chapter01.mp3"
    short_chapter = tmp_path / "chapter02.mp3"
    durations = {long_chapter: 2000.0, short_chapter: 600.0}
    monkeypatch.setattr(
        main_window,
        "read_track",
        lambda path: (durations[path], path.stem, "Reader"),
    )
    window._tracks = [long_chapter, short_chapter]
    for path in window._tracks:
        item = QListWidgetItem(path.name)
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        window._track_list.addItem(item)

    window._track_list.setCurrentRow(0)
    window._keep_start.setValue(700)
    window._keep_end.setValue(1800)
    assert window._review(long_chapter).keep_range == (700, 1800)

    window._track_list.setCurrentRow(1)

    assert window._keep_start.value() == 0
    assert window._keep_end.value() == 600
    assert window._review(short_chapter).keep_range is None

    window._track_list.setCurrentRow(0)

    assert window._keep_start.value() == 700
    assert window._keep_end.value() == 1800
    window.close()
    application.processEvents()


def test_preferences_apply_without_changing_trim_ranges(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(main_window, "load_settings", lambda: AppSettings())
    window = MainWindow()
    path = tmp_path / "chapter.mp3"
    window._review(path).keep_start_seconds, window._review(path).keep_end_seconds = 8, 500
    updated = AppSettings(
        projects_directory=str(tmp_path / "Projects"),
        output_directory=str(tmp_path / "Output"),
        detection=DetectionSettings(intro_window_seconds=90),
    )

    class FakeDialog:
        result_settings = updated

        def __init__(self, *_args):
            pass

        def exec(self):
            return main_window.QDialog.DialogCode.Accepted

    saved = []
    monkeypatch.setattr(main_window, "SettingsDialog", FakeDialog)
    monkeypatch.setattr(main_window, "save_settings", saved.append)
    window._show_settings()

    assert saved == [updated]
    assert window._project_folder() == tmp_path / "Projects"
    assert window._project_detection.intro_window_seconds == 90
    assert window._review(path).keep_range == (8, 500)
    window._set_busy(True)
    assert not window._settings_action.isEnabled()
    window._set_busy(False)
    assert window._settings_action.isEnabled()
    window.close()
    application.processEvents()


def test_interior_notice_range_survives_switching_and_previews_intro_intervals(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    first = tmp_path / "01.mp3"
    second = tmp_path / "02.mp3"
    monkeypatch.setattr(main_window, "read_track", lambda path: (100, path.stem, "Reader"))
    window._add_tracks([first, second], persist=False)
    window._on_worker_progress(TrackBoundaryResult(
        first,
        BoundaryDetection(
            BoundarySuggestion(15, 95, "LibriVox", removal_start_seconds=4), None
        ),
    ))
    assert window._notice_start.value() == 4
    assert window._keep_start.value() == 15
    window._notice_start.setValue(4.25)
    window._track_list.setCurrentRow(1)
    assert window._notice_start.value() == 0
    window._track_list.setCurrentRow(0)
    assert window._notice_start.value() == 4.25
    assert window._keep_start.value() == 15
    assert "opening 00:00-00:04" in window._track_list.item(0).text()

    calls = []
    monkeypatch.setattr(window, "_play_interval", lambda *args, **kwargs: calls.append((args, kwargs)))
    window._preview_intro_cut()
    assert calls[-1][0][:2] == (4.25, 15)
    window._preview_kept_intro()
    assert calls[-1][0][:2] == (0, 4.25)
    assert calls[-1][1]["following_intervals"] == [(15, 100)]
    window.close()
    application.processEvents()


def test_preview_advances_to_following_retained_interval():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()

    class Player:
        position = None

        def setPosition(self, position):
            self.position = position

    player = Player()
    window._player = player
    window._pending_preview = (0, 4250)
    window._preview_queue = [(15000, 100000)]
    window._on_position_changed(4250)
    assert player.position == 15000
    assert window._pending_preview == (15000, 100000)
    assert window._preview_queue == []
    window.close()
    application.processEvents()


def test_final_chapter_keeps_book_ending_in_preview_and_saved_ranges(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    first = tmp_path / "01.mp3"
    last = tmp_path / "28.mp3"
    monkeypatch.setattr(main_window, "read_track", lambda path: (200, path.stem, "Author"))
    window._add_tracks([first, last], persist=False)
    window._track_list.setCurrentRow(1)
    result = TrackBoundaryResult(
        last,
        BoundaryDetection(None, BoundarySuggestion(
            170, 95, "End of chapter", resume_after_outro_seconds=174,
        )),
    )
    window._on_worker_progress(result)
    assert window._keep_end.value() == 170
    assert window._ending_resume.value() == 174
    assert window._review(last).outro_resume_start_seconds == 174
    window._track_list.setCurrentRow(0)
    assert window._ending_resume.value() == 0
    window._track_list.setCurrentRow(1)
    assert window._ending_resume.value() == 174
    calls = []
    monkeypatch.setattr(window, "_play_interval", lambda *args, **kwargs: calls.append((args, kwargs)))
    window._preview_outro_cut()
    assert calls[-1][0][:2] == (170, 174)
    window._preview_kept_intro()
    assert calls[-1][0][:2] == (0, 170)
    assert calls[-1][1]["following_intervals"] == [(174, 200)]
    window.close()
    application.processEvents()


@pytest.mark.parametrize("keep_start,keep_end,resume,expected_start,following", [
    (0, 100, 0, 95, []),
    (98, 100, 0, 98, []),
    (0, 100, 105, 95, [(105, 120)]),
    (0, 120, 0, 115, []),
])
def test_chapter_ending_preview_uses_retained_intervals(
    tmp_path, monkeypatch, keep_start, keep_end, resume, expected_start, following,
):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    path = tmp_path / "chapter.mp3"
    monkeypatch.setattr(window, "_selected_path", lambda: path)
    monkeypatch.setattr(main_window, "read_track", lambda _path: (120, "Chapter", "Reader"))
    window._keep_start.setValue(keep_start)
    window._keep_end.setValue(keep_end)
    window._ending_resume.setValue(resume)
    calls = []
    monkeypatch.setattr(window, "_play_interval", lambda *args, **kwargs: calls.append((args, kwargs)))

    window._preview_chapter_ending()

    assert calls[0][0][:2] == (expected_start, keep_end)
    assert calls[0][0][2] is window._preview_ending_button
    assert calls[0][1]["following_intervals"] == following
    window.close()
    application.processEvents()


def test_chapter_ending_button_toggles_and_skips_removed_outro(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    path = tmp_path / "chapter.mp3"
    monkeypatch.setattr(window, "_selected_path", lambda: path)
    monkeypatch.setattr(main_window, "read_track", lambda _path: (120, "Chapter", "Reader"))
    window._set_track_controls_enabled(True)
    window._keep_end.setValue(100)
    window._ending_resume.setValue(105)

    class LoadedPlayer:
        play_calls = 0
        current_position = None

        def source(self):
            return QUrl.fromLocalFile(str(path.resolve()))

        def mediaStatus(self):
            return QMediaPlayer.MediaStatus.LoadedMedia

        def stop(self):
            window._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        def setSource(self, _source):
            pass

        def setPosition(self, value):
            self.current_position = value

        def play(self):
            self.play_calls += 1

    player = LoadedPlayer()
    window._player = player
    button = window._preview_ending_button
    button.click()
    assert window._pending_preview == (95000, 100000)
    assert button.text() == "Stop ending preview"
    window._on_position_changed(100000)
    assert player.current_position == 105000
    assert window._pending_preview == (105000, 120000)
    button.click()
    assert window._pending_preview is None
    assert window._preview_queue == []
    assert button.text() == "Preview chapter ending"
    button.click()
    assert player.play_calls == 2
    window._stop_preview()
    window.close()
    application.processEvents()