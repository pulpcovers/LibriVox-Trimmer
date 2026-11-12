import pytest
from PySide6.QtCore import Qt, QUrl, QPoint, QRect
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QFileDialog, QListWidgetItem

from librivox_trimmer import main_window
from librivox_trimmer import project as project_module
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


@pytest.mark.parametrize("auto_analyze", [False, True])
def test_download_finished_starts_analyze_all_only_when_preference_is_enabled(window, tmp_path, monkeypatch, auto_analyze):
    from types import SimpleNamespace

    project = SimpleNamespace(
        path=tmp_path / "Book.lvproj",
        tracks=[SimpleNamespace(path=tmp_path / "01.mp3")],
    )
    calls = []
    window._settings = AppSettings(auto_analyze_after_download=auto_analyze)
    monkeypatch.setattr(window, "_set_project", lambda _project: None)
    monkeypatch.setattr(window, "_add_tracks", lambda tracks, **_kwargs: calls.append(("load", tracks)))
    monkeypatch.setattr(window, "_detect_all", lambda: calls.append(("analyze", None)))

    window._download_finished(project)

    assert calls == [("load", [project.tracks[0].path])] + (
        [("analyze", None)] if auto_analyze else []
    )


@pytest.mark.parametrize("problem,expected_model", [({"verify_boundaries": True}, "tiny.en"), ({"model_size": "missing-model"}, "base.en")])
def test_open_project_warns_about_optional_settings_and_still_loads_chapters(window, tmp_path, monkeypatch, problem, expected_model):
    import json
    from librivox_trimmer.project import TrackReview, save_project

    metadata = lambda path: (120.0, path.stem, "Reader")
    monkeypatch.setattr(project_module, "read_track", metadata)
    monkeypatch.setattr(main_window, "read_track", metadata)
    chapter = tmp_path / "chapter.mp3"
    chapter.write_bytes(b"placeholder")
    path = tmp_path / "Book.lvproj"
    save_project(
        path, [chapter], detection_settings=DetectionSettings(model_size="tiny.en"),
        reviews={chapter.resolve(): TrackReview(3, 110)},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["detection_settings"].update(problem)
    path.write_text(json.dumps(payload), encoding="utf-8")
    original = path.read_bytes()
    warnings = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(path), ""))
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda _parent, title, text, *_args, **_kwargs: warnings.append((title, text)))
    monkeypatch.setattr(main_window.QMessageBox, "critical", lambda *_args: pytest.fail("A recoverable settings issue should not be fatal"))
    monkeypatch.setattr(window, "_offer_import_external_tracks", lambda: None)

    window._open_project()

    assert window._project_path == path.resolve()
    assert window._tracks == [chapter.resolve()]
    assert window._review(chapter).keep_range == (3, 110)
    assert window._project_detection.model_size == expected_model
    assert len(warnings) == 1
    assert warnings[0][0] == "Project opened with warnings"
    if "verify_boundaries" in problem:
        assert "verify_boundaries" in warnings[0][1]
    else:
        assert "current preferences" in warnings[0][1].lower()
    assert path.read_bytes() == original


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


def test_status_text_is_selectable_for_copying_paths(window):
    flags = window._status.textInteractionFlags()

    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
    assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard


@pytest.mark.parametrize("method", ["warning", "critical", "information", "question"])
def test_message_boxes_show_literal_text_and_preserve_default_button(qapp, monkeypatch, method):
    message = "Could not open <b>chapter</b>.mp3 & notes"
    captured = {}

    def capture(dialog):
        captured.update(text=dialog.text(), format=dialog.textFormat(),
                        default=dialog.standardButton(dialog.defaultButton()))
        return int(main_window.QMessageBox.StandardButton.No)

    monkeypatch.setattr(main_window.QMessageBox, "exec", capture)
    result = getattr(main_window.QMessageBox, method)(
        None, "Check chapter", message,
        main_window.QMessageBox.StandardButton.Yes | main_window.QMessageBox.StandardButton.No,
        main_window.QMessageBox.StandardButton.No,
    )

    assert captured == {"text": message, "format": Qt.TextFormat.PlainText,
                        "default": main_window.QMessageBox.StandardButton.No}
    assert result == main_window.QMessageBox.StandardButton.No


def test_help_menu_follows_settings_and_opens_workflow_guide(window, monkeypatch):
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


