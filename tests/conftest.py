import pytest
from PySide6.QtWidgets import QApplication

from librivox_trimmer import main_window
from librivox_trimmer.main_window import MainWindow
from librivox_trimmer.settings import AppSettings


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """A MainWindow isolated from the user's real settings and projects folder."""
    monkeypatch.setattr(main_window, "projects_dir", lambda: tmp_path / "Projects")
    monkeypatch.setattr(main_window, "load_settings", lambda: AppSettings())
    main = MainWindow()
    yield main
    main.close()
    qapp.processEvents()


@pytest.fixture
def add_chapters(window, tmp_path, monkeypatch):
    """Load chapters of the given duration into the window and return their paths."""

    def add(*names: str, duration: float = 100.0):
        monkeypatch.setattr(main_window, "read_track", lambda path: (duration, path.stem, "Reader"))
        paths = [tmp_path / name for name in names]
        window._add_tracks(paths, persist=False)
        return paths

    return add
