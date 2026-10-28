import subprocess
from array import array

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from librivox_trimmer.audio import build_trim_command, resolve_ffmpeg
from librivox_trimmer.waveform import (
    FrameGrid,
    WaveformWindow,
    column_peaks,
    read_frame_grid,
    read_waveform,
)
from librivox_trimmer.waveform_view import WaveformView


GRID = FrameGrid(origin=0.0021, frame_seconds=576 / 22050)


def test_frame_grid_finds_boundaries_on_either_side():
    boundary = GRID.origin + 300 * GRID.frame_seconds
    assert GRID.boundary_at_or_before(boundary + 0.01) == pytest.approx(boundary)
    assert GRID.boundary_at_or_after(boundary - 0.01) == pytest.approx(boundary)
    assert GRID.boundary_at_or_before(boundary) == pytest.approx(boundary)
    assert GRID.boundary_at_or_after(boundary) == pytest.approx(boundary)
    assert GRID.nearest_boundary(boundary + 0.012) == pytest.approx(boundary)


@pytest.mark.parametrize("starts_kept_audio", [True, False])
@pytest.mark.parametrize("index", [1, 37, 300, 4321])
def test_rounded_cut_value_still_exports_on_the_chosen_boundary(index, starts_kept_audio):
    boundary = GRID.origin + index * GRID.frame_seconds
    value = round(GRID.value_for_cut(boundary, starts_kept_audio=starts_kept_audio), 2)
    assert GRID.exported_cut(value, starts_kept_audio=starts_kept_audio) == pytest.approx(boundary)


def test_column_peaks_report_min_and_max_per_column():
    samples = array("h", [0, 5, -3, 2, 10, -10, 1, 1])
    assert column_peaks(samples, 4) == [(0, 5), (-3, 2), (-10, 10), (1, 1)]
    assert column_peaks(samples, 16)[0] == (0, 0)
    assert column_peaks(array("h"), 4) == []


@pytest.fixture
def tone_mp3(tmp_path):
    path = tmp_path / "tone.mp3"
    subprocess.run(
        [resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=22050",
         "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", str(path)],
        check=True, capture_output=True,
    )
    return path


def _packet_checksums(path, *extra):
    output = subprocess.run(
        [resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", *extra, "-i", str(path),
         "-map", "0:a:0", "-c:a", "copy", "-f", "framecrc", "-"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [line.split(",")[5].strip() for line in output.splitlines() if line and not line.startswith("#")]


def test_frame_grid_and_window_are_read_from_a_real_mp3(tone_mp3):
    grid = read_frame_grid(tone_mp3)
    assert grid is not None
    assert grid.frame_seconds == pytest.approx(576 / 22050)
    assert 0 <= grid.origin < grid.frame_seconds

    window = read_waveform(tone_mp3, 1.0, 2.5)
    assert window.start == 1.0
    assert window.end == pytest.approx(2.5, abs=0.01)
    assert max(window.samples) > 1000


def test_export_keeps_exactly_the_frames_between_snapped_cuts(tmp_path, tone_mp3):
    grid = read_frame_grid(tone_mp3)
    first, last = 40, 80
    start = round(grid.value_for_cut(grid.origin + first * grid.frame_seconds, starts_kept_audio=True), 2)
    end = round(grid.value_for_cut(grid.origin + last * grid.frame_seconds, starts_kept_audio=False), 2)
    output = tmp_path / "cut.mp3"
    subprocess.run(build_trim_command(resolve_ffmpeg(), tone_mp3, output, start, end), check=True, capture_output=True)

    source = _packet_checksums(tone_mp3)
    exported = _packet_checksums(output)
    # Frame numbering follows packet timestamps, which start before zero by the encoder delay.
    offset = next(i for i, crc in enumerate(source) if crc == exported[0]) - first
    assert exported == source[first + offset:last + offset]


def test_waveform_view_maps_clicks_to_snapped_times_and_nudges_by_frames():
    application = QApplication.instance() or QApplication([])
    view = WaveformView()
    view.resize(512, 140)
    samples = array("h", [0] * 8000 * 5)
    view.set_waveform(WaveformWindow(start=10.0, sample_rate=8000, samples=samples), GRID)
    view.show()
    application.processEvents()

    rect = view.plot_rect()
    assert view.time_at(rect.left()) == pytest.approx(10.0)
    assert view.time_at(rect.right()) == pytest.approx(15.0)
    assert view.x_at(view.time_at(rect.center().x())) == pytest.approx(rect.center().x())

    chosen = []
    view.cut_changed.connect(chosen.append)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert chosen[-1] == pytest.approx(GRID.nearest_boundary(view.time_at(rect.center().x())))

    QTest.keyClick(view, Qt.Key.Key_Right)
    assert chosen[-1] - chosen[-2] == pytest.approx(GRID.frame_seconds)
    QTest.keyClick(view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert chosen[-2] - chosen[-1] == pytest.approx(10 * GRID.frame_seconds)
    view.close()
    application.processEvents()