@pytest.mark.parametrize("has_outputs", [True, False])
def test_export_failure_dialog_lists_all_failures_as_literal_details(window, tmp_path, monkeypatch, has_outputs):
    failures = ["<b>chapter01</b>.mp3: output already exists", "chapter02.mp3: permission denied"]
    outputs = [tmp_path / "chapter03_trimmed.mp3"] if has_outputs else []
    captured = {}

    def capture(dialog):
        captured.update(text=dialog.text(), details=dialog.detailedText(), format=dialog.textFormat())
        return int(main_window.QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(main_window.QMessageBox, "exec", capture)

    window._export_all_finished((outputs, failures))

    assert f"Exported {len(outputs)} chapter(s); 2 failed." in captured["text"]
    assert (str(tmp_path) in captured["text"]) == has_outputs
    assert captured["details"] == "\n\n".join(failures)
    assert captured["format"] == Qt.TextFormat.PlainText
    assert "Retry failed chapters individually" in captured["text"]


def test_task_failure_separates_recovery_advice_from_technical_details(window, monkeypatch):
    window._worker_success = window._trim_finished
    window._worker_description = "Trimming chapter01.mp3..."
    captured = {}

    def capture(dialog):
        captured.update(text=dialog.text(), details=dialog.detailedText())
        return int(main_window.QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(main_window.QMessageBox, "exec", capture)

    window._show_worker_error("<b>FFmpeg</b>: permission denied")

    assert "Trimming chapter01.mp3" in captured["text"]
    assert "output folder is writable" in captured["text"]
    assert captured["details"] == "<b>FFmpeg</b>: permission denied"
    assert "<b>" not in captured["text"]


def test_cleanup_confirmation_explains_permanent_deletion_and_defaults_to_cancel(window, tmp_path, monkeypatch):
    window._project_path = tmp_path / "book.lvproj"
    captured = {}

    def capture(dialog):
        captured.update(text=dialog.text(), default=dialog.standardButton(dialog.defaultButton()))
        return int(main_window.QMessageBox.StandardButton.Cancel)

    monkeypatch.setattr(main_window.QMessageBox, "exec", capture)

    window._clean_up_project()

    assert "all chapter copies and cached analysis" in captured["text"]
    assert "Original MP3s outside the project will not be deleted" in captured["text"]
    assert "cannot be undone" in captured["text"]
    assert captured["default"] == main_window.QMessageBox.StandardButton.Cancel
    assert window._project_path == tmp_path / "book.lvproj"


def test_download_progress_displays_percentage_and_byte_fallback(window):
    window._on_worker_progress(DownloadProgress(2 * 1024 * 1024, 10 * 1024 * 1024))

    assert window._activity_progress.minimum() == 0
    assert window._activity_progress.maximum() == 100
    assert window._activity_progress.value() == 20
    assert window._status.text() == "Downloading audiobook: 20% (2.0 MB of 10.0 MB)"

    window._on_worker_progress(DownloadProgress(3 * 1024 * 1024, None))

    assert window._activity_progress.minimum() == 0
    assert window._activity_progress.maximum() == 0
    assert window._status.text() == "Downloading audiobook: 3.0 MB received"


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


def test_preview_buttons_toggle_stop_replay_and_switching(window, monkeypatch, tmp_path):
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
    assert intro_button.text() == "Play removed intro"
    assert window._pending_preview is None
    assert not window._playback_timer.isActive()

    intro_button.click()
    assert player.play_calls == 2

    kept_button = window._preview_kept_button
    assert kept_button.text() == "Play Kept Intro"
    assert window._intro_panel.layout().indexOf(kept_button) == (
        window._intro_panel.layout().indexOf(intro_button) + 1
    )
    kept_button.click()
    assert intro_button.text() == "Play removed intro"
    assert kept_button.text() == "Stop kept-intro preview"
    assert player.position == 1000
    assert player.play_calls == 3

    kept_button.click()
    assert kept_button.text() == "Play Kept Intro"
    assert window._pending_preview is None
    assert player.play_calls == 3


@pytest.mark.parametrize("review_reason", [None, "No clear pause; preview this cut."])
def test_bulk_detection_applies_each_result_and_summarizes_failures(window, tmp_path, monkeypatch, review_reason):
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


def test_bulk_unmatched_chapter_with_untranscribed_speech_is_marked_for_review(window, tmp_path, monkeypatch):
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


def test_review_warnings_are_restored_when_project_is_reopened(window, tmp_path, monkeypatch):
    from librivox_trimmer import project as project_module
    from librivox_trimmer.project import TrackReview, save_project

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
    monkeypatch.setattr(
        main_window.QMessageBox, "question", lambda *_args: main_window.QMessageBox.StandardButton.No,
    )

    window._open_project()

    assert "[Review" not in window._track_list.item(0).text()
    assert "[Review intro] [Review outro]" in window._track_list.item(1).text()
    assert window._track_list.item(1).toolTip() == f"Intro: {reason}\nOutro: {outro_reason}"


@pytest.mark.parametrize("detect_intro,expected", [(True, None), (False, "Earlier warning.")])
def test_rerun_clears_review_warning_only_when_intro_was_analyzed(window, tmp_path, monkeypatch, detect_intro, expected):
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


def test_selected_analysis_receives_saved_catalog_metadata(window, frame_grid, monkeypatch):
    metadata = {"title": "Catalog Book", "chapters": []}
    window._book_metadata = metadata
    captured = {}
    monkeypatch.setattr(window, "_start_worker", lambda function, *_args, **_kwargs: captured.update(work=function))
    monkeypatch.setattr(
        main_window, "analyze_track_boundaries",
        lambda path, **options: captured.update(
            path=path, metadata=options["book_metadata"],
        ),
    )

    window._detect_selected()
    captured["work"](lambda _message: None)

    assert captured["path"] == window._tracks[0]
    assert captured["metadata"] is metadata


def test_detect_all_button_queues_all_tracks_with_progress(window, tmp_path, monkeypatch):
    paths = [tmp_path / "chapter01.mp3", tmp_path / "chapter02.mp3"]
    window._tracks = paths
    window._set_track_controls_enabled(True)
    captured: dict[str, object] = {}

    def analyze_batch(track_paths, *, progress_callback, settings, should_cancel, book_metadata=None):
        captured["paths"] = track_paths
        captured["settings"] = settings
        captured["should_cancel"] = should_cancel
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
    assert callable(captured["on_success"])
    assert captured["options"] == {"with_progress": True}
    assert progress == ["Chapter 1 of 2: chapter01"]
    assert "2 chapters" in str(captured["message"])
    assert window._detect_all_button.text() == "Cancel Analysis"
    assert window._detect_all_button.isEnabled()
    assert captured["should_cancel"]() is False

    window._detect_all_button.click()

    assert captured["should_cancel"]() is True
    assert not window._detect_all_button.isEnabled()
    window._finish_worker()
    assert window._detect_all_button.text() == "Analyse all chapters"


@pytest.mark.parametrize("cached,has_url", [(False, True), (True, True), (False, False)])
def test_analyze_all_fetches_metadata_only_when_missing_and_url_is_saved(window, monkeypatch, tmp_path, cached, has_url):
    metadata = {"schema_version": 1, "title": "Book", "chapters": [{"title": "Chapter"}]}
    window._tracks = [tmp_path / "chapter.mp3"]
    window._source_url = "https://librivox.org/book/" if has_url else ""
    window._book_metadata = metadata if cached else None
    captured = {}
    fetches, saves, analyses = [], [], []
    monkeypatch.setattr(window, "_start_worker", lambda function, *_args, **_kwargs: captured.update(work=function))
    monkeypatch.setattr(main_window, "fetch_project_metadata", lambda url: fetches.append(url) or metadata)
    monkeypatch.setattr(window, "_save_project_without_dialog", lambda: saves.append(window._book_metadata))
    monkeypatch.setattr(
        main_window, "analyze_tracks_boundaries",
        lambda paths, **options: analyses.append((paths, options["book_metadata"])) or [],
    )

    window._detect_all()
    assert fetches == []
    captured["work"](window._on_worker_progress)

    should_fetch = not cached and has_url
    assert fetches == ([window._source_url] if should_fetch else [])
    assert saves == ([metadata] if should_fetch else [])
    assert analyses == [(window._tracks, metadata if cached or should_fetch else None)]
    assert window._book_metadata == (metadata if cached or should_fetch else None)


@pytest.mark.parametrize("failure", ["network", "missing"])
def test_metadata_failure_does_not_block_analysis_and_is_reported(window, monkeypatch, tmp_path, failure):
    window._tracks = [tmp_path / "chapter.mp3"]
    window._source_url = "https://librivox.org/book/"
    captured, analyses = {}, []
    monkeypatch.setattr(window, "_start_worker", lambda function, *_args, **_kwargs: captured.update(work=function))
    monkeypatch.setattr(window, "_save_project_without_dialog", lambda: None)

    def fetch(_url):
        if failure == "network":
            raise main_window.RequestException("Connection unavailable")
        return None

    monkeypatch.setattr(main_window, "fetch_project_metadata", fetch)
    monkeypatch.setattr(main_window, "analyze_tracks_boundaries", lambda paths, **_kwargs: analyses.append(paths) or [])

    window._detect_all()
    results = captured["work"](window._on_worker_progress)
    window._tracks = []
    window._bulk_detection_finished(results)

    assert len(analyses) == 1
    assert window._book_metadata is None
    assert "analysis continued without it" in window._status.text()


def test_cancel_before_metadata_fetch_skips_network_and_analysis_work(window, monkeypatch, tmp_path):
    window._tracks = [tmp_path / "chapter.mp3"]
    window._source_url = "https://librivox.org/book/"
    captured = {}
    monkeypatch.setattr(window, "_start_worker", lambda function, *_args, **_kwargs: captured.update(work=function))
    monkeypatch.setattr(main_window, "fetch_project_metadata", lambda *_args: pytest.fail("Cancelled metadata request"))

    def analyze(_paths, **options):
        assert options["should_cancel"]()
        return []

    monkeypatch.setattr(main_window, "analyze_tracks_boundaries", analyze)
    window._detect_all()
    window._detect_all()

    assert captured["work"](window._on_worker_progress) == []
    assert window._book_metadata is None


def test_cancelled_bulk_analysis_reports_kept_chapters(window, monkeypatch):
    monkeypatch.setattr(window, "_save_project_without_dialog", lambda: None)

    window._bulk_detection_finished([], cancelled=True, total=3)

    assert window._status.text().startswith("Analysis cancelled: kept results for 0 of 3 chapters")


def test_export_selected_preserves_source_filename(window, frame_grid, tmp_path, monkeypatch):
    source = window._tracks[0]
    output_directory = tmp_path / "exports"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(output_directory))
    captured = {}
    monkeypatch.setattr(
        window, "_start_worker",
        lambda function, *_args: captured.update(function=function),
    )
    calls = []
    monkeypatch.setattr(
        main_window, "trim_mp3",
        lambda source, target, *_args, **_kwargs: calls.append((source, target)) or target,
    )

    window._trim_selected()
    captured["function"]()

    assert calls == [(source, output_directory / source.name)]


def test_export_all_requires_confirmation_before_opening_folder(window, tmp_path, monkeypatch):
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


def test_export_all_uses_each_chapters_saved_cut_settings(window, tmp_path, monkeypatch):
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
        "chapter01.mp3", "chapter02.mp3",
    ]
    assert len(result[0]) == 2 and result[1] == []
    assert len(progress) == 2
    assert captured["on_success"] == window._export_all_finished
    assert captured["options"] == {"with_progress": True}


