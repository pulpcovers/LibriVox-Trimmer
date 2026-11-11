from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
    QPushButton, QStyle, QTabWidget, QVBoxLayout, QWidget,
)

from librivox_trimmer.dialogs import QMessageBox
from librivox_trimmer.settings import AppSettings, DetectionSettings, MODEL_CHOICES


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(580, 390)
        self.result_settings = settings
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        storage = QWidget()
        storage_form = QFormLayout(storage)
        self._projects = QLineEdit()
        self._output = QLineEdit()
        storage_form.addRow("New projects folder", self._folder_picker(self._projects))
        storage_form.addRow("Default output folder", self._folder_picker(self._output))
        self._quality = QComboBox()
        self._quality.addItem("Best available (prefer 128 kbps)", "best")
        self._quality.addItem("Smaller download (prefer 64 kbps)", "smaller")
        storage_form.addRow("Download quality", self._quality)
        tabs.addTab(storage, "Storage")

        detection = QWidget()
        detection_form = QFormLayout(detection)
        self._model = QComboBox()
        for model, label in zip(MODEL_CHOICES, (
            "Tiny English (~75 MB, fastest)",
            "Base English (~150 MB, balanced)",
            "Small English (~500 MB, slower)",
            "Medium English (~1.5 GB, slowest)",
        )):
            self._model.addItem(label, model)
        detection_form.addRow("Speech model", self._model)
        self._intro = QCheckBox("Detect intro disclaimers")
        self._outro = QCheckBox("Detect spoken outros")
        self._auto_analyze = QCheckBox("Analyse all chapters automatically after download")
        detection_form.addRow(self._intro)
        detection_form.addRow(self._outro)
        detection_form.addRow(self._auto_analyze)
        tabs.addTab(detection, "Detection")

        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        self._intro_window = self._seconds(5, 600)
        self._outro_window = self._seconds(5, 600)
        self._intro_margin = self._seconds(0, 3, 2)
        self._refinement = QCheckBox("Refine outro start using nearby silence")
        self._refinement.setToolTip(
            "Prefer a cut inside a nearby pause before the spoken outro. "
            "If no suitable preceding pause is found, check for a pause starting near "
            "the detected cut and move to its end. If neither is found, leave the cut unchanged. "
            "Preview the result: silence alone does not guarantee a safe cut."
        )
        advanced_form.addRow("Intro search window", self._intro_window)
        advanced_form.addRow("Outro search window", self._outro_window)
        advanced_form.addRow("Extra intro removal margin", self._intro_margin)
        advanced_form.addRow(self._refinement)
        self._credit_hotwords = QCheckBox("Use narrator-name hints")
        self._credit_hotwords.setToolTip(
            "Experimental: use chapter-specific catalog narrator names as vocabulary hints "
            "for focused reader-credit retries only. "
            "Hints may bias transcription; preview credit boundaries before exporting."
        )
        advanced_form.addRow(self._credit_hotwords)
        tabs.addTab(advanced, "Advanced")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            lambda: self._populate(AppSettings())
        )
        layout.addWidget(buttons)
        self._populate(settings)

    def _folder_picker(self, field: QLineEdit) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field, 1)
        browse = QPushButton()
        browse.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        browse.setToolTip("Choose folder")
        browse.setAccessibleName("Choose folder")
        browse.clicked.connect(lambda: self._browse(field))
        row.addWidget(browse)
        return container

    def _browse(self, field: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose folder", field.text())
        if selected:
            field.setText(selected)

    @staticmethod
    def _seconds(minimum: float, maximum: float, decimals: int = 0) -> QDoubleSpinBox:
        control = QDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(decimals)
        control.setSingleStep(0.1 if decimals else 5)
        control.setSuffix(" s")
        return control

    def _populate(self, settings: AppSettings) -> None:
        self._projects.setText(settings.projects_directory)
        self._projects.setPlaceholderText(str(settings.project_folder()))
        self._output.setText(settings.output_directory)
        self._output.setPlaceholderText(str(settings.output_folder()))
        self._quality.setCurrentIndex(self._quality.findData(settings.download_quality))
        detection = settings.detection
        self._model.setCurrentIndex(self._model.findData(detection.model_size))
        self._intro.setChecked(detection.detect_intro)
        self._outro.setChecked(detection.detect_outro)
        self._auto_analyze.setChecked(settings.auto_analyze_after_download)
        self._intro_window.setValue(detection.intro_window_seconds)
        self._outro_window.setValue(detection.outro_window_seconds)
        self._intro_margin.setValue(detection.intro_safety_gap_seconds)
        self._refinement.setChecked(detection.refine_outro)
        self._credit_hotwords.setChecked(detection.narrator_credit_hotwords)

    def _save(self) -> None:
        settings = AppSettings(
            projects_directory=self._projects.text().strip(),
            output_directory=self._output.text().strip(),
            download_quality=self._quality.currentData(),
            auto_analyze_after_download=self._auto_analyze.isChecked(),
            detection=DetectionSettings(
                model_size=self._model.currentData(),
                detect_intro=self._intro.isChecked(),
                detect_outro=self._outro.isChecked(),
                intro_window_seconds=self._intro_window.value(),
                outro_window_seconds=self._outro_window.value(),
                intro_safety_gap_seconds=self._intro_margin.value(),
                refine_outro=self._refinement.isChecked(),
                narrator_credit_hotwords=self._credit_hotwords.isChecked(),
            ),
        )
        try:
            settings.validate()
            for folder in (settings.project_folder(), settings.output_folder()):
                if folder.exists() and not folder.is_dir():
                    raise ValueError(f"Not a folder: {folder}")
        except ValueError as error:
            QMessageBox.warning(self, "Invalid settings", str(error))
            return
        self.result_settings = settings
        self.accept()