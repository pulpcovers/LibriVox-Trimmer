import pytest
from array import array

from PySide6.QtWidgets import QApplication

from librivox_trimmer import main_window
from librivox_trimmer.main_window import MainWindow
from librivox_trimmer.settings import AppSettings
from librivox_trimmer.waveform import WAVEFORM_SAMPLE_RATE, WaveformWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_analysis_cache(tmp_path_factory, monkeypatch):
    """Keep tests from reading or writing the user's real analysis cache."""
    monkeypatch.setenv("LIBRIVOX_ANALYSIS_CACHE_DIR", str(tmp_path_factory.mktemp("analysis-cache")))


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """A MainWindow isolated from the user's real settings and projects folder."""
    monkeypatch.setattr(main_window, "projects_dir", lambda: tmp_path / "Projects")
    monkeypatch.setattr(main_window, "load_settings", lambda: AppSettings())
    # Test chapters are placeholder files; decoding them with ffmpeg in a worker thread only adds noise and races.
    monkeypatch.setattr(main_window, "read_frame_grid", lambda _path: None)
    monkeypatch.setattr(
        main_window, "read_waveform",
        lambda _path, start, end: WaveformWindow(start, WAVEFORM_SAMPLE_RATE, array("h", [0]) * int((end - start) * WAVEFORM_SAMPLE_RATE)),
    )
    main = MainWindow()
    yield main
    # Waveform loads run in the thread pool; let them finish before Qt objects are torn down.
    main._thread_pool.waitForDone(10000)
    qapp.processEvents()
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