def test_detect_all_button_is_above_chapter_list(window, qapp):
    window.resize(900, 590)
    window.show()
    qapp.processEvents()

    assert window._detect_all_button.geometry().bottom() < window._track_list.geometry().top()


@pytest.mark.parametrize("size", [(900, 590), (1200, 760)])
def test_chapter_editor_separates_detection_cuts_review_and_export(size, window, qapp):
    window.resize(*size)
    window._set_track_controls_enabled(True)
    window.show()
    qapp.processEvents()

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
    qapp.processEvents()
    assert rect(window._preview_intro_button) == original_rect
    assert not window._preview_intro_button.icon().isNull()


def test_switching_to_shorter_chapter_does_not_save_previous_range(window, tmp_path, monkeypatch):
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


def test_preferences_apply_without_changing_trim_ranges(window, tmp_path, monkeypatch):
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


def test_interior_notice_range_survives_switching_and_previews_intro_intervals(window, add_chapters, monkeypatch):
    first, second = add_chapters("01.mp3", "02.mp3")
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


def test_preview_advances_to_following_retained_interval(window):
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


def test_final_chapter_keeps_book_ending_in_preview_and_saved_ranges(window, add_chapters, monkeypatch):
    first, last = add_chapters("01.mp3", "28.mp3", duration=200)
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


