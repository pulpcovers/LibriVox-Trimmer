from dataclasses import replace

import pytest

from librivox_trimmer.settings import AppSettings, DetectionSettings, load_settings, save_settings


def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings(
        projects_directory=str(tmp_path / "Books"),
        download_quality="smaller",
        auto_analyze_after_download=True,
        detection=DetectionSettings(intro_window_seconds=90, detect_outro=False, narrator_credit_hotwords=True),
    )
    save_settings(settings, path)
    assert load_settings(path) == settings


def test_legacy_settings_default_auto_analysis_to_disabled(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"download_quality":"best"}', encoding="utf-8")

    assert load_settings(path).auto_analyze_after_download is False
    assert load_settings(path).detection.narrator_credit_hotwords is False


@pytest.mark.parametrize("changes", [
    {"intro_window_seconds": 0},
    {"intro_window_seconds": float("nan")},
    {"intro_safety_gap_seconds": -1},
    {"detect_intro": False, "detect_outro": False},
    {"model_size": "unknown"},
    {"narrator_credit_hotwords": "yes"},
])
def test_invalid_detection_settings_rejected(changes):
    with pytest.raises(ValueError):
        replace(DetectionSettings(), **changes).validate()


def test_dialog_save_reads_controls_and_cancel_does_not_modify_settings():
    from PySide6.QtWidgets import QApplication
    from librivox_trimmer.settings_dialog import SettingsDialog

    application = QApplication.instance() or QApplication([])
    original = AppSettings()
    dialog = SettingsDialog(original)
    dialog._intro_window.setValue(90)
    dialog._outro.setChecked(False)
    dialog._auto_analyze.setChecked(True)
    assert not dialog._credit_hotwords.isChecked()
    dialog._credit_hotwords.setChecked(True)
    dialog._quality.setCurrentIndex(1)
    dialog._save()
    assert dialog.result_settings.detection.intro_window_seconds == 90
    assert not dialog.result_settings.detection.detect_outro
    assert dialog.result_settings.download_quality == "smaller"
    assert dialog.result_settings.auto_analyze_after_download
    assert dialog.result_settings.detection.narrator_credit_hotwords
    cancelled = SettingsDialog(original)
    cancelled._intro_window.setValue(120)
    cancelled._auto_analyze.setChecked(True)
    cancelled._credit_hotwords.setChecked(True)
    cancelled.reject()
    assert cancelled.result_settings == original
    dialog._populate(AppSettings())
    assert not dialog._credit_hotwords.isChecked()
    dialog.close()
    cancelled.close()
    application.processEvents()