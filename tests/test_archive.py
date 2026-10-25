import zipfile

import pytest

from librivox_trimmer.archive import extract_mp3s


def test_extract_mp3s_ignores_other_files(tmp_path):
    archive_path = tmp_path / "chapters.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Book/chapter01.mp3", b"mp3 data")
        archive.writestr("Book/cover.jpg", b"image data")

    extracted = extract_mp3s(archive_path, tmp_path / "output")

    assert len(extracted) == 1
    assert extracted[0].read_bytes() == b"mp3 data"


def test_extract_mp3s_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.mp3", b"bad data")

    with pytest.raises(ValueError, match="Unsafe path"):
        extract_mp3s(archive_path, tmp_path / "output")

    assert not (tmp_path / "outside.mp3").exists()