@pytest.mark.parametrize("keep_start,keep_end,resume,expected_start,following", [
    (0, 100, 0, 95, []),
    (98, 100, 0, 98, []),
    (0, 100, 105, 95, [(105, 120)]),
    (0, 120, 0, 115, []),
])
def test_chapter_ending_preview_uses_retained_intervals(
    window, tmp_path, monkeypatch, keep_start, keep_end, resume, expected_start, following,
):
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


def test_chapter_ending_button_toggles_and_skips_removed_outro(window, tmp_path, monkeypatch):
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
    assert button.text() == "Play kept ending"
    button.click()
    assert player.play_calls == 2
    window._stop_preview()


@pytest.fixture
def frame_grid(window, add_chapters):
    from librivox_trimmer.waveform import FrameGrid

    (path,) = add_chapters("chapter.mp3", duration=120)
    window._track_list.setCurrentRow(0)
    grid = FrameGrid(origin=0.0021, frame_seconds=576 / 22050)
    window._frame_grids[path.resolve()] = grid
    window._keep_start.setValue(10)
    window._keep_end.setValue(100)
    return grid


@pytest.mark.parametrize("notice_start,expected_index", [(0, 1), (4, 0)])
def test_selecting_chapter_targets_its_first_intro_boundary(window, frame_grid, notice_start, expected_index):
    path = window._tracks[0]
    window._review(path).intro_removal_start_seconds = notice_start
    window._waveform_field.setCurrentIndex(2)

    window._select_track(0)

    assert window._waveform_field.currentIndex() == expected_index
    boundary = frame_grid.nearest_boundary(5 if notice_start else 11)
    window._waveform.cut_changed.emit(boundary)
    review = window._review(path)
    if notice_start:
        assert review.intro_removal_start_seconds == window._notice_start.value()
        assert review.intro_removal_start_seconds != notice_start
        assert review.keep_start_seconds == 10
    else:
        assert review.keep_start_seconds == window._keep_start.value()
        assert review.keep_start_seconds != 10
        assert review.intro_removal_start_seconds == 0


