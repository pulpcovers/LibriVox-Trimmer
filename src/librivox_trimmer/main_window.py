from pathlib import Path
import shutil
from dataclasses import replace

from PySide6.QtCore import QThreadPool, QTimer, Qt, QUrl, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QDoubleSpinBox,
    QProgressBar,
    QDialog,
    QFrame,
    QStyle,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from librivox_trimmer.archive import extract_mp3s
from librivox_trimmer.audio import trim_mp3
from librivox_trimmer.config import projects_dir
from librivox_trimmer.detection import (
    TrackBoundaryResult,
    analyze_track_boundaries,
    analyze_tracks_boundaries,
)
from librivox_trimmer.download_state import (
    cleanup_incomplete_downloads,
    lock_download_directory,
)
from librivox_trimmer.librivox_client import download_project_archive
from librivox_trimmer.metadata import read_track
from librivox_trimmer.models import natural_path_key
from librivox_trimmer.project import (
    PROJECT_SUFFIX,
    ProjectDocument,
    TrackReview,
    cleanup_project,
    load_project,
    new_project_path_for_url,
    project_assets_dir,
    save_project as write_project,
)
from librivox_trimmer.progress import DownloadProgress
from librivox_trimmer.workers import Worker
from librivox_trimmer.settings import AppSettings, load_settings, save_settings
from librivox_trimmer.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LibriVox Trimmer")
        self.resize(900, 590)
        self._thread_pool = QThreadPool.globalInstance()
        self._active_worker: Worker | None = None
        self._worker_success = None
        self._tracks: list[Path] = []
        self._project_path: Path | None = None
        self._source_url = ""
        self._managed_audio_dir: Path | None = None
        self._reviews: dict[Path, TrackReview] = {}
        self._has_missing_tracks = False
        self._pending_preview: tuple[int, int] | None = None
        self._preview_queue: list[tuple[int, int]] = []
        self._preview_started = False
        self._active_preview_button: QPushButton | None = None
        self._active_preview_original_text = ""
        self._bulk_results: dict[Path, TrackBoundaryResult] = {}
        self._bulk_errors: dict[Path, str] = {}
        self._settings_error = ""
        try:
            self._settings = load_settings()
        except (OSError, ValueError, TypeError) as error:
            self._settings = AppSettings()
            self._settings_error = f"Could not load preferences; using defaults: {error}"
        self._project_detection = self._settings.detection
        self._removed_incomplete_downloads = cleanup_incomplete_downloads(self._project_folder())

        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.errorOccurred.connect(self._on_player_error)
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(100)
        self._playback_timer.timeout.connect(self._check_preview_end)

        self._build_ui()

    def _build_ui(self) -> None:
        self._build_project_menu()
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        title = QLabel("LibriVox Trimmer")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(title)

        download_row = QHBoxLayout()
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("Paste a LibriVox project URL")
        self._download_button = QPushButton("Fetch chapters")
        self._download_button.clicked.connect(self._download_project)
        download_row.addWidget(self._url_input, 1)
        download_row.addWidget(self._download_button)
        layout.addLayout(download_row)

        local_row = QHBoxLayout()
        self._open_button = QPushButton("Open MP3 files")
        self._open_button.clicked.connect(self._open_files)
        self._track_count = QLabel("No chapters loaded")
        self._track_count.setStyleSheet("color: #5d6670;")
        local_row.addWidget(self._open_button)
        local_row.addWidget(self._track_count, 1)
        layout.addLayout(local_row)

        content = QHBoxLayout()
        chapter_column = QVBoxLayout()
        chapter_toolbar = QHBoxLayout()
        self._detect_all_button = QPushButton("Analyse all chapters")
        self._detect_all_button.clicked.connect(self._detect_all)
        chapter_toolbar.addWidget(self._detect_all_button)
        self._export_all_button = QPushButton("Export all chapters...")
        self._export_all_button.clicked.connect(self._export_all_chapters)
        chapter_toolbar.addWidget(self._export_all_button)
        chapter_toolbar.addStretch(1)
        chapter_column.addLayout(chapter_toolbar)
        self._track_list = QListWidget()
        self._track_list.currentRowChanged.connect(self._select_track)
        chapter_column.addWidget(self._track_list, 1)
        content.addLayout(chapter_column, 1)

        editor = QVBoxLayout()
        editor.setSpacing(16)
        self._track_info = QLabel("Select a chapter to review")
        self._track_info.setWordWrap(True)
        self._track_info.setMinimumWidth(350)
        editor.addWidget(self._track_info)

        self._detect_button = QPushButton("Analyse selected chapter")
        self._detect_button.setMinimumHeight(36)
        self._detect_button.setStyleSheet(
            "QPushButton { background-color: #176b62; color: white; "
            "border: 1px solid #12574f; border-radius: 4px; padding: 7px 14px; }"
            "QPushButton:hover { background-color: #1b7b70; }"
            "QPushButton:pressed { background-color: #12574f; }"
            "QPushButton:disabled { background-color: #e1e5e8; "
            "color: #697386; border-color: #d0d6db; }"
        )
        self._detect_all_button.setStyleSheet(self._detect_button.styleSheet())
        self._detect_button.clicked.connect(self._detect_selected)
        detection_row = QHBoxLayout()
        detection_row.addWidget(self._detect_button)
        detection_row.addStretch(1)
        editor.addLayout(detection_row)
        editor.addSpacing(4)

        cut_sections = QHBoxLayout()
        cut_sections.setSpacing(24)
        self._intro_panel = QWidget()
        self._outro_panel = QWidget()
        intro_layout = QVBoxLayout(self._intro_panel)
        outro_layout = QVBoxLayout(self._outro_panel)
        for section_layout, caption in ((intro_layout, "Intro"), (outro_layout, "Outro")):
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(10)
            heading = QLabel(caption)
            heading.setStyleSheet("font-size: 15px; font-weight: 600;")
            section_layout.addWidget(heading)
        intro_form = QFormLayout()
        outro_form = QFormLayout()
        intro_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        outro_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        intro_form.setVerticalSpacing(8)
        outro_form.setVerticalSpacing(8)
        self._notice_start = self._make_time_input()
        self._ending_resume = self._make_time_input()
        self._ending_resume.setSpecialValueText("None")
        self._ending_resume.setToolTip(
            "Keep the audio from this time to the end of the file, after removing "
            "the chapter-ending announcement. Zero means no retained ending."
        )
        self._notice_start.setToolTip(
            "Audio before this time is kept. Only audio from this time to "
            "Resume audio at is removed. Use zero to remove the entire opening."
        )
        self._keep_start = self._make_time_input()
        self._keep_end = self._make_time_input()
        self._keep_start.valueChanged.connect(self._save_selected_cut_range)
        self._keep_end.valueChanged.connect(self._save_selected_cut_range)
        self._notice_start.valueChanged.connect(self._save_selected_cut_range)
        self._ending_resume.valueChanged.connect(self._save_selected_cut_range)
        intro_form.addRow("Remove intro notice from", self._notice_start)
        intro_form.addRow("Resume audio at", self._keep_start)
        outro_form.addRow("End narration at", self._keep_end)
        outro_form.addRow("Retain book ending from", self._ending_resume)
        intro_layout.addLayout(intro_form)
        outro_layout.addLayout(outro_form)

        self._preview_intro_button = QPushButton("Preview intro cut")
        self._preview_outro_button = QPushButton("Preview outro cut")
        self._preview_ending_button = QPushButton("Preview chapter ending")
        self._preview_ending_button.setToolTip(
            "Play the last five seconds of retained narration, skip the removed "
            "outro, and include any retained book-ending announcement."
        )
        self._preview_kept_button = QPushButton("Preview Kept Intro")
        self._preview_intro_button.clicked.connect(self._preview_intro_cut)
        self._preview_outro_button.clicked.connect(self._preview_outro_cut)
        self._preview_ending_button.clicked.connect(self._preview_chapter_ending)
        self._preview_kept_button.clicked.connect(self._preview_kept_intro)
        for button, stop_caption in (
            (self._preview_intro_button, "Stop intro preview"),
            (self._preview_outro_button, "Stop outro preview"),
            (self._preview_ending_button, "Stop ending preview"),
            (self._preview_kept_button, "Stop kept-intro preview"),
        ):
            button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            button.setMinimumHeight(34)
            text_width = max(
                button.fontMetrics().horizontalAdvance(button.text()),
                button.fontMetrics().horizontalAdvance(stop_caption),
            )
            button.setMinimumWidth(text_width + 48)
        intro_layout.addWidget(self._preview_intro_button)
        intro_layout.addWidget(self._preview_kept_button)
        outro_layout.addWidget(self._preview_outro_button)
        outro_layout.addWidget(self._preview_ending_button)
        intro_layout.addStretch(1)
        outro_layout.addStretch(1)
        cut_sections.addWidget(self._intro_panel, 1)
        cut_sections.addWidget(self._outro_panel, 1)
        editor.addLayout(cut_sections)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        editor.addWidget(divider)
        editor.addStretch(1)

        self._trim_button = QPushButton("Export trimmed chapter...")
        self._trim_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self._trim_button.setMinimumHeight(36)
        self._trim_button.clicked.connect(self._trim_selected)
        export_row = QHBoxLayout()
        export_row.addStretch(1)
        export_row.addWidget(self._trim_button)
        editor.addLayout(export_row)
        content.addLayout(editor, 2)
        layout.addLayout(content, 1)

        self._status = QLabel("Ready")
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._status.setStyleSheet("color: #5d6670; border-top: 1px solid #d8dde2;")
        layout.addWidget(self._status)
        self._activity_progress = QProgressBar()
        self._activity_progress.setTextVisible(True)
        self._activity_progress.setFormat("%p%")
        self._activity_progress.setRange(0, 0)
        self._activity_progress.hide()
        layout.addWidget(self._activity_progress)
        self.setCentralWidget(root)
        self._set_track_controls_enabled(False)
        if self._removed_incomplete_downloads:
            self._status.setText(
                f"Cleaned up {len(self._removed_incomplete_downloads)} interrupted download(s)"
            )
        if self._settings_error:
            self._status.setText(self._settings_error)

    def _build_project_menu(self) -> None:
        project_menu = self.menuBar().addMenu("&Project")
        open_action = project_menu.addAction("Open Project...")
        open_action.triggered.connect(self._open_project)
        save_action = project_menu.addAction("Save Project")
        save_action.triggered.connect(self._save_project)
        project_menu.addSeparator()
        cleanup_action = project_menu.addAction("Clean Up Project...")
        cleanup_action.triggered.connect(self._clean_up_project)
        settings_menu = self.menuBar().addMenu("&Settings")
        self._settings_action = settings_menu.addAction("Preferences...")
        self._settings_action.triggered.connect(self._show_settings)
        help_menu = self.menuBar().addMenu("&Help")
        help_action = help_menu.addAction("How to use LibriVox Trimmer")
        help_action.triggered.connect(self._show_help)

    def _project_folder(self) -> Path:
        if self._settings.projects_directory:
            return self._settings.project_folder()
        return projects_dir()

    def _show_settings(self) -> None:
        if self._active_worker is not None:
            return
        dialog = SettingsDialog(
            replace(self._settings, detection=self._project_detection), self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_settings
        try:
            updated.project_folder().mkdir(parents=True, exist_ok=True)
            updated.output_folder().mkdir(parents=True, exist_ok=True)
            save_settings(updated)
        except OSError as error:
            QMessageBox.critical(self, "Could not save settings", str(error))
            return
        self._settings = updated
        self._project_detection = updated.detection
        self._save_project_without_dialog()
        self._status.setText("Settings saved. Detection changes apply on the next run.")

    def _show_help(self) -> None:
        QMessageBox.about(
            self,
            "Using LibriVox Trimmer",
            """
            <h3>Trim an audiobook chapter</h3>
            <ol>
              <li>Paste a LibriVox project URL and choose <b>Fetch chapters</b>, or use
                  <b>Open MP3 files</b> to load chapters from your computer.</li>
              <li>Select a chapter and choose <b>Analyse selected chapter</b>, or choose
                  <b>Analyse all chapters</b> to analyse the loaded list. Load the whole
                  book when detecting its first or last chapter so book-ending detection
                  has the right context.</li>
              <li>Review the suggested Intro and Outro times. Detection only suggests
                  cuts; it never changes or trims your source files. A <b>Review</b>
                  marker means the audio needs a closer listen. Hover over the chapter
                  for details.</li>
              <li>Use the preview buttons to listen to the removed intro/outro, the
                  preserved opening through the kept narration and book ending. Adjust the time fields
                  if needed.</li>
              <li>Choose <b>Export trimmed chapter</b> for the selected chapter, or
                  <b>Export all chapters...</b> to batch-export the loaded list. Batch
                  export asks you to confirm that you have validated every cut point.
                  Select an output folder; source MP3s remain unchanged.</li>
            </ol>
            <h3>Projects and preferences</h3>
            <p><b>Project &gt; Save Project</b> saves chapter locations, cut times,
               and review notes so you can continue later. Use <b>Open Project</b>
               to resume. <b>Clean Up Project</b> deletes the project and its
               downloaded chapter files.</p>
            <p><b>Settings &gt; Preferences</b> controls storage folders, download
               quality, and detection options. Changing detection preferences does
               not update saved cut times; run detection again to apply them.</p>
            """,
        )

    @staticmethod
    def _make_time_input() -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setDecimals(2)
        field.setSingleStep(0.25)
        field.setRange(0, 24 * 60 * 60)
        field.setSuffix(" s")
        return field

    def _set_track_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._notice_start,
            self._ending_resume,
            self._keep_start,
            self._keep_end,
            self._detect_button,
            self._detect_all_button,
            self._export_all_button,
            self._preview_intro_button,
            self._preview_outro_button,
            self._preview_ending_button,
            self._preview_kept_button,
            self._trim_button,
        ):
            widget.setEnabled(enabled)

    def _set_busy(self, busy: bool) -> None:
        self._settings_action.setEnabled(not busy)
        self.menuBar().actions()[0].setEnabled(not busy)
        self._track_list.setEnabled(not busy)
        self._download_button.setEnabled(not busy)
        self._open_button.setEnabled(not busy)
        self._trim_button.setEnabled(bool(self._tracks) and not busy)
        self._detect_button.setEnabled(bool(self._tracks) and not busy)
        self._detect_all_button.setEnabled(bool(self._tracks) and not busy)
        self._export_all_button.setEnabled(bool(self._tracks) and not busy)

    def _open_files(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Open chapter MP3 files",
            str(Path.home()),
            "MP3 audio (*.mp3)",
        )
        if filenames:
            self._add_tracks([Path(filename) for filename in filenames])

    def _download_project(self) -> None:
        project_url = self._url_input.text().strip()
        if not project_url.startswith(("https://", "http://")):
            QMessageBox.warning(self, "Invalid URL", "Enter a LibriVox project URL.")
            return

        project_path = new_project_path_for_url(
            project_url,
            self._project_folder(),
        )
        assets_dir = project_assets_dir(project_path)
        preferences = self._settings

        def download_and_extract(report_progress) -> ProjectDocument:
            with lock_download_directory(project_path.parent, assets_dir):
                assets_dir.mkdir(parents=True, exist_ok=False)
                try:
                    archive_path = download_project_archive(
                        project_url,
                        assets_dir,
                        progress_callback=report_progress,
                        quality=preferences.download_quality,
                    )
                    tracks = extract_mp3s(archive_path, assets_dir / "chapters")
                    archive_path.unlink(missing_ok=True)
                    return write_project(
                        project_path,
                        tracks,
                        source_url=project_url,
                        managed_audio_dir=assets_dir,
                        detection_settings=preferences.detection,
                    )
                except Exception:
                    shutil.rmtree(assets_dir, ignore_errors=True)
                    raise

        self._start_worker(
            download_and_extract,
            self._download_finished,
            f"Finding audiobook; project will be saved to {project_path}",
            with_progress=True,
        )

    def _download_finished(self, project: ProjectDocument) -> None:
        self._tracks.clear()
        self._track_list.clear()
        self._reviews.clear()
        self._set_project(project)
        self._add_tracks([track.path for track in project.tracks], persist=False)
        self._status.setText(f"Project saved: {project.path}")

    def _open_project(self) -> None:
        initial_directory = (
            self._project_path.parent
            if self._project_path is not None
            else self._project_folder()
        )
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open audiobook project",
            str(initial_directory),
            "LibriVox project (*.lvproj)",
        )
        if not selected_path:
            return
        try:
            project = load_project(Path(selected_path))
        except Exception as error:
            QMessageBox.critical(self, "Could not open project", str(error))
            return

        self._set_project(project)
        self._tracks.clear()
        self._track_list.clear()
        self._reviews = {track.path.resolve(): replace(track.review) for track in project.tracks}
        existing_paths = [track.path for track in project.tracks if track.path.is_file()]
        missing_paths = [track.path for track in project.tracks if not track.path.is_file()]
        self._has_missing_tracks = bool(missing_paths)
        self._add_tracks(existing_paths, persist=False)
        self._url_input.setText(project.source_url)
        if missing_paths:
            missing_names = ", ".join(path.name for path in missing_paths[:5])
            remainder = len(missing_paths) - 5
            if remainder > 0:
                missing_names += f" and {remainder} more"
            self._status.setText(f"Project opened; missing files: {missing_names}")
            QMessageBox.warning(
                self,
                "Some project files are missing",
                "The project manifest was opened, but these MP3 files could not be found:\n"
                f"{missing_names}\n\nUse Save Project only after restoring the missing files.",
            )
        else:
            self._status.setText(f"Project resumed: {project.path}")

    def _set_project(self, project: ProjectDocument) -> None:
        self._project_path = project.path
        self._source_url = project.source_url
        self._managed_audio_dir = project.managed_audio_dir
        self._has_missing_tracks = False
        self._project_detection = project.detection_settings or self._settings.detection

    def _save_project(self) -> None:
        if not self._tracks:
            QMessageBox.information(self, "No chapters", "Load MP3 chapters before saving a project.")
            return
        if self._has_missing_tracks:
            QMessageBox.warning(
                self,
                "Missing project files",
                "Restore missing project files before saving, so their references are not lost.",
            )
            return
        if self._project_path is None:
            selected_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save audiobook project",
                str(self._project_folder() / f"Audiobook Project{PROJECT_SUFFIX}"),
                "LibriVox project (*.lvproj)",
            )
            if not selected_path:
                return
            self._project_path = Path(selected_path)
            if self._project_path.suffix.lower() != PROJECT_SUFFIX:
                self._project_path = self._project_path.with_suffix(PROJECT_SUFFIX)

        self._save_selected_cut_range()
        try:
            project = self._write_project()
        except Exception as error:
            QMessageBox.critical(self, "Could not save project", str(error))
            return
        self._set_project(project)
        self._status.setText(f"Project saved: {project.path}")

    def _save_selected_cut_range(self, _value: float | None = None) -> None:
        path = self._selected_path()
        if path is None or self._keep_end.value() <= self._keep_start.value():
            return
        notice_start = self._notice_start.value()
        if notice_start > 0 and notice_start >= self._keep_start.value():
            return
        ending_resume = self._ending_resume.value()
        if ending_resume > 0 and not self._keep_end.value() < ending_resume < self._keep_end.maximum():
            return
        review = self._review(path)
        review.keep_start_seconds = self._keep_start.value()
        review.keep_end_seconds = self._keep_end.value()
        review.intro_removal_start_seconds = notice_start
        review.outro_resume_start_seconds = ending_resume if ending_resume > 0 else None
        self._refresh_track_item(path)
        if self._project_path is not None and not self._has_missing_tracks:
            try:
                self._write_project()
            except Exception as error:
                self._status.setText(f"Could not save review position: {error}")

    def _clean_up_project(self) -> None:
        if self._project_path is None:
            QMessageBox.information(self, "No project", "Open or create a project first.")
            return
        response = QMessageBox.question(
            self,
            "Clean up project",
            "Delete this project file and its downloaded chapter files?\n\n"
            "MP3 files opened from elsewhere will not be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        try:
            cleanup_project(self._project_path)
        except Exception as error:
            QMessageBox.critical(self, "Could not clean up project", str(error))
            return
        self._project_path = None
        self._source_url = ""
        self._managed_audio_dir = None
        self._reviews.clear()
        self._has_missing_tracks = False
        self._tracks.clear()
        self._track_list.clear()
        self._track_count.setText("No chapters loaded")
        self._track_info.setText("Select a chapter to review")
        self._set_track_controls_enabled(False)
        self._status.setText("Project and its downloaded files were removed")

    def _start_worker(self, function, on_success, message: str, *, with_progress=False) -> None:
        self._set_busy(True)
        self._status.setText(message)
        self._activity_progress.setRange(0, 0)
        self._activity_progress.show()
        worker = Worker(function, accepts_progress=with_progress)
        self._active_worker = worker
        self._worker_success = on_success
        worker.signals.finished.connect(self._on_worker_finished)
        worker.signals.failed.connect(self._show_worker_error)
        worker.signals.failed.connect(self._on_worker_failed)
        worker.signals.progress.connect(self._on_worker_progress)
        self._thread_pool.start(worker)

    @Slot(object)
    def _on_worker_progress(self, update) -> None:
        if isinstance(update, str):
            self._status.setText(update)
            return
        if isinstance(update, TrackBoundaryResult):
            self._apply_bulk_detection_result(update)
            return
        if not isinstance(update, DownloadProgress):
            return

        downloaded = self._format_bytes(update.downloaded_bytes)
        if update.total_bytes is not None:
            percent = min(100, int(update.downloaded_bytes * 100 / update.total_bytes))
            total = self._format_bytes(update.total_bytes)
            self._activity_progress.setRange(0, 100)
            self._activity_progress.setValue(percent)
            self._status.setText(
                f"Downloading audiobook: {percent}% ({downloaded} of {total})"
            )
        else:
            self._activity_progress.setRange(0, 0)
            self._status.setText(f"Downloading audiobook: {downloaded} received")

    @Slot(object)
    def _on_worker_finished(self, result) -> None:
        callback = self._worker_success
        self._finish_worker()
        if callback is not None:
            callback(result)

    @Slot(str)
    def _on_worker_failed(self, _message: str) -> None:
        self._finish_worker()

    def _finish_worker(self) -> None:
        self._active_worker = None
        self._worker_success = None
        self._activity_progress.hide()
        self._set_busy(False)

    @staticmethod
    def _format_bytes(value: int) -> str:
        megabytes = value / (1024 * 1024)
        if megabytes >= 1:
            return f"{megabytes:.1f} MB"
        return f"{value / 1024:.0f} KB"

    def _show_worker_error(self, message: str) -> None:
        self._status.setText("Task failed")
        QMessageBox.critical(self, "Could not complete task", message)

    def _add_tracks(self, paths: list[Path], *, persist: bool = True) -> None:
        added_paths: list[Path] = []
        for path in sorted(paths, key=natural_path_key):
            if path in self._tracks or path.suffix.lower() != ".mp3":
                continue
            try:
                duration, title, _artist = read_track(path)
            except Exception as error:
                self._status.setText(f"Skipped {path.name}: {error}")
                continue
            self._tracks.append(path)
            added_paths.append(path)

        self._tracks.sort(key=natural_path_key)
        self._track_list.clear()
        for path in self._tracks:
            list_item = QListWidgetItem(self._track_item_text(path))
            list_item.setData(Qt.ItemDataRole.UserRole, str(path))
            list_item.setToolTip(self._review_tooltip(path))
            self._track_list.addItem(list_item)

        self._track_count.setText(f"{len(self._tracks)} chapter(s)")
        if added_paths:
            first_added = min(added_paths, key=natural_path_key)
            self._track_list.setCurrentRow(self._tracks.index(first_added))
            self._status.setText(f"Loaded {len(added_paths)} chapter(s), sorted by filename")
            if persist and self._project_path is not None:
                self._save_project_without_dialog()
        elif not self._tracks:
            self._status.setText("No valid MP3 chapters found")

    def _review(self, path: Path) -> TrackReview:
        return self._reviews.setdefault(path.resolve(), TrackReview())

    def _track_item_text(self, path: Path) -> str:
        duration, title, _artist = read_track(path)
        label = f"{title}    {self._format_time(duration)}"
        review = self._review(path)
        if review.keep_range is not None:
            keep_start, keep_end = review.keep_range
            label += f"    Keep {self._format_time(keep_start)}-{self._format_time(keep_end)}"
            if review.intro_removal_start_seconds > 0:
                label += f" + opening 00:00-{self._format_time(review.intro_removal_start_seconds)}"
            if review.outro_resume_start_seconds is not None:
                label += f" + ending {self._format_time(review.outro_resume_start_seconds)}"
        if review.intro_review_reason:
            label += " [Review intro]"
        if review.outro_review_reason:
            label += " [Review outro]"
        return label

    def _review_tooltip(self, path: Path) -> str:
        review = self._review(path)
        parts = []
        if review.intro_review_reason:
            parts.append("Intro: " + review.intro_review_reason)
        if review.outro_review_reason:
            parts.append("Outro: " + review.outro_review_reason)
        return "\n".join(parts)

    def _refresh_track_item(self, path: Path) -> None:
        try:
            row = self._tracks.index(path)
        except ValueError:
            return
        item = self._track_list.item(row)
        if item is not None:
            item.setText(self._track_item_text(path))
            item.setToolTip(self._review_tooltip(path))

    def _refresh_all_track_items(self) -> None:
        for row, path in enumerate(self._tracks):
            item = self._track_list.item(row)
            if item is not None:
                item.setText(self._track_item_text(path))
                item.setToolTip(self._review_tooltip(path))

    def _write_project(self) -> ProjectDocument:
        return write_project(
            self._project_path,
            self._tracks,
            source_url=self._source_url,
            managed_audio_dir=self._managed_audio_dir,
            reviews=self._reviews,
            detection_settings=self._project_detection,
        )

    def _save_project_without_dialog(self) -> None:
        if self._project_path is None or self._has_missing_tracks:
            return
        try:
            self._write_project()
        except Exception as error:
            self._status.setText(f"Could not update project: {error}")

    def _selected_path(self) -> Path | None:
        item = self._track_list.currentItem()
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _select_track(self, row: int) -> None:
        if row < 0 or row >= len(self._tracks):
            self._set_track_controls_enabled(False)
            return
        path = self._tracks[row]
        try:
            duration, title, artist = read_track(path)
        except Exception as error:
            self._track_info.setText(f"Could not read {path.name}: {error}")
            self._set_track_controls_enabled(False)
            return
        self._track_info.setText(
            f"{title}\n{artist}\n{path}\nDuration: {self._format_time(duration)}"
        )
        self._show_review(path, duration)
        self._set_track_controls_enabled(True)

    def _show_review(self, path: Path, duration: float) -> None:
        """Load a chapter's saved cut points into the controls without triggering a save."""
        review = self._review(path)
        keep_range = review.keep_range
        if keep_range is None or not keep_range[0] < keep_range[1] <= duration:
            keep_range = (0.0, duration)
        values = (
            (self._keep_start, keep_range[0]),
            (self._keep_end, keep_range[1]),
            (self._notice_start, review.intro_removal_start_seconds),
            (self._ending_resume, review.outro_resume_start_seconds or 0.0),
        )
        for control, value in values:
            control.blockSignals(True)
            control.setRange(0, duration)
            control.setValue(value)
            control.blockSignals(False)

    def _preview_intro_cut(self) -> None:
        self._play_interval(
            self._notice_start.value(),
            self._keep_start.value(),
            self._preview_intro_button,
            "Stop intro preview",
        )

    def _preview_outro_cut(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        duration, _, _ = read_track(path)
        self._play_interval(
            self._keep_end.value(),
            self._ending_resume.value() or duration,
            self._preview_outro_button,
            "Stop outro preview",
        )

    def _preview_chapter_ending(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        duration, _, _ = read_track(path)
        keep_start = self._keep_start.value()
        keep_end = self._keep_end.value()
        if not 0 <= keep_start < keep_end <= duration:
            self._status.setText("Choose a valid retained narration range before previewing")
            return
        following_intervals = []
        ending_resume = self._ending_resume.value()
        if ending_resume > 0:
            if not keep_end < ending_resume < duration:
                self._status.setText("Retained ending must follow the chapter outro cut and precede EOF")
                return
            following_intervals.append((ending_resume, duration))
        self._play_interval(
            max(keep_start, keep_end - 5.0),
            keep_end,
            self._preview_ending_button,
            "Stop ending preview",
            following_intervals=following_intervals,
        )

    def _preview_kept_intro(self) -> None:
        notice_start = self._notice_start.value()
        intervals = []
        if notice_start > 0:
            if notice_start >= self._keep_start.value():
                self._status.setText("Notice start must be before resume time")
                return
            intervals.append((0, notice_start))
        intervals.append((self._keep_start.value(), self._keep_end.value()))
        ending_resume = self._ending_resume.value()
        if ending_resume > 0:
            path = self._selected_path()
            if path is None:
                return
            if ending_resume <= self._keep_end.value():
                self._status.setText("Retained ending must follow the chapter outro cut")
                return
            intervals.append((ending_resume, read_track(path)[0]))
        self._play_interval(
            intervals[0][0],
            intervals[0][1],
            self._preview_kept_button,
            "Stop kept-intro preview",
            following_intervals=intervals[1:],
        )

    def _detect_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        settings = self._project_detection
        is_first = bool(self._tracks) and path == self._tracks[0]
        is_last = bool(self._tracks) and path == self._tracks[-1]
        self._start_worker(
            lambda report_progress: analyze_track_boundaries(
                path,
                progress_callback=report_progress,
                settings=settings,
                is_first_chapter=is_first,
                is_last_chapter=is_last,
            ),
            self._detection_finished,
            "Checking the speech model; downloading it if needed...",
            with_progress=True,
        )

    def _detect_all(self) -> None:
        if not self._tracks:
            return
        track_paths = list(self._tracks)
        settings = self._project_detection
        self._bulk_results.clear()
        self._bulk_errors.clear()
        self._start_worker(
            lambda report_progress: analyze_tracks_boundaries(
                track_paths,
                progress_callback=report_progress,
                settings=settings,
            ),
            self._bulk_detection_finished,
            f"Preparing to analyze {len(track_paths)} chapters...",
            with_progress=True,
        )

    def _bulk_detection_finished(self, results: list[TrackBoundaryResult]) -> None:
        for result in results:
            self._apply_bulk_detection_result(result)

        suggestion_count = sum(
            result.detection is not None
            and (result.detection.intro is not None or result.detection.outro is not None)
            and result.path.resolve() not in self._bulk_errors
            for result in results
        )
        no_match_count = sum(
            result.detection is not None
            and result.detection.intro is None
            and result.detection.outro is None
            and result.path.resolve() not in self._bulk_errors
            for result in results
        )
        failures = [
            f"{path.name}: {error}" for path, error in self._bulk_errors.items()
        ]

        selected_path = self._selected_path()
        if selected_path is not None:
            try:
                self._show_review(selected_path, read_track(selected_path)[0])
            except Exception as error:
                failures.append(f"Could not refresh selected chapter: {error}")

        self._save_project_without_dialog()
        summary = (
            f"Detection complete: suggestions for {suggestion_count} of "
            f"{len(results)} chapters; {no_match_count} had no matching phrases; "
            f"{len(failures)} failed. Select chapters to review their keep times."
        )
        if failures:
            summary += " First issue: " + failures[0]
        review_count = sum(
            result.detection is not None
            and bool(result.detection.intro_review_reason or result.detection.outro_review_reason)
            for result in results
        )
        if review_count:
            summary += f" {review_count} chapter(s) need review; hover over marked chapters for details."
        self._status.setText(summary)

    def _apply_detection(self, path: Path, detection) -> bool:
        """Store a chapter's detection result; return False when its suggestions overlap."""
        duration, _, _ = read_track(path)
        review = self._review(path)
        keep_start, keep_end = review.keep_range or (0.0, duration)
        if detection.intro is not None:
            keep_start = detection.intro.keep_time_seconds
        if detection.outro is not None:
            keep_end = detection.outro.keep_time_seconds
        if keep_start >= keep_end:
            return False
        if detection.intro is not None or detection.outro is not None:
            review.keep_start_seconds, review.keep_end_seconds = keep_start, keep_end
        if detection.intro is not None:
            review.intro_removal_start_seconds = detection.intro.removal_start_seconds
        if detection.outro is not None:
            review.outro_resume_start_seconds = detection.outro.resume_after_outro_seconds
        # A run that skipped a section says nothing about that section's earlier warning.
        if self._project_detection.detect_intro:
            review.intro_review_reason = detection.intro_review_reason
        if self._project_detection.detect_outro:
            review.outro_review_reason = detection.outro_review_reason
        self._refresh_track_item(path)
        selected = self._selected_path()
        if selected is not None and selected.resolve() == path.resolve():
            self._show_review(path, duration)
        self._save_project_without_dialog()
        return True

    def _apply_bulk_detection_result(self, result: TrackBoundaryResult) -> None:
        path = result.path.resolve()
        if path in self._bulk_results:
            return
        self._bulk_results[path] = result

        if result.error is not None:
            self._bulk_errors[path] = result.error
            return
        if result.detection is None:
            self._bulk_errors[path] = "no analysis result"
            return
        try:
            applied = self._apply_detection(path, result.detection)
        except Exception as error:
            self._bulk_errors[path] = str(error)
            return
        if not applied:
            self._bulk_errors[path] = "detected boundaries overlap"

    def _detection_finished(self, detection) -> None:
        path = self._selected_path()
        if path is None:
            return
        intro = detection.intro
        outro = detection.outro
        messages: list[str] = []
        if intro is not None:
            messages.append(f"intro {intro.keep_time_seconds:.1f}s ({intro.confidence:.0f}% match)")
            if detection.intro_review_reason:
                messages.append("Review intro: " + detection.intro_review_reason)
            if intro.removal_start_seconds > 0:
                messages.append(f"opening preserved through {intro.removal_start_seconds:.2f}s")
        elif detection.intro_review_reason:
            messages.append("no intro notice matched. Review intro: " + detection.intro_review_reason)
        if outro is not None:
            messages.append(f"outro {outro.keep_time_seconds:.1f}s ({outro.confidence:.0f}% match)")
            if outro.resume_after_outro_seconds is not None:
                messages.append(f"book ending retained from {outro.resume_after_outro_seconds:.2f}s")
            if detection.outro_review_reason:
                messages.append("Review outro: " + detection.outro_review_reason)
        elif detection.outro_review_reason:
            messages.append("no closing marker matched. Review outro: " + detection.outro_review_reason)

        if not self._apply_detection(path, detection):
            QMessageBox.warning(
                self,
                "Overlapping suggestions",
                "The detected intro and outro overlap. Existing boundaries were kept; "
                "adjust them manually or analyze a different chapter.",
            )
            self._status.setText("Suggestions overlap; current boundaries unchanged")
            return

        if messages:
            self._status.setText(
                "Suggested " + " and ".join(messages) + ". Preview before trimming."
            )
        else:
            self._status.setText(
                "No standard intro/outro phrases found; current boundaries were kept."
            )

    def _play_interval(
        self,
        start: float,
        end: float,
        button: QPushButton,
        playing_text: str,
        following_intervals: list[tuple[float, float]] | None = None,
    ) -> None:
        path = self._selected_path()
        if path is None:
            return
        if self._active_preview_button is button and self._pending_preview is not None:
            self._stop_preview()
            return
        if end <= start:
            self._status.setText("That preview interval is empty")
            return
        if self._pending_preview is not None:
            self._stop_preview()
        source_url = QUrl.fromLocalFile(str(path.resolve()))
        source_is_loaded = (
            self._player.source() == source_url
            and self._player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia
        )
        self._pending_preview = None
        self._preview_started = False
        self._playback_timer.stop()
        self._player.stop()
        self._pending_preview = (round(start * 1000), round(end * 1000))
        self._preview_queue = [
            (round(interval_start * 1000), round(interval_end * 1000))
            for interval_start, interval_end in (following_intervals or [])
        ]
        self._active_preview_button = button
        self._active_preview_original_text = button.text()
        button.setText(playing_text)
        button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self._player.setSource(source_url)
        if source_is_loaded:
            self._start_pending_preview()
        preview_duration = end - start + sum(
            interval_end - interval_start
            for interval_start, interval_end in (following_intervals or [])
        )
        self._status.setText(f"Previewing {self._format_time(preview_duration)}")

    def _on_media_status_changed(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._start_pending_preview()

    def _start_pending_preview(self) -> None:
        if self._pending_preview is None or self._preview_started:
            return
        self._preview_started = True
        start_ms, _end_ms = self._pending_preview
        self._player.setPosition(start_ms)
        self._player.play()
        self._playback_timer.start()

    def _on_position_changed(self, position: int) -> None:
        if self._pending_preview is not None and position >= self._pending_preview[1]:
            self._advance_preview()

    def _check_preview_end(self) -> None:
        if self._pending_preview is not None:
            if self._player.position() >= self._pending_preview[1]:
                self._advance_preview()

    def _advance_preview(self) -> None:
        if not self._preview_queue:
            self._stop_preview("Preview finished")
            return
        self._pending_preview = self._preview_queue.pop(0)
        self._player.setPosition(self._pending_preview[0])

    def _stop_preview(self, status_message: str = "Preview stopped") -> None:
        self._pending_preview = None
        self._preview_queue.clear()
        self._preview_started = False
        self._playback_timer.stop()
        self._reset_active_preview_button()
        self._player.stop()
        self._status.setText(status_message)

    def _on_player_error(self, _error, message: str) -> None:
        self._pending_preview = None
        self._preview_queue.clear()
        self._preview_started = False
        self._playback_timer.stop()
        self._reset_active_preview_button()
        self._status.setText(f"Playback error: {message}")

    def _reset_active_preview_button(self) -> None:
        if self._active_preview_button is None:
            return
        self._active_preview_button.setText(self._active_preview_original_text)
        self._active_preview_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._active_preview_button = None
        self._active_preview_original_text = ""

    def _trim_selected(self) -> None:
        source_path = self._selected_path()
        if source_path is None:
            return
        keep_start = self._keep_start.value()
        keep_end = self._keep_end.value()
        notice_start = self._notice_start.value()
        ending_resume = self._ending_resume.value() or None
        if keep_end <= keep_start:
            QMessageBox.warning(self, "Invalid range", "Keep end must be after keep start.")
            return

        if notice_start > 0 and notice_start >= keep_start:
            QMessageBox.warning(self, "Invalid notice range", "Notice start must be before resume time.")
            return
        if ending_resume is not None and not keep_end < ending_resume < self._keep_end.maximum():
            QMessageBox.warning(self, "Invalid ending range", "Retained ending must follow the chapter outro cut.")
            return

        output_directory = QFileDialog.getExistingDirectory(
            self,
            "Choose output folder",
            str(self._settings.output_folder()),
        )
        if not output_directory:
            return
        output_path = Path(output_directory) / f"{source_path.stem}_trimmed.mp3"
        self._start_worker(
            lambda: trim_mp3(
                source_path, output_path, keep_start, keep_end,
                intro_removal_start=notice_start,
                outro_resume_start=ending_resume,
            ),
            self._trim_finished,
            f"Trimming {source_path.name}...",
        )

    def _export_all_chapters(self) -> None:
        if not self._tracks:
            return
        response = QMessageBox.warning(
            self,
            "Export all chapters?",
            "All loaded chapters will be exported using their current cut settings. "
            "Before continuing, verify that you have reviewed and validated every "
            "cut point, including chapters marked for review. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        selected_path = self._selected_path()
        jobs = []
        targets = set()
        for source_path in self._tracks:
            try:
                duration, _, _ = read_track(source_path)
            except Exception as error:
                QMessageBox.warning(self, "Could not export all chapters", f"{source_path.name}: {error}")
                return
            review = self._review(source_path)
            if selected_path is not None and source_path.resolve() == selected_path.resolve():
                keep_start = self._keep_start.value()
                keep_end = self._keep_end.value()
                notice_start = self._notice_start.value()
                ending_resume = self._ending_resume.value() or None
            else:
                keep_start = review.keep_start_seconds if review.keep_start_seconds is not None else 0.0
                keep_end = review.keep_end_seconds if review.keep_end_seconds is not None else duration
                notice_start = review.intro_removal_start_seconds
                ending_resume = review.outro_resume_start_seconds
            if keep_start < 0 or keep_end <= keep_start or keep_end > duration:
                QMessageBox.warning(
                    self, "Invalid cut range", f"Review the keep range for {source_path.name}.",
                )
                return
            if notice_start < 0 or (notice_start > 0 and notice_start >= keep_start):
                QMessageBox.warning(
                    self, "Invalid intro range", f"Review the intro cut for {source_path.name}.",
                )
                return
            if ending_resume is not None and not keep_end < ending_resume < duration:
                QMessageBox.warning(
                    self, "Invalid ending range", f"Review the retained ending for {source_path.name}.",
                )
                return
            output_path = Path("unused") / f"{source_path.stem}_trimmed.mp3"
            if output_path.name.casefold() in targets:
                QMessageBox.warning(
                    self, "Duplicate chapter names",
                    "Two loaded chapters have the same filename and cannot be exported "
                    "to one folder without overwriting each other.",
                )
                return
            targets.add(output_path.name.casefold())
            jobs.append((source_path, keep_start, keep_end, notice_start, ending_resume))

        output_directory = QFileDialog.getExistingDirectory(
            self,
            "Choose output folder",
            str(self._settings.output_folder()),
        )
        if not output_directory:
            return
        output_directory = Path(output_directory)
        jobs = [
            (source, output_directory / f"{source.stem}_trimmed.mp3", start, end, notice, resume)
            for source, start, end, notice, resume in jobs
        ]

        def export_all(report_progress):
            outputs, failures = [], []
            for index, (source, target, start, end, notice, resume) in enumerate(jobs, start=1):
                report_progress(f"Exporting chapter {index} of {len(jobs)}: {source.name}...")
                try:
                    outputs.append(trim_mp3(
                        source, target, start, end,
                        intro_removal_start=notice,
                        outro_resume_start=resume,
                    ))
                except Exception as error:
                    failures.append(f"{source.name}: {error}")
            return outputs, failures

        self._start_worker(
            export_all,
            self._export_all_finished,
            f"Exporting {len(jobs)} chapters...",
            with_progress=True,
        )

    def _export_all_finished(self, result: tuple[list[Path], list[str]]) -> None:
        outputs, failures = result
        if failures:
            message = f"Exported {len(outputs)} chapter(s); {len(failures)} failed."
            QMessageBox.warning(self, "Export completed with issues", message, QMessageBox.StandardButton.Ok)
            self._status.setText(message + " First issue: " + failures[0])
            return
        message = f"Exported {len(outputs)} chapter(s) to {outputs[0].parent}."
        self._status.setText(message)
        QMessageBox.information(self, "Export complete", message)

    def _trim_finished(self, output_path: Path) -> None:
        self._status.setText(f"Saved {output_path}")
        QMessageBox.information(self, "Trim complete", f"Saved trimmed MP3:\n{output_path}")

    @staticmethod
    def _format_time(seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        minutes, remainder = divmod(total_seconds, 60)
        return f"{minutes:02d}:{remainder:02d}"