def test_editing_intro_at_rounded_eof_saves_exact_duration_and_preserves_cut(window, tmp_path, monkeypatch):
    duration = 1946.567619047619
    path = tmp_path / "chapter.mp3"
    path.write_bytes(b"placeholder")
    metadata = lambda _path: (duration, "Chapter", "Reader")
    monkeypatch.setattr(main_window, "read_track", metadata)
    monkeypatch.setattr(project_module, "read_track", metadata)
    project_path = tmp_path / "Book.lvproj"
    project_module.save_project(
        project_path, [path], reviews={path.resolve(): project_module.TrackReview(17.5, duration)},
    )
    project = project_module.load_project(project_path)
    window._set_project(project)
    window._reviews = {track.path.resolve(): track.review for track in project.tracks}
    window._add_tracks([path], persist=False)
    monkeypatch.setattr(
        main_window.QMessageBox, "critical", lambda *_args: pytest.fail("Saving a valid intro must not fail"),
    )

    assert window._keep_end.value() > duration
    assert window._selected_cut_field()[0] is window._keep_start
    window._waveform.cut_changed.emit(18.12)

    saved = project_module.load_project(project_path).tracks[0].review
    assert saved.keep_range == (18.12, duration)
    assert saved.intro_removal_start_seconds == 0
    window._select_track(0)
    assert window._keep_start.value() == 18.12
    assert window._review(path).keep_range == (18.12, duration)


@pytest.mark.parametrize("reason", ["Check the intro", None])
def test_mark_reviewed_hides_flags_reversibly_and_analysis_resets_it(window, add_chapters, monkeypatch, reason):
    first, second = add_chapters("01.mp3", "02.mp3")
    saves = []
    monkeypatch.setattr(window, "_save_project_without_dialog", lambda: saves.append(True))
    review = window._review(first)
    review.intro_review_reason = "Check the intro"
    review.outro_review_reason = "Check the outro"
    window._refresh_track_item(first)

    window._mark_reviewed.setChecked(True)

    assert len(saves) == 1
    assert review.manually_reviewed
    assert review.intro_review_reason == "Check the intro"
    assert "[Review" not in window._track_list.item(0).text()
    assert window._track_list.item(0).background().style() == Qt.BrushStyle.NoBrush
    window._track_list.setCurrentRow(1)
    assert not window._mark_reviewed.isChecked()
    window._track_list.setCurrentRow(0)
    assert window._mark_reviewed.isChecked()
    assert len(saves) == 1
    window._mark_reviewed.setChecked(False)
    assert len(saves) == 2
    assert "[Review intro]" in window._track_list.item(0).text()
    assert window._track_list.item(0).background().color().name() == "#fff7c2"
    window._mark_reviewed.setChecked(True)

    window._apply_detection(first, BoundaryDetection(None, None, intro_review_reason=reason))

    assert not review.manually_reviewed
    assert not window._mark_reviewed.isChecked()
    assert ("[Review intro]" in window._track_list.item(0).text()) == bool(reason)
    assert review.outro_review_reason is None


def test_waveform_click_writes_frame_snapped_value_to_selected_field(window, frame_grid):
    grid = frame_grid
    window._waveform_field.setCurrentIndex(1)
    boundary = grid.origin + 400 * grid.frame_seconds

    window._apply_waveform_cut(boundary)

    assert window._keep_start.value() == round(boundary + grid.frame_seconds / 2, 2)
    assert grid.exported_cut(window._keep_start.value(), starts_kept_audio=True) == pytest.approx(boundary)
    assert window._review(window._tracks[0]).keep_start_seconds == window._keep_start.value()

    window._waveform_field.setCurrentIndex(2)
    end_boundary = grid.origin + 3500 * grid.frame_seconds
    window._apply_waveform_cut(end_boundary)
    assert grid.exported_cut(window._keep_end.value(), starts_kept_audio=False) == pytest.approx(end_boundary)


@pytest.mark.parametrize("decoded_end", [99.95, 100.0])
def test_end_waveform_does_not_reload_when_marker_is_just_past_audio_end(
    window, frame_grid, monkeypatch, decoded_end,
):
    from array import array
    from librivox_trimmer.waveform import WaveformWindow

    window._waveform_field.setCurrentIndex(2)
    window._keep_end.setValue(100.0)
    window._keep_end.setMaximum(100.0)
    path = window._tracks[0].resolve()
    worker = object()
    window._waveform_workers.add(worker)
    waveform = WaveformWindow(95.0, 8000, array("h", [0]) * round((decoded_end - 95.0) * 8000))
    reloads = []
    monkeypatch.setattr(window, "_refresh_waveform", lambda: reloads.append(True))

    window._waveform_loaded(worker, (window._waveform_request, path, frame_grid, waveform))

    assert reloads == []
    assert window._waveform.window_range == (95.0, decoded_end)
    assert window._waveform.cut == frame_grid.exported_cut(100.0, starts_kept_audio=False)


def test_waveform_recenters_once_after_editing_stops_and_keeps_old_window_visible(window, frame_grid, monkeypatch):
    from array import array
    from PySide6.QtTest import QTest
    from librivox_trimmer.waveform import WaveformWindow

    path = window._tracks[0]
    path.write_bytes(b"placeholder")
    window._waveform_field.setCurrentIndex(1)
    window._waveform.set_waveform(WaveformWindow(5.0, 8000, array("h", [0]) * 40000), frame_grid)
    assert window._waveform_recenter_timer.interval() == 1000
    window._waveform_recenter_timer.setInterval(10)
    workers = []
    monkeypatch.setattr(window._thread_pool, "start", workers.append)

    window._keep_start.setValue(8.0)
    window._keep_start.setValue(9.0)

    assert workers == []
    assert window._waveform_recenter_timer.isActive()
    assert window._waveform.window_range == (5.0, 10.0)
    QTest.qWait(40)

    assert len(workers) == 1
    assert window._waveform.window_range == (5.0, 10.0)
    assert not window._waveform_recenter_timer.isActive()
    workers[0].run()
    assert window._waveform.window_range == pytest.approx((6.5, 11.5))


def test_waveform_recenter_waits_for_drag_release_and_cancels_on_field_switch(window, frame_grid):
    window._waveform_field.setCurrentIndex(1)
    window._waveform._dragging = True
    window._waveform.drag_started.emit()
    window._keep_start.setValue(9.0)

    assert not window._waveform_recenter_timer.isActive()

    window._waveform._dragging = False
    window._waveform.drag_finished.emit()
    assert window._waveform_recenter_timer.isActive()
    window._waveform_field.setCurrentIndex(2)
    assert not window._waveform_recenter_timer.isActive()


@pytest.mark.parametrize("field_index,value,window_start,sample_count", [(0, 0, 0, 40000), (2, 120, 115, 39960)])
def test_waveform_recenter_skips_unchanged_window_at_file_edges(window, frame_grid, monkeypatch, field_index, value, window_start, sample_count):
    from array import array
    from librivox_trimmer.waveform import WaveformWindow

    window._waveform_field.setCurrentIndex(field_index)
    window._selected_cut_field()[0].setValue(value)
    window._waveform.set_waveform(WaveformWindow(window_start, 8000, array("h", [0]) * sample_count), frame_grid)
    monkeypatch.setattr(window, "_refresh_waveform", lambda **_kwargs: pytest.fail("Already centered at the file edge"))

    window._recenter_waveform()


def test_waveform_recenter_cancels_when_chapter_changes(window, add_chapters):
    add_chapters("one.mp3", "two.mp3")
    window._track_list.setCurrentRow(0)
    window._keep_start.setValue(1)
    assert window._waveform_recenter_timer.isActive()

    window._track_list.setCurrentRow(1)

    assert not window._waveform_recenter_timer.isActive()


def test_waveform_recenter_cancels_when_window_closes(window, frame_grid):
    assert window._waveform_recenter_timer.isActive()

    window.close()

    assert not window._waveform_recenter_timer.isActive()


@pytest.mark.parametrize("new_drag", [False, True])
def test_background_waveform_does_not_replace_new_edit_or_active_drag(window, frame_grid, new_drag):
    from array import array
    from librivox_trimmer.waveform import WaveformWindow

    old_window = WaveformWindow(5.0, 8000, array("h", [0]) * 40000)
    new_window = WaveformWindow(7.5, 8000, array("h", [0]) * 40000)
    window._waveform.set_waveform(old_window, frame_grid)
    request = window._waveform_request
    if new_drag:
        window._waveform._dragging = True
        window._waveform.drag_started.emit()
    else:
        window._keep_start.setValue(9)
    worker = object()

    window._waveform_loaded(worker, (request, window._tracks[0].resolve(), frame_grid, new_window))

    assert window._waveform.window_range == (5.0, 10.0)


def test_waveform_refuses_cuts_out_of_order(window, frame_grid):
    grid = frame_grid
    window._waveform_field.setCurrentIndex(0)
    review = window._review(window._tracks[0])
    saved = (review.keep_range, review.intro_removal_start_seconds)

    window._apply_waveform_cut(grid.origin + 600 * grid.frame_seconds)

    assert window._notice_start.value() == 0
    assert "before Resume audio at" in window._status.text()
    assert (review.keep_range, review.intro_removal_start_seconds) == saved


def test_explicit_zero_intro_start_displays_the_start_of_the_file(window, frame_grid, monkeypatch):
    path = window._tracks[0]
    path.write_bytes(b"placeholder")
    windows = []
    read_waveform = main_window.read_waveform

    def capture_waveform(source, start, end):
        windows.append((start, end))
        return read_waveform(source, start, end)

    monkeypatch.setattr(main_window, "read_waveform", capture_waveform)
    monkeypatch.setattr(window._thread_pool, "start", lambda worker: worker.run())

    window._waveform_field.setCurrentIndex(0)

    assert windows == [(0, 5)]
    assert window._selected_cut_field()[0] is window._notice_start
    assert window._waveform.cut == 0
    image = window._waveform.grab().toImage()
    plot = window._waveform.plot_rect()
    color = image.pixelColor(int(plot.center().x()), int(plot.top() + 10))
    assert color.red() > color.green()


def test_preview_buttons_switch_waveform_to_first_cut_played(window, frame_grid, monkeypatch):
    window._set_track_controls_enabled(True)
    started = []
    monkeypatch.setattr(window, "_start_pending_preview", lambda: started.append(True))
    monkeypatch.setattr(window._player, "setSource", lambda _source: None)

    def label():
        return window._waveform_field.currentText()

    window._preview_outro_button.click()
    assert label() == "End narration at"
    window._stop_preview()

    window._preview_intro_button.click()
    assert label() == "Resume audio at"
    window._stop_preview()

    window._notice_start.setValue(4)
    window._waveform_field.setCurrentIndex(3)
    window._preview_kept_button.click()
    assert label() == "Remove intro notice from"
    window._waveform_field.setCurrentIndex(3)
    window._preview_kept_button.click()
    assert window._pending_preview is None
    assert label() == "Retain book ending from"

    window._preview_ending_button.click()
    assert label() == "End narration at"
    window._stop_preview()


def test_clean_up_releases_the_media_player_before_deleting(window, tmp_path, monkeypatch):
    window._project_path = tmp_path / "book.lvproj"
    window._tracks = [tmp_path / "chapter.mp3"]
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *a, **k: main_window.QMessageBox.StandardButton.Yes)
    window._player.setSource(QUrl.fromLocalFile(str(tmp_path / "chapter.mp3")))
    seen = []
    monkeypatch.setattr(main_window, "cleanup_project", lambda path: seen.append(window._player.source().isEmpty()))

    window._clean_up_project()

    assert seen == [True]


def test_failed_automatic_save_shows_one_error_dialog_until_a_save_succeeds(window, tmp_path, monkeypatch):
    window._project_path = tmp_path / "book.lvproj"
    dialogs = []
    monkeypatch.setattr(
        main_window.QMessageBox, "critical", lambda _parent, title, text: dialogs.append((title, text)),
    )
    outcome = {"fail": True}

    def write():
        if outcome["fail"]:
            raise OSError("disk full")

    monkeypatch.setattr(window, "_write_project", write)

    window._save_project_without_dialog()
    window._save_project_without_dialog()
    assert len(dialogs) == 1
    assert dialogs[0][0] == "Project not saved"
    assert "disk full" in dialogs[0][1]

    outcome["fail"] = False
    window._save_project_without_dialog()
    outcome["fail"] = True
    window._save_project_without_dialog()
    assert len(dialogs) == 2


def test_chapters_with_review_flags_are_highlighted_yellow(window, add_chapters):
    paths = add_chapters("a.mp3", "b.mp3")
    window._review(paths[1]).outro_review_reason = "Check the ending"

    window._refresh_all_track_items()

    assert window._track_list.item(0).background().style() == Qt.BrushStyle.NoBrush
    assert window._track_list.item(1).background().color().name() == "#fff7c2"
    window._review(paths[1]).outro_review_reason = ""
    window._refresh_track_item(paths[1])
    assert window._track_list.item(1).background().style() == Qt.BrushStyle.NoBrush


def test_opening_a_project_with_network_paths_asks_first_and_can_be_declined(window, tmp_path, monkeypatch):
    project_path = tmp_path / "Shared.lvproj"
    project_path.write_text(
        '{"format_version": 1, "tracks": [{"file": {"type": "absolute", '
        '"path": "\\\\\\\\attacker.example\\\\share\\\\a.mp3"}, "review": {}}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(project_path), ""))
    asked = []

    def decline(_parent, title, text, *_args):
        asked.append(text)
        return main_window.QMessageBox.StandardButton.No

    monkeypatch.setattr(main_window.QMessageBox, "warning", decline)
    monkeypatch.setattr(
        main_window.QMessageBox, "critical", lambda *_args: pytest.fail("Declining should not be an error"),
    )

    window._open_project()

    assert len(asked) == 1 and "attacker.example" in asked[0]
    assert window._tracks == []


def _run_workers_inline(window, monkeypatch):
    monkeypatch.setattr(
        window, "_start_worker",
        lambda function, on_success, message, **_kw: on_success(function(lambda _message: None)),
    )


def test_open_files_copies_them_into_a_new_named_project(window, tmp_path, monkeypatch):
    from librivox_trimmer.project import load_project

    monkeypatch.setattr(main_window, "read_track", lambda path: (100.0, path.stem, "Reader"))
    monkeypatch.setattr(project_module, "read_track", lambda path: (100.0, path.stem, "Reader"))
    source_dir = tmp_path / "My Downloads"
    source_dir.mkdir()
    sources = [source_dir / "02.mp3", source_dir / "01.mp3"]
    for source in sources:
        source.write_bytes(b"mp3 data")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_a, **_k: ([str(p) for p in sources], ""))
    monkeypatch.setattr(main_window.QInputDialog, "getText", lambda *_a, **_k: ("Zenda", True))
    _run_workers_inline(window, monkeypatch)

    window._open_files()

    project_path = tmp_path / "Projects" / "Zenda.lvproj"
    chapters = project_module.project_assets_dir(project_path) / "chapters"
    assert [path.name for path in window._tracks] == ["01.mp3", "02.mp3"]
    assert all(path.resolve().parent == chapters.resolve() for path in window._tracks)
    assert all(source.exists() for source in sources)
    assert window._project_path == project_path
    saved = load_project(project_path)
    assert [track.path.parent for track in saved.tracks] == [chapters.resolve()] * 2


def test_cancelling_the_project_name_prompt_imports_nothing(window, tmp_path, monkeypatch):
    source = tmp_path / "01.mp3"
    source.write_bytes(b"mp3")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_a, **_k: ([str(source)], ""))
    monkeypatch.setattr(main_window.QInputDialog, "getText", lambda *_a, **_k: ("", False))
    monkeypatch.setattr(window, "_start_worker", lambda *_a, **_k: pytest.fail("Nothing should be copied"))

    window._open_files()

    assert window._tracks == [] and window._project_path is None


def test_opening_an_older_project_offers_to_copy_external_chapters(window, tmp_path, monkeypatch):
    from librivox_trimmer.project import TrackReview, load_project, save_project

    stub = lambda path: (600.0, path.stem, "Reader")
    monkeypatch.setattr(project_module, "read_track", stub)
    monkeypatch.setattr(main_window, "read_track", stub)
    chapter = tmp_path / "elsewhere" / "chapter01.mp3"
    chapter.parent.mkdir()
    chapter.write_bytes(b"mp3")
    project_path = tmp_path / "Book.lvproj"
    save_project(project_path, [chapter], reviews={chapter.resolve(): TrackReview(2.5, 100.0)})
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(project_path), ""))
    monkeypatch.setattr(
        main_window.QMessageBox, "question", lambda *_args: main_window.QMessageBox.StandardButton.Yes,
    )
    _run_workers_inline(window, monkeypatch)

    window._open_project()

    chapters = project_module.project_assets_dir(project_path) / "chapters"
    assert window._tracks[0].resolve().parent == chapters.resolve()
    assert window._review(window._tracks[0]).keep_range == (2.5, 100.0)
    assert chapter.exists()
    assert load_project(project_path).tracks[0].path.parent == chapters.resolve()


def test_labels_showing_file_text_never_render_html(window):
    window._track_info.setText("<b>bold</b> <img src='x'>")

    assert window._track_info.textFormat() == Qt.TextFormat.PlainText
    assert window._status.textFormat() == Qt.TextFormat.PlainText


def test_unreadable_preferences_warn_the_user_and_fall_back_to_defaults(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "projects_dir", lambda: tmp_path / "Projects")

    def broken():
        raise ValueError("Expecting value: line 1 column 1")

    monkeypatch.setattr(main_window, "load_settings", broken)
    shown = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda _p, title, text: shown.append((title, text)))
    main = MainWindow()

    qapp.processEvents()

    assert main._settings == AppSettings()
    assert len(shown) == 1 and "Expecting value" in shown[0][1]
    assert "using defaults" in main._status.text()
    main.close()
    qapp.processEvents()


def test_preview_running_to_the_end_of_the_file_finishes_when_the_media_ends(window):
    window._active_preview_button = window._preview_ending_button
    window._active_preview_original_text = "Play kept ending"
    window._pending_preview = (1_000_000, 1_445_900)
    window._preview_started = True

    window._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)

    assert window._pending_preview is None
    assert window._preview_ending_button.text() == "Play kept ending"
    assert window._status.text() == "Preview finished"


def test_focusing_a_time_field_selects_it_for_the_waveform(window, frame_grid, qapp):
    window.show()
    qapp.processEvents()

    window._keep_end.setFocus()
    qapp.processEvents()

    assert window._waveform_field.currentText() == "End narration